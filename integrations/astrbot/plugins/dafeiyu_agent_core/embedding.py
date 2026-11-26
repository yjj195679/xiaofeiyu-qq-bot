"""Pluggable embeddings with opt-in external transmission and safe fallback."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol, Sequence

from .store import AgentStore


SENSITIVE = re.compile(r"密码|验证码|api\s*key|token|secret|cookie|session", re.I)


class EmbeddingProvider(Protocol):
    name: str
    model: str
    external: bool

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class DisabledEmbeddingProvider:
    name = "disabled"
    model = "none"
    external = False

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embedding provider is disabled")


class EmbeddingService:
    def __init__(self, store: AgentStore, provider: EmbeddingProvider | None = None):
        self.store = store
        self.provider = provider or DisabledEmbeddingProvider()

    def vector(self, text: str, *, allow_external: bool = False) -> list[float] | None:
        text = text.strip()
        if not text or len(text) > 8000 or SENSITIVE.search(text):
            return None
        if self.provider.external and not allow_external:
            return None
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cached = self.store.get_cached_embedding(
            self.provider.name, self.provider.model, digest,
        )
        if cached is not None:
            return cached
        try:
            vectors = self.provider.embed([text])
            if len(vectors) != 1:
                return None
            vector = [float(value) for value in vectors[0]]
            self.store.cache_embedding(
                self.provider.name, self.provider.model, digest, vector,
            )
            return vector
        except (ArithmeticError, OSError, RuntimeError, TypeError, ValueError):
            return None
