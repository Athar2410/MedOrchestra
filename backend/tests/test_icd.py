import httpx

from app.config import get_settings
from app.services.icd import ICD11Client, search_variants


def transport(search_results: dict, calls: list[str], autocode=None, search_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if "connect/token" in str(request.url):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.path.endswith("/search"):
            if search_status != 200:
                return httpx.Response(search_status)
            q = request.url.params["q"]
            return httpx.Response(200, json={"destinationEntities": search_results.get(q, [])})
        if request.url.path.endswith("/autocode"):
            return httpx.Response(200, json=autocode or {})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def client(search_results, calls, **kw) -> ICD11Client:
    settings = get_settings().model_copy(update={"icd_client_id": "id", "icd_client_secret": "s"})
    return ICD11Client(settings, transport=transport(search_results, calls, **kw))


DENGUE = [
    {"theCode": "1D22", "title": "Severe dengue", "score": 0.898},
    {"theCode": "1D2Z", "title": "Dengue, unspecified", "score": 0.838},
    {"theCode": None, "title": "Chapter", "score": 0.9},
]


async def test_unqualified_term_prefers_unspecified_code():
    calls: list[str] = []
    match = await client({"Dengue fever": DENGUE, "Severe dengue": DENGUE}, calls).code(
        "Dengue fever"
    )
    assert (match.code, match.title) == ("1D2Z", "Dengue, unspecified")


async def test_stated_qualifier_keeps_specific_code():
    match = await client({"Severe dengue": DENGUE}, []).code("Severe dengue")
    assert match.code == "1D22"


async def test_results_are_cached_and_token_reused():
    calls: list[str] = []
    c = client({"Dengue fever": DENGUE}, calls)
    await c.code("Dengue fever")
    await c.code("dengue fever ")
    assert sum(p.endswith("/search") for p in calls) == 1
    assert sum("token" in p for p in calls) == 1


async def test_search_error_falls_back_to_autocode_on_older_release():
    calls: list[str] = []
    c = client(
        {}, calls, search_status=500,
        autocode={"theCode": "BA4Z", "matchingText": "acute coronary syndrome", "matchScore": 1},
    )  # fmt: skip
    match = await c.code("Acute coronary syndrome")
    assert match.code == "BA4Z"
    assert any("/2025-01/mms/autocode" in p for p in calls)


async def test_no_confident_match_returns_none():
    weak = {"Fatigue": [{"theCode": "MG22", "title": "Fatigue", "score": 0.3}]}
    assert await client(weak, []).code("Fatigue") is None


def test_search_variants_simplify_progressively():
    assert search_variants("Acute viral meningitis (e.g., enterovirus)") == [
        "Acute viral meningitis (e.g., enterovirus)",
        "Acute viral meningitis",
        "viral meningitis",
    ]


async def test_modifier_is_dropped_when_full_term_finds_nothing():
    calls: list[str] = []
    results = {
        "Bacterial meningitis": [
            {"theCode": "1D01.0Z", "title": "Bacterial meningitis, unspecified", "score": 1.0}
        ]
    }
    results["bacterial meningitis"] = results["Bacterial meningitis"]
    match = await client(results, calls).code("Acute bacterial meningitis")
    assert match.code == "1D01.0Z"


async def test_postcoordinated_clusters_are_skipped():
    results = {
        "Measles": [
            {"theCode": "AB0Z/1F03.1", "title": "Otitis media, unspecified", "score": 1.0},
            {"theCode": "1F03", "title": "Measles", "score": 0.95},
        ]
    }
    assert (await client(results, []).code("Measles")).code == "1F03"
