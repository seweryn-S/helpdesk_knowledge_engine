from __future__ import annotations

import logging
from typing import List, Optional

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class EmbeddingClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient):
        self.settings = settings
        self.http_client = http_client
        self.base_url = str(settings.embedding_api_base_url).rstrip("/")
        self.model_name = settings.embedding_model_name
        self.prompt_prefix = settings.embedding_prompt_prefix
        self.context_length = settings.embedding_context_length

    @property
    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {self.settings.embedding_api_key}"
        return headers

    def _prepare_inputs(self, texts: List[str]) -> List[str]:
        if not self.prompt_prefix:
            return texts
        return [f"{self.prompt_prefix}{text}" for text in texts]

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        inputs = self._prepare_inputs(texts)
        payload = {"model": self.model_name, "input": inputs}
        response = await self.http_client.post(
            f"{self.base_url}/embeddings",
            headers=self._headers,
            json=payload,
            timeout=30.0,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Embedding API error {response.status_code}: {response.text}"
            )
        data = response.json()
        if "data" not in data:
            raise RuntimeError("Embedding API response missing 'data' field")
        embeddings: List[Optional[List[float]]] = [None] * len(inputs)
        for item in data["data"]:
            idx = item.get("index", 0)
            embeddings[idx] = item.get("embedding")
        if any(emb is None for emb in embeddings):
            raise RuntimeError("Embedding API returned incomplete embeddings")
        return [emb for emb in embeddings if emb is not None]

    async def probe_dimension(self) -> int:
        logger.info("Probing embedding dimension via embedding API")
        sample = "dimension probe"
        embeddings = await self.embed_texts([sample])
        if not embeddings or not embeddings[0]:
            raise RuntimeError("Failed to obtain embedding for dimension probe")
        dim = len(embeddings[0])
        logger.info("Detected embedding dimension: %s", dim)
        return dim
