"""Map free-text medication entries to DDInter drug names.

Resolution order, first hit wins:
  1. exact     — cleaned name (dose/form/frequency stripped) is a DDInter drug
  2. synonym   — an equivalent name (INN vs USAN, e.g. aspirin -> acetylsalicylic acid)
  3. rxnorm    — NLM RxNav approximate match -> ingredient(s); handles brands
                 (Coumadin -> warfarin), combination products and typos
  4. fuzzy     — offline typo tolerance against DDInter names
"""

import asyncio
import logging
import re

import httpx
from rapidfuzz import fuzz, process

from app.config import Settings
from app.schemas import MedicationMatch
from app.services.drug_graph import DrugGraph

logger = logging.getLogger(__name__)

# Groups of equivalent names; resolution picks whichever member DDInter uses
# (DDInter mixes conventions: "acetaminophen" but "salbutamol", "rifampicin").
SYNONYM_GROUPS: list[set[str]] = [
    {"aspirin", "acetylsalicylic acid", "asa"},
    {"paracetamol", "acetaminophen", "apap"},
    {"salbutamol", "albuterol"},
    {"adrenaline", "epinephrine"},
    {"noradrenaline", "norepinephrine"},
    {"frusemide", "furosemide"},
    {"glibenclamide", "glyburide"},
    {"lignocaine", "lidocaine"},
    {"pethidine", "meperidine"},
    {"amoxycillin", "amoxicillin"},
    {"cefalexin", "cephalexin"},
    {"ciclosporin", "cyclosporine"},
    {"rifampicin", "rifampin"},
    {"levothyroxine", "thyroxine", "l-thyroxine"},
    {"valproic acid", "valproate", "sodium valproate"},
    {"co-trimoxazole", "sulfamethoxazole"},
    {"bendroflumethiazide", "bendrofluazide"},
]
_SYNONYMS = {name: group for group in SYNONYM_GROUPS for name in group}

# Everything from the first dose-like token onwards is dropped ("metoprolol 50mg bid").
_DOSE = re.compile(r"\s+\d[\d.,/]*\s*(mg|mcg|µg|ug|g|ml|units?|iu|%|meq|mmol)?\b.*$", re.I)
_NOISE_WORDS = re.compile(
    r"\b(tablets?|tabs?|capsules?|caps?|oral|po|iv|im|sc|er|sr|xl|xr|cr|la|"
    r"od|bd|bid|tid|tds|qid|qds|prn|daily|nocte|mane|once|twice)\b",
    re.I,
)


def clean_name(raw: str) -> str:
    name = _DOSE.sub("", raw.strip().lower())
    name = _NOISE_WORDS.sub(" ", name)
    return re.sub(r"\s+", " ", name).strip(" ,.-")


class RxNormClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._min_score = settings.rxnorm_min_score
        self._http = httpx.AsyncClient(
            base_url=settings.rxnorm_base_url,
            timeout=settings.rxnorm_timeout_seconds,
            transport=transport,
        )
        self._cache: dict[str, list[str]] = {}

    async def ingredients(self, term: str) -> list[str]:
        """Ingredient names for the best approximate match, or [] if none is confident."""
        if term in self._cache:
            return self._cache[term]
        try:
            resp = await self._http.get(
                "/approximateTerm.json", params={"term": term, "maxEntries": 1}
            )
            resp.raise_for_status()
            candidates = resp.json().get("approximateGroup", {}).get("candidate") or []
            best = candidates[0] if candidates else None
            if not best or float(best.get("score", 0)) < self._min_score:
                result: list[str] = []
            else:
                related = await self._http.get(
                    f"/rxcui/{best['rxcui']}/related.json", params={"tty": "IN"}
                )
                related.raise_for_status()
                groups = related.json().get("relatedGroup", {}).get("conceptGroup") or []
                result = sorted(
                    {
                        prop["name"].lower()
                        for group in groups
                        for prop in group.get("conceptProperties") or []
                    }
                )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("RxNorm lookup failed for %r: %s", term, exc)
            return []  # not cached, so a transient failure is retried next time
        self._cache[term] = result
        return result


class DrugNameResolver:
    def __init__(self, graph: DrugGraph, rxnorm: RxNormClient | None) -> None:
        self._graph = graph
        self._rxnorm = rxnorm
        self._names = graph.drug_names

    def _local(self, name: str) -> tuple[str, str] | None:
        if name in self._graph:
            return name, "exact"
        for alias in sorted(_SYNONYMS.get(name, ())):
            if alias in self._graph:
                return alias, "synonym"
        return None

    async def resolve(self, raw: str) -> MedicationMatch:
        name = clean_name(raw)
        if not name:
            return MedicationMatch(input=raw, resolved=[], method=None)
        if hit := self._local(name):
            return MedicationMatch(input=raw, resolved=[hit[0]], method=hit[1])

        if self._rxnorm:
            ingredients = await self._rxnorm.ingredients(name)
            resolved = [hit[0] for i in ingredients if (hit := self._local(i))]
            if resolved:
                return MedicationMatch(input=raw, resolved=resolved, method="rxnorm")

        match = process.extractOne(name, self._names, scorer=fuzz.ratio, score_cutoff=88)
        if match:
            return MedicationMatch(input=raw, resolved=[match[0]], method="fuzzy")
        return MedicationMatch(input=raw, resolved=[], method=None)

    async def resolve_all(self, raws: list[str]) -> list[MedicationMatch]:
        return list(await asyncio.gather(*(self.resolve(r) for r in raws)))
