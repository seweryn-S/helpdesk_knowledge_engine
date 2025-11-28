from __future__ import annotations

import logging
from typing import Optional

from qdrant_client import models as qmodels

from app.clients.embeddings import EmbeddingClient
from app.clients.qdrant import QdrantRepository
from app.core.config import Settings
from app.models.schemas import Passage, QueryFilters, QueryRequest, QueryResponse

logger = logging.getLogger(__name__)


class QueryService:
    def __init__(
        self,
        embedding_client: EmbeddingClient,
        qdrant_repo: QdrantRepository,
        settings: Settings,
    ):
        self.embedding_client = embedding_client
        self.qdrant_repo = qdrant_repo
        self.ticket_url_prefix = str(settings.helpdesk_ticket_url_prefix)

    async def query(self, request: QueryRequest) -> QueryResponse:
        embeddings = await self.embedding_client.embed_texts([request.query])
        if not embeddings:
            raise RuntimeError("Failed to get embedding for query")
        vector = embeddings[0]
        qfilter = self._build_filter(request.filters)
        ticket_points = self.qdrant_repo.search_tickets(vector=vector, limit=request.limit, query_filter=qfilter)
        update_points = self.qdrant_repo.search_updates(vector=vector, limit=request.limit, query_filter=qfilter)

        merged = ticket_points + update_points
        merged.sort(key=lambda p: p.score or 0.0, reverse=True)
        selected = merged[: request.limit]

        passages: list[Passage] = []
        for p in selected:
            payload = p.payload or {}
            chunk_text = payload.get("detail_chunk") or payload.get("chunk_text") or payload.get("text") or ""
            passage_text = chunk_text.strip() or chunk_text
            url_suffix = payload.get("url_suffix")
            full_url = f"{self.ticket_url_prefix}{url_suffix}" if url_suffix else None
            passages.append(
                Passage(
                    text=passage_text,
                    score=p.score or 0.0,
                    source=payload.get("source", "unknown"),
                    ticket_id=payload.get("ticket_id"),
                    update_id=payload.get("update_id"),
                    status=payload.get("status"),
                    category=payload.get("category"),
                    tags=payload.get("tags", []) or [],
                    user_role=payload.get("user_role"),
                    update_type=payload.get("update_type"),
                    time_created=payload.get("time_created"),
                    time_modified=payload.get("time_modified"),
                    url_suffix=url_suffix,
                    url=full_url,
                    chunk_no=payload.get("chunk_no"),
                    chunk_total=payload.get("chunk_total"),
                    chunk_start=payload.get("chunk_start"),
                    chunk_end=payload.get("chunk_end"),
                    sentence_start=payload.get("sentence_start"),
                    sentence_end=payload.get("sentence_end"),
                    details_hash=payload.get("details_hash"),
                    chunk_text=chunk_text,
                )
            )
        return QueryResponse(query=request.query, results=passages)

    def _build_filter(self, filters: Optional[QueryFilters]) -> Optional[qmodels.Filter]:
        if not filters:
            return None
        must: list[qmodels.FieldCondition] = []

        if filters.hide_hidden:
            must.append(
                qmodels.FieldCondition(
                    key="hidden",
                    match=qmodels.MatchValue(value=False),
                )
            )
        if filters.status:
            must.append(
                qmodels.FieldCondition(
                    key="status",
                    match=qmodels.MatchAny(any=filters.status),
                )
            )
        if filters.category:
            must.append(
                qmodels.FieldCondition(
                    key="category",
                    match=qmodels.MatchAny(any=filters.category),
                )
            )
        if filters.tags:
            must.append(
                qmodels.FieldCondition(
                    key="tags",
                    match=qmodels.MatchAny(any=filters.tags),
                )
            )
        if filters.user_roles:
            must.append(
                qmodels.FieldCondition(
                    key="user_role",
                    match=qmodels.MatchAny(any=filters.user_roles),
                )
            )
        if filters.update_types:
            must.append(
                qmodels.FieldCondition(
                    key="update_type",
                    match=qmodels.MatchAny(any=filters.update_types),
                )
            )
        time_range = {}
        if filters.time_from:
            time_range["gte"] = filters.time_from.timestamp()
        if filters.time_to:
            time_range["lte"] = filters.time_to.timestamp()
        if time_range:
            must.append(
                qmodels.FieldCondition(
                    key="time_modified_unix",
                    range=qmodels.Range(**time_range),
                )
            )
        if not must:
            return None
        return qmodels.Filter(must=must)
