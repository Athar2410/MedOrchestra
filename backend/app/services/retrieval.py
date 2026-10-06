"""PubMed evidence retrieval: MedCPT query encoding -> Supabase hybrid search -> rerank.

Pipeline per call (all queries at once):
  1. encode every query with the MedCPT query encoder
  2. `hybrid_search` (pgvector + full-text, RRF-fused; migrations/001_pubmed.sql) per query
  3. pool candidates round-robin by query rank, capped at `rerank_pool` (cross-encoder
     cost on CPU is ~0.15 s/doc), and score each against the query that found it
  4. keep `evidence_k` abstracts, round-robin across queries so every hypothesis gets
     evidence instead of the most-studied condition taking every slot

Database access is synchronous (psycopg async does not support the Windows Proactor
event loop) and runs with the model inference in a worker thread.
"""

import asyncio
import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
from itertools import zip_longest
from typing import Any, Protocol

import psycopg

from app.config import Settings, get_settings
from app.schemas import Evidence

logger = logging.getLogger(__name__)


class Retriever(Protocol):
    async def search(self, queries: list[str]) -> list[Evidence]: ...

    async def score(self, pairs: list[tuple[str, str]]) -> list[float]: ...


# The Diagnostician sends the case query plus up to 5 hypothesis queries.
MAX_PARALLEL_QUERIES = 6
STATEMENT_TIMEOUT = "3s"


def _configure_connection(conn: Any) -> None:
    conn.execute(f"set statement_timeout = '{STATEMENT_TIMEOUT}'")


SEARCH_SQL = """
select pmid, title, abstract, journal, pub_year
from hybrid_search(%s, %s::halfvec, %s)
"""


Row = tuple[Any, ...]  # (pmid, title, abstract, journal, pub_year)


def pool_candidates(per_query: list[list[tuple[str, Row]]], cap: int) -> list[tuple[str, Row]]:
    """Interleave each query's hits by rank; the first query to find a PMID owns it."""
    pool: list[tuple[str, Row]] = []
    seen: set[int] = set()
    for rank_row in zip_longest(*per_query):
        for item in rank_row:
            if item and item[1][0] not in seen:
                seen.add(item[1][0])
                pool.append(item)
    return pool[:cap]


def select_evidence(
    queries: list[str],
    pool: list[tuple[str, Row]],
    scores: list[float],
    k: int,
    min_score: float = float("-inf"),
) -> list[Evidence]:
    """Best-scored abstracts per query, taken round-robin across queries, up to k.

    Abstracts the cross-encoder scores below `min_score` are dropped, so a query that
    found nothing relevant gives up its slots instead of filling them with noise.
    """
    by_query: dict[str, list[Evidence]] = {q: [] for q in queries}
    for (query, (pmid, title, abstract, journal, year)), score in zip(pool, scores, strict=True):
        if score < min_score:
            continue
        by_query[query].append(
            Evidence(
                pmid=pmid,
                title=title,
                abstract=abstract,
                journal=journal,
                pub_year=year,
                query=query,
                score=float(score),
            )  # fmt: skip
        )
    for hits in by_query.values():
        hits.sort(key=lambda e: e.score, reverse=True)
    selected: list[Evidence] = []
    for rank_row in zip_longest(*by_query.values()):
        selected.extend(e for e in rank_row if e)
    return selected[:k]


class PubMedRetriever:
    def __init__(self, settings: Settings) -> None:
        from psycopg_pool import ConnectionPool

        from app.services.embeddings import get_medcpt

        self._settings = settings
        self._medcpt = get_medcpt()
        self._pool = ConnectionPool(
            settings.database_url,
            min_size=2,
            max_size=MAX_PARALLEL_QUERIES,
            open=False,
            kwargs={"autocommit": True},
            configure=_configure_connection,
        )

    def close(self) -> None:
        self._pool.close()

    def warm_up(self) -> None:
        self._pool.open(wait=True, timeout=20)
        self._medcpt.warm_up(("query", "cross"))

    async def search(self, queries: list[str]) -> list[Evidence]:
        return await asyncio.to_thread(self._search_sync, queries)

    async def score(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Cross-encoder relevance of (query, document) pairs."""
        return (await asyncio.to_thread(self._medcpt.rerank, pairs)).tolist()

    def _search_sync(self, queries: list[str]) -> list[Evidence]:
        from app.services.embeddings import to_halfvec_literal

        queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))
        if not queries:
            return []
        self._pool.open(wait=True, timeout=20)
        vectors = self._medcpt.encode_queries(queries)

        def run(query: str, vector: Any) -> list[tuple[str, Row]]:
            # One query timing out (statement_timeout) costs only its own hits.
            try:
                with self._pool.connection() as conn:
                    rows = conn.execute(
                        SEARCH_SQL,
                        (query, to_halfvec_literal(vector), self._settings.retrieval_per_query),
                    ).fetchall()
            except psycopg.Error as exc:
                logger.warning("hybrid_search failed for %r: %s", query, exc)
                return []
            return [(query, row) for row in rows]

        # Queries run in parallel: on a cold free-tier cache each takes 0.2-1.7 s.
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_QUERIES) as executor:
            per_query = list(executor.map(run, queries, vectors))

        pool = pool_candidates(per_query, self._settings.rerank_pool)
        if not pool:
            return []
        scores = self._medcpt.rerank([(q, f"{row[1]}. {row[2]}") for q, row in pool])
        return select_evidence(
            queries, pool, list(scores), self._settings.evidence_k, self._settings.min_rerank_score
        )


_UNSET = object()
_override: Any = _UNSET


@lru_cache
def _default_retriever() -> PubMedRetriever | None:
    settings = get_settings()
    if not (settings.retrieval_enabled and settings.database_url):
        return None
    try:
        return PubMedRetriever(settings)
    except ImportError as exc:  # ml extra (torch/transformers) not installed
        logger.warning("PubMed retrieval disabled: %s", exc)
        return None


def get_retriever() -> Retriever | None:
    return _default_retriever() if _override is _UNSET else _override


@contextmanager
def use_retriever(retriever: Retriever | None) -> Iterator[None]:
    """Temporarily replace the retriever (tests; `None` simulates no database)."""
    global _override
    previous, _override = _override, retriever
    try:
        yield
    finally:
        _override = previous
