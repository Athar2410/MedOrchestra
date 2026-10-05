"""MedCPT encoders (NCBI): article/query bi-encoders and a cross-encoder reranker.

Requires the `ml` extra (torch, transformers). Models load lazily on first use and
inference is serialised with a lock; callers on the event loop should use
`asyncio.to_thread`. Benchmarks on the dev laptop (i5-1235U, CPU only): articles
~7/s, 5 queries ~140 ms, cross-encoder ~0.15 s per doc at 256 tokens.
"""

import threading
from functools import lru_cache
from typing import Any

import numpy as np

from app.config import Settings, get_settings


class MedCPT:
    def __init__(self, settings: Settings) -> None:
        import torch  # deferred: the app runs without the ml extra

        torch.set_num_threads(settings.torch_threads)
        self._torch = torch
        self._settings = settings
        self._models: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _load(self, kind: str) -> tuple[Any, Any]:
        if kind not in self._models:
            from transformers import (
                AutoModel,
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

            name = {
                "query": self._settings.medcpt_query_model,
                "article": self._settings.medcpt_article_model,
                "cross": self._settings.medcpt_cross_model,
            }[kind]
            model_cls = AutoModelForSequenceClassification if kind == "cross" else AutoModel
            self._models[kind] = (
                AutoTokenizer.from_pretrained(name),
                model_cls.from_pretrained(name).eval(),
            )
        return self._models[kind]

    def warm_up(self, kinds: tuple[str, ...] = ("query", "cross")) -> None:
        with self._lock:
            for kind in kinds:
                self._load(kind)

    def _embed(self, kind: str, inputs: list, max_length: int) -> np.ndarray:
        with self._lock, self._torch.inference_mode():
            tokenizer, model = self._load(kind)
            enc = tokenizer(
                inputs, truncation=True, padding=True, return_tensors="pt", max_length=max_length
            )
            # MedCPT uses the [CLS] token embedding.
            return model(**enc).last_hidden_state[:, 0, :].float().numpy()

    def encode_articles(self, articles: list[tuple[str, str]]) -> np.ndarray:
        """(title, abstract) pairs -> (n, 768) embeddings."""
        return self._embed("article", [list(a) for a in articles], max_length=512)

    def encode_queries(self, queries: list[str]) -> np.ndarray:
        return self._embed("query", queries, max_length=64)

    def rerank(self, pairs: list[tuple[str, str]], max_length: int = 256) -> np.ndarray:
        """(query, document) pairs -> relevance logits (higher is more relevant)."""
        if not pairs:
            return np.zeros(0)
        with self._lock, self._torch.inference_mode():
            tokenizer, model = self._load("cross")
            enc = tokenizer(
                [list(p) for p in pairs],
                truncation="only_second",
                padding=True,
                return_tensors="pt",
                max_length=max_length,
            )
            return model(**enc).logits.squeeze(-1).float().numpy()


@lru_cache
def get_medcpt() -> MedCPT:
    return MedCPT(get_settings())


def to_halfvec_literal(vector: np.ndarray) -> str:
    """pgvector text format, e.g. '[0.1,0.2]', cast with ::halfvec in SQL."""
    return "[" + ",".join(f"{x:.5g}" for x in vector.tolist()) + "]"
