import httpx
import pytest

from app.config import get_settings
from app.services.drug_graph import get_drug_graph
from app.services.drug_names import DrugNameResolver, RxNormClient, clean_name


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Metoprolol 50mg BID", "metoprolol"),
        ("aspirin 75 mg od", "aspirin"),
        ("Salbutamol inhaler 100mcg prn", "salbutamol inhaler"),
        ("Warfarin tablets", "warfarin"),
        ("  Acetylsalicylic Acid  ", "acetylsalicylic acid"),
    ],
)
def test_clean_name(raw, clean):
    assert clean_name(raw) == clean


def rxnorm_transport(calls: list[str]) -> httpx.MockTransport:
    approx = {
        "coumadin": ("202421", "13.6"),
        "percocet": ("42844", "13.7"),
        "gibberish": ("1", "4.5"),
    }
    ingredients = {"202421": ["warfarin"], "42844": ["acetaminophen", "oxycodone"]}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/approximateTerm.json"):
            rxcui, score = approx.get(request.url.params["term"], (None, None))
            candidate = [{"rxcui": rxcui, "score": score}] if rxcui else []
            return httpx.Response(200, json={"approximateGroup": {"candidate": candidate}})
        rxcui = request.url.path.split("/")[-2]
        props = [{"name": n, "tty": "IN"} for n in ingredients[rxcui]]
        return httpx.Response(
            200,
            json={"relatedGroup": {"conceptGroup": [{"tty": "IN", "conceptProperties": props}]}},
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def resolver():
    calls: list[str] = []
    rxnorm = RxNormClient(get_settings(), transport=rxnorm_transport(calls))
    r = DrugNameResolver(get_drug_graph(), rxnorm)
    r.calls = calls
    return r


async def test_exact_and_dose_stripped(resolver):
    match = await resolver.resolve("Warfarin 5mg daily")
    assert (match.resolved, match.method) == (["warfarin"], "exact")
    assert resolver.calls == []  # local hit never calls RxNorm


async def test_synonym_maps_to_ddinter_naming(resolver):
    assert (await resolver.resolve("Aspirin")).resolved == ["acetylsalicylic acid"]
    assert (await resolver.resolve("paracetamol")).resolved == ["acetaminophen"]
    # DDInter uses the INN here, so the US name must map the other way.
    assert (await resolver.resolve("albuterol")).resolved == ["salbutamol"]


async def test_brand_and_combination_via_rxnorm(resolver):
    coumadin = await resolver.resolve("Coumadin")
    assert (coumadin.resolved, coumadin.method) == (["warfarin"], "rxnorm")
    percocet = await resolver.resolve("Percocet")
    assert percocet.resolved == ["acetaminophen", "oxycodone"]


async def test_low_score_rxnorm_match_is_rejected(resolver):
    match = await resolver.resolve("gibberish")
    assert (match.resolved, match.method) == ([], None)


async def test_fuzzy_typo_without_rxnorm():
    offline = DrugNameResolver(get_drug_graph(), rxnorm=None)
    match = await offline.resolve("simvastatn")
    assert (match.resolved, match.method) == (["simvastatin"], "fuzzy")
