"""WHO ICD-11 coding of diagnosis names (https://icd.who.int/icdapi).

OAuth2 client-credentials token (cached until shortly before expiry), then the MMS
`search` endpoint on the configured release. WHO's `autocode` endpoint returned HTTP 500
for release 2026-01 (Oct 2026), so it is only used as a fallback on the older release.
Every failure returns None: a missing code must never break a diagnosis.
"""

import asyncio
import logging
import re
import time
from functools import lru_cache

import httpx
from pydantic import BaseModel

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://icdaccessmanagement.who.int/connect/token"
API_URL = "https://id.who.int"
MIN_SCORE = 0.5

# WHO search can rank a more specific entity first ("Dengue fever" -> "Severe dengue",
# 0.90 vs "Dengue, unspecified" 0.84). Coding rule: if the diagnosis does not state a
# qualifier, don't pick an entity that adds one.
QUALIFIERS = {"severe", "chronic", "recurrent", "complicated", "with", "without", "other"}
QUALIFIER_PENALTY = 0.2


def _added_qualifiers(term: str, title: str) -> set[str]:
    words = lambda text: set(re.findall(r"[a-z]+", text.lower()))  # noqa: E731
    return (words(title) & QUALIFIERS) - words(term)


class ICDMatch(BaseModel):
    code: str
    title: str


class ICD11Client:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._http = httpx.AsyncClient(timeout=10, transport=transport)
        self._token: str | None = None
        self._token_expiry = 0.0
        self._token_lock = asyncio.Lock()
        self._cache: dict[str, ICDMatch | None] = {}

    async def _headers(self) -> dict[str, str]:
        async with self._token_lock:
            if not self._token or time.monotonic() > self._token_expiry:
                resp = await self._http.post(
                    TOKEN_URL,
                    data={
                        "client_id": self._settings.icd_client_id,
                        "client_secret": self._settings.icd_client_secret,
                        "scope": "icdapi_access",
                        "grant_type": "client_credentials",
                    },
                )
                resp.raise_for_status()
                body = resp.json()
                self._token = body["access_token"]
                self._token_expiry = time.monotonic() + body.get("expires_in", 3600) - 60
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Accept-Language": "en",
            "API-Version": "v2",
        }

    async def _search(self, term: str) -> ICDMatch | None:
        resp = await self._http.get(
            f"{API_URL}/icd/release/11/{self._settings.icd_release}/mms/search",
            params={"q": term, "flatResults": "true"},
            headers=await self._headers(),
        )
        resp.raise_for_status()
        best: tuple[float, ICDMatch] | None = None
        for entity in resp.json().get("destinationEntities") or []:
            # Chapters and blocks have no code. Postcoordinated clusters ("A/B") carry the
            # first stem's title, e.g. "Otitis media" for measles encephalitis — skip them.
            code = entity.get("theCode") or ""
            if not code or "/" in code or entity.get("score", 0) < MIN_SCORE:
                continue
            title = re.sub(r"<[^>]+>", "", entity.get("title", ""))
            score = entity["score"] - QUALIFIER_PENALTY * len(_added_qualifiers(term, title))
            if best is None or score > best[0]:
                best = (score, ICDMatch(code=entity["theCode"], title=title))
        return best[1] if best else None

    async def _autocode(self, term: str) -> ICDMatch | None:
        resp = await self._http.get(
            f"{API_URL}/icd/release/11/{self._settings.icd_fallback_release}/mms/autocode",
            params={"searchText": term},
            headers=await self._headers(),
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("theCode") and body.get("matchScore", 0) >= MIN_SCORE:
            return ICDMatch(code=body["theCode"], title=body.get("matchingText") or term)
        return None

    async def code(self, term: str) -> ICDMatch | None:
        key = term.strip().lower()
        if key in self._cache:
            return self._cache[key]
        match = None
        for lookup in (self._search, self._autocode):
            try:
                # WHO search is brittle with extra modifiers ("Acute bacterial meningitis"
                # finds nothing, "Bacterial meningitis" scores 1.0): simplify step by step.
                for variant in search_variants(term):
                    if match := await lookup(variant):
                        break
                break
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                logger.warning("ICD-11 %s failed for %r: %s", lookup.__name__, term, exc)
        else:
            return None  # every lookup errored: don't cache, retry next time
        self._cache[key] = match
        return match


def search_variants(term: str) -> list[str]:
    """The term, without parentheticals, then with leading words dropped (>= 2 words left)."""
    variants = [term.strip()]
    words = re.sub(r"\s*\([^)]*\)", "", term).replace(",", " ").split()
    while len(words) >= 2:
        variants.append(" ".join(words))
        words = words[1:]
    return list(dict.fromkeys(variants))


@lru_cache
def get_icd() -> ICD11Client | None:
    settings = get_settings()
    if not (settings.icd_client_id and settings.icd_client_secret):
        return None
    return ICD11Client(settings)
