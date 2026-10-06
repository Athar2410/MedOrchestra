"""Build the PubMed retrieval corpus in Supabase.

Stages (each resumable; state lives in backend/data/pubmed/, which is gitignored):
  1. select  — esearch per topic (pipelines.pubmed_topics) -> topics.json
  2. fetch   — efetch abstracts for the first --limit PMIDs (round-robin across topics)
               -> articles.jsonl
  3. embed   — MedCPT article embeddings, upserted into pubmed_chunks; rows already
               embedded in the database are skipped

    .\\.venv\\Scripts\\python -m pipelines.ingest_pubmed --limit 5000     # dev sample
    .\\.venv\\Scripts\\python -m pipelines.ingest_pubmed --limit 50000    # full corpus

Run `python -m pipelines.migrate` first. Embedding is CPU-bound (~7 abstracts/s on the
dev laptop); keep the machine plugged in. Interrupting and re-running resumes.
"""

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from itertools import zip_longest

import httpx
import psycopg

from app.config import BACKEND_DIR, get_settings
from pipelines.pubmed_topics import TOPICS

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DATA = BACKEND_DIR / "data" / "pubmed"
TOPICS_FILE = DATA / "topics.json"
ARTICLES_FILE = DATA / "articles.jsonl"

MIN_ABSTRACT_CHARS = 200
EXCLUDED_TYPES = (
    "letter[pt] OR comment[pt] OR editorial[pt] OR erratum[pt] OR "
    '"retracted publication"[pt] OR news[pt]'
)


class NCBI:
    """E-utilities client that respects the rate limit (10 req/s with a key, else 3)."""

    def __init__(self, api_key: str) -> None:
        self._params = {"tool": "medorchestra", **({"api_key": api_key} if api_key else {})}
        self._interval = 0.11 if api_key else 0.34
        self._last = 0.0
        self._http = httpx.Client(base_url=EUTILS, timeout=60)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(5):
            wait = self._interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = self._http.request(method, path, **kwargs)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                return resp
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == 4:
                    raise
                print(f"  retry {path} ({exc})")
                time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def search(self, term: str, retmax: int) -> list[int]:
        params = {**self._params, "db": "pubmed", "term": term, "retmax": retmax,
                  "sort": "relevance", "retmode": "json"}  # fmt: skip
        resp = self._request("GET", "/esearch.fcgi", params=params)
        return [int(p) for p in resp.json()["esearchresult"]["idlist"]]

    def fetch(self, pmids: list[int]) -> str:
        data = {**self._params, "db": "pubmed", "id": ",".join(map(str, pmids)),
                "retmode": "xml", "rettype": "abstract"}  # fmt: skip
        return self._request("POST", "/efetch.fcgi", data=data).text


def topic_query(topic: str) -> str:
    return (
        f'("{topic}"[MeSH Major Topic] OR "{topic}"[Title]) AND hasabstract[text] '
        f"AND english[lang] AND 2000:3000[dp] NOT ({EXCLUDED_TYPES})"
    )


def stage_select(ncbi: NCBI, per_topic: int) -> dict[str, list[int]]:
    selected: dict[str, list[int]] = (
        json.loads(TOPICS_FILE.read_text()) if TOPICS_FILE.exists() else {}
    )
    todo = [t for t in TOPICS if t not in selected]
    for i, topic in enumerate(todo, 1):
        selected[topic] = ncbi.search(topic_query(topic), per_topic)
        print(f"[select {i}/{len(todo)}] {topic}: {len(selected[topic])} PMIDs")
        TOPICS_FILE.write_text(json.dumps(selected))
    thin = [t for t in TOPICS if len(selected.get(t, [])) < 50]
    if thin:
        print(f"warning: few results for {thin}")
    return selected


def round_robin(selected: dict[str, list[int]], limit: int) -> list[tuple[int, str]]:
    """Interleave topics by relevance rank, dedupe, stop at limit."""
    order: list[tuple[int, str]] = []
    seen: set[int] = set()
    columns = [[(pmid, topic) for pmid in selected.get(topic, [])] for topic in TOPICS]
    for row in zip_longest(*columns):
        for item in row:
            if item and item[0] not in seen:
                seen.add(item[0])
                order.append(item)
                if len(order) == limit:
                    return order
    return order


def _text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def parse_articles(xml: str) -> list[dict]:
    articles = []
    for art in ET.fromstring(xml).iter("PubmedArticle"):
        citation = art.find("MedlineCitation")
        article = citation.find("Article") if citation is not None else None
        if article is None:
            continue
        sections = []
        for part in article.findall("Abstract/AbstractText"):
            text = _text(part)
            label = part.get("Label")
            if text:
                sections.append(f"{label.capitalize()}: {text}" if label else text)
        abstract = " ".join(sections)
        title = _text(article.find("ArticleTitle"))
        if len(abstract) < MIN_ABSTRACT_CHARS or not title:
            continue
        year_text = _text(article.find("Journal/JournalIssue/PubDate/Year")) or _text(
            article.find("Journal/JournalIssue/PubDate/MedlineDate")
        )
        articles.append(
            {
                "pmid": int(_text(citation.find("PMID"))),
                "title": title,
                "abstract": abstract,
                "journal": _text(article.find("Journal/Title")) or None,
                "pub_year": int(year_text[:4]) if year_text[:4].isdigit() else None,
                "mesh_terms": [
                    _text(d) for d in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")
                ],
            }
        )
    return articles


def load_articles() -> dict[int, dict]:
    if not ARTICLES_FILE.exists():
        return {}
    with ARTICLES_FILE.open(encoding="utf-8") as f:
        return {a["pmid"]: a for a in map(json.loads, f)}


def stage_fetch(ncbi: NCBI, wanted: list[tuple[int, str]], batch: int = 200) -> None:
    have = load_articles()
    # PMIDs fetched before but dropped (no abstract) are remembered so they are not refetched.
    skipped_file = DATA / "skipped.json"
    skipped = set(json.loads(skipped_file.read_text())) if skipped_file.exists() else set()
    todo = [pmid for pmid, _ in wanted if pmid not in have and pmid not in skipped]
    with ARTICLES_FILE.open("a", encoding="utf-8") as out:
        for i in range(0, len(todo), batch):
            chunk = todo[i : i + batch]
            parsed = parse_articles(ncbi.fetch(chunk))
            for a in parsed:
                out.write(json.dumps(a) + "\n")
            skipped |= set(chunk) - {a["pmid"] for a in parsed}
            skipped_file.write_text(json.dumps(sorted(skipped)))
            print(f"[fetch] {min(i + batch, len(todo))}/{len(todo)} PMIDs")


UPSERT = """
insert into pubmed_chunks (pmid, title, abstract, journal, pub_year, mesh_terms, topic, embedding)
values (%s, %s, %s, %s, %s, %s, %s, %s::halfvec)
on conflict (pmid) do update set
    title = excluded.title, abstract = excluded.abstract, journal = excluded.journal,
    pub_year = excluded.pub_year, mesh_terms = excluded.mesh_terms, topic = excluded.topic,
    embedding = excluded.embedding
"""


def stage_embed(wanted: list[tuple[int, str]], database_url: str, batch: int = 32) -> None:
    from app.services.embeddings import get_medcpt, to_halfvec_literal

    articles = load_articles()
    with psycopg.connect(database_url) as conn:
        done = {
            r[0] for r in conn.execute("select pmid from pubmed_chunks where embedding is not null")
        }
        todo = [(articles[p], t) for p, t in wanted if p in articles and p not in done]
        # Batches are padded to their longest abstract (median ~210 tokens, p90 ~380), so
        # sort by length within windows: similar-length batches roughly halve the compute,
        # and the windows keep a partial run spread across all topics.
        window = 1024
        todo = [
            item
            for i in range(0, len(todo), window)
            for item in sorted(
                todo[i : i + window], key=lambda x: len(x[0]["title"]) + len(x[0]["abstract"])
            )
        ]
        print(f"[embed] {len(done)} already in database, {len(todo)} to embed")
        medcpt = get_medcpt()
        started = time.monotonic()
        for i in range(0, len(todo), batch):
            chunk = todo[i : i + batch]
            vectors = medcpt.encode_articles([(a["title"], a["abstract"]) for a, _ in chunk])
            rows = [
                (
                    a["pmid"],
                    a["title"],
                    a["abstract"],
                    a["journal"],
                    a["pub_year"],
                    a["mesh_terms"],
                    topic,
                    to_halfvec_literal(v),
                )  # fmt: skip
                for (a, topic), v in zip(chunk, vectors, strict=True)
            ]
            # The Supabase pooler occasionally drops long-lived connections; reconnect and
            # retry the batch rather than losing a multi-hour run.
            for attempt in range(5):
                try:
                    with conn.cursor() as cur:
                        cur.executemany(UPSERT, rows)
                    conn.commit()
                    break
                except psycopg.OperationalError as exc:
                    if attempt == 4:
                        raise
                    print(f"  database connection lost ({exc}); reconnecting", flush=True)
                    time.sleep(5 * (attempt + 1))
                    conn = psycopg.connect(database_url)
            n = min(i + batch, len(todo))
            rate = n / (time.monotonic() - started)
            eta = (len(todo) - n) / rate / 60
            print(f"[embed] {n}/{len(todo)}  {rate:.1f}/s  ETA {eta:.0f} min", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the PubMed corpus in Supabase")
    parser.add_argument("--limit", type=int, default=5000, help="total abstracts in the corpus")
    parser.add_argument("--per-topic", type=int, default=600, help="PMIDs selected per topic")
    parser.add_argument("--stage", choices=["all", "select", "fetch", "embed"], default="all")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL is not set (backend/.env)")
        return 1
    DATA.mkdir(parents=True, exist_ok=True)
    ncbi = NCBI(settings.ncbi_api_key)

    selected = (
        stage_select(ncbi, args.per_topic)
        if args.stage in ("all", "select")
        else (json.loads(TOPICS_FILE.read_text()))
    )
    # Over-select so abstracts dropped while fetching (none/too short) don't leave a shortfall.
    wanted = round_robin(selected, int(args.limit * 1.15))
    if args.stage in ("all", "fetch"):
        stage_fetch(ncbi, wanted)
    if args.stage in ("all", "embed"):
        have = load_articles()
        wanted = [(p, t) for p, t in wanted if p in have][: args.limit]
        stage_embed(wanted, settings.database_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
