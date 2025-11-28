from __future__ import annotations

import logging

from qdrant_client import QdrantClient, models as qmodels
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import Settings

logger = logging.getLogger(__name__)


class QdrantRepository:
    def __init__(self, settings: Settings):
        scheme = "https" if settings.qdrant_use_tls else "http"
        self.ticket_collection = settings.qdrant_ticket_collection
        self.update_collection = settings.qdrant_update_collection
        self.client = QdrantClient(
            url=f"{scheme}://{settings.qdrant_host}:{settings.qdrant_port}",
            api_key=settings.qdrant_api_key or None,
        )

    def ensure_collections(self, vector_size: int, distance: qmodels.Distance = qmodels.Distance.COSINE) -> None:
        """Ensure both ticket and update collections exist with the right vector size."""
        self._ensure_collection(self.ticket_collection, vector_size, distance)
        self._ensure_collection(self.update_collection, vector_size, distance)

    def _ensure_collection(
        self,
        collection_name: str,
        vector_size: int,
        distance: qmodels.Distance = qmodels.Distance.COSINE,
    ) -> None:
        try:
            info = self.client.get_collection(collection_name)
            existing_vectors = info.config.params.vectors
            if isinstance(existing_vectors, dict):
                vector_params = existing_vectors.get("embedding")
            else:
                vector_params = existing_vectors
            if vector_params is None:
                raise RuntimeError("Collection exists but vector params missing")
            if vector_params.size != vector_size:
                raise RuntimeError(
                    f"Vector size mismatch: collection has {vector_params.size}, expected {vector_size}"
                )
            logger.info("Qdrant collection '%s' already exists", collection_name)
            return
        except UnexpectedResponse as exc:
            if getattr(exc, "status_code", None) != 404:
                raise

        logger.info("Creating Qdrant collection '%s' with vector size %s", collection_name, vector_size)
        self.client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=vector_size,
                distance=distance,
            ),
        )

    def upsert_ticket_points(self, points: list[qmodels.PointStruct]) -> None:
        self._upsert_points(self.ticket_collection, points)

    def upsert_update_points(self, points: list[qmodels.PointStruct]) -> None:
        self._upsert_points(self.update_collection, points)

    def _upsert_points(self, collection_name: str, points: list[qmodels.PointStruct]) -> None:
        if not points:
            return
        self.client.upsert(
            collection_name=collection_name,
            points=points,
        )

    def search_tickets(
        self,
        vector: list[float],
        limit: int,
        query_filter: qmodels.Filter | None = None,
    ) -> list[qmodels.ScoredPoint]:
        return self._search(self.ticket_collection, vector, limit, query_filter)

    def search_updates(
        self,
        vector: list[float],
        limit: int,
        query_filter: qmodels.Filter | None = None,
    ) -> list[qmodels.ScoredPoint]:
        return self._search(self.update_collection, vector, limit, query_filter)

    def _search(
        self,
        collection_name: str,
        vector: list[float],
        limit: int,
        query_filter: qmodels.Filter | None = None,
    ) -> list[qmodels.ScoredPoint]:
        response = self.client.query_points(
            collection_name=collection_name,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return list(response.points or [])

    def scroll_tickets(
        self,
        query_filter: qmodels.Filter,
        limit: int = 128,
        with_vectors: bool = False,
    ) -> list[qmodels.Record]:
        return self._scroll(self.ticket_collection, query_filter, limit, with_vectors)

    def scroll_updates(
        self,
        query_filter: qmodels.Filter,
        limit: int = 128,
        with_vectors: bool = False,
    ) -> list[qmodels.Record]:
        return self._scroll(self.update_collection, query_filter, limit, with_vectors)

    def _scroll(
        self,
        collection_name: str,
        query_filter: qmodels.Filter,
        limit: int = 128,
        with_vectors: bool = False,
    ) -> list[qmodels.Record]:
        records: list[qmodels.Record] = []
        offset = None
        while True:
            page, offset = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=query_filter,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            records.extend(page or [])
            if offset is None:
                break
        return records
