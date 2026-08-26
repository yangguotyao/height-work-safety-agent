from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from openai import OpenAI

from ..config import Settings


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbeddingProvider:
    provider_name = "hashing"

    def __init__(self, dimension: int):
        self.dimension = dimension
        self.model_name = f"cjk-ngram-hashing-{dimension}d"

    def _embed(self, text: str) -> list[float]:
        compact = re.sub(r"\s+", "", text.lower())
        vector = [0.0] * self.dimension
        features: list[str] = []
        for size in (2, 3):
            features.extend(
                compact[index : index + size] for index in range(len(compact) - size + 1)
            )
        features.extend(re.findall(r"[a-z]+\d+(?:\.\d+)*|\d+(?:\.\d+)?(?:mm|cm|m)?", text.lower()))
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            raw = int.from_bytes(digest, "little")
            index = raw % self.dimension
            sign = 1.0 if raw & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OpenAIEmbeddingProvider:
    provider_name = "openai"

    def __init__(self, settings: Settings):
        if not settings.model_api_key:
            raise ValueError("外部 Embedding 必须设置 MODEL_API_KEY")
        if not settings.embedding_model:
            raise ValueError("EMBEDDING_PROVIDER=openai 时必须设置 EMBEDDING_MODEL")
        arguments = {
            "api_key": settings.model_api_key,
            "timeout": settings.model_timeout_seconds,
            "max_retries": 0,
        }
        if settings.model_base_url:
            arguments["base_url"] = settings.model_base_url
        self.client = OpenAI(**arguments)
        self.model_name = settings.embedding_model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model_name, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingProvider(settings)
    return HashingEmbeddingProvider(settings.embedding_dimension)
