"""Embedding clients for the homiletics engine.

Production: LocalEmbeddingsClient wraps sentence-transformers / BAAI/bge-large-en-v1.5.
The model lazy-loads on first embed() call. Subsequent calls reuse the in-process
model. Thread-safe via a single mutex around encode().

Tests: CannedEmbeddingsClient returns deterministic vectors keyed by text hash —
unit-normalized so cosine similarity tests behave predictably.
"""

from __future__ import annotations
import hashlib
import math
import struct
import threading
from typing import Protocol


class EmbeddingsClient(Protocol):
    def embed(self, texts: list) -> list: ...
    def embed_one(self, text: str) -> list: ...
    @property
    def dimension(self) -> int: ...


class CannedEmbeddingsClient:
    """Deterministic test double. Vectors derived from SHA-256 of text."""

    def __init__(self, dimension: int = 8):
        self._dim = dimension

    @property
    def dimension(self) -> int:
        return self._dim

    def embed_one(self, text: str) -> list:
        return self._vector(text)

    def embed(self, texts: list) -> list:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list:
        # Stretch SHA-256 into `dimension` float32s, then unit-normalize.
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Repeat the hash until we have enough bytes
        needed = self._dim * 4
        buf = b""
        while len(buf) < needed:
            buf += hashlib.sha256(buf + h).digest()
        raw = list(struct.unpack(f"{self._dim}f", buf[:needed]))
        # Normalize to unit length
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]


class LocalEmbeddingsClient:
    """sentence-transformers wrapper. Lazy model load on first embed()."""

    def __init__(self, model_name: str = "BAAI/bge-large-en-v1.5"):
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()
        self._dim = None

    @property
    def dimension(self) -> int:
        if self._dim is None:
            self._ensure_loaded()
        return self._dim

    def embed_one(self, text: str) -> list:
        return self.embed([text])[0]

    def embed(self, texts: list) -> list:
        self._ensure_loaded()
        with self._lock:
            vecs = self._model.encode(texts, normalize_embeddings=True,
                                      show_progress_bar=False)
        return [v.tolist() for v in vecs]

    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError(
                    f"sentence_transformers not installed. Run "
                    f"tools/setup_embeddings.sh to install to /Volumes/External/Logos4/Data/python-deps. "
                    f"Original error: {e}"
                )
            self._model = SentenceTransformer(self._model_name)
            self._dim = self._model.get_sentence_embedding_dimension()
