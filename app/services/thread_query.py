from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from qdrant_client import models as qmodels

from app.clients.embeddings import EmbeddingClient
from app.clients.qdrant import QdrantRepository
from app.core.config import Settings
from app.models.schemas import QueryRequest, ThreadEntry, ThreadQueryResponse, ThreadSearchResult
from app.services.thread import ThreadService

logger = logging.getLogger(__name__)


class ThreadQueryService:
    _SEARCH_MULTIPLIER = 3
    _SEARCH_LIMIT_MAX = 100

    def __init__(
        self,
        embedding_client: EmbeddingClient,
        qdrant_repo: QdrantRepository,
        settings: Settings,
    ):
        self.embedding_client = embedding_client
        self.qdrant_repo = qdrant_repo
        self.ticket_url_prefix = str(settings.helpdesk_ticket_url_prefix)

    async def query_threads(self, request: QueryRequest) -> ThreadQueryResponse:
        vector = await self._embed_query(request.query)
        qfilter = self._build_filter(request)

        search_limit = min(max(request.limit * self._SEARCH_MULTIPLIER, request.limit), self._SEARCH_LIMIT_MAX)
        ticket_points = self.qdrant_repo.search_tickets(vector=vector, limit=search_limit, query_filter=qfilter)
        update_points = self.qdrant_repo.search_updates(vector=vector, limit=search_limit, query_filter=qfilter)

        merged_points = ticket_points + update_points
        merged_points.sort(key=lambda p: p.score or 0.0, reverse=True)

        ticket_ids, ticket_scores = self._select_ticket_ids(merged_points, request.limit)
        if not ticket_ids:
            return ThreadQueryResponse(query=request.query, results=[])

        hide_hidden = True

        ticket_payloads = self._fetch_ticket_payloads(ticket_ids)
        update_payloads = self._fetch_update_payloads(ticket_ids, hide_hidden)

        results: List[ThreadSearchResult] = []
        for ticket_id in ticket_ids:
            ticket_chunks = ticket_payloads.get(ticket_id, [])
            if not ticket_chunks:
                logger.warning("Ticket %s missing in Qdrant payloads during thread query", ticket_id)
                continue
            updates_for_ticket = update_payloads.get(ticket_id, [])
            thread = self._build_thread(
                ticket_id,
                ticket_chunks,
                updates_for_ticket,
                ticket_scores.get(ticket_id, 0.0),
                hide_hidden,
            )
            if thread:
                results.append(thread)

        return ThreadQueryResponse(query=request.query, results=results)

    async def _embed_query(self, query: str) -> List[float]:
        embeddings = await self.embedding_client.embed_texts([query])
        if not embeddings:
            raise RuntimeError("Failed to get embedding for query")
        return embeddings[0]

    @staticmethod
    def _build_filter(request: QueryRequest) -> Optional[qmodels.Filter]:
        must: List[qmodels.FieldCondition] = []

        # Always skip hidden and semantically empty entries in thread search.
        must.append(
            qmodels.FieldCondition(
                key="hidden",
                match=qmodels.MatchValue(value=False),
            )
        )
        must.append(
            qmodels.FieldCondition(
                key="semantic_empty",
                match=qmodels.MatchValue(value=False),
            )
        )
        if request.status:
            must.append(
                qmodels.FieldCondition(
                    key="status",
                    match=qmodels.MatchAny(any=request.status),
                )
            )
        if request.category:
            must.append(
                qmodels.FieldCondition(
                    key="category",
                    match=qmodels.MatchAny(any=request.category),
                )
            )
        if request.tags:
            must.append(
                qmodels.FieldCondition(
                    key="tags",
                    match=qmodels.MatchAny(any=request.tags),
                )
            )
        # Filtering by user_role and update_type has been removed from the public request
        # model to simplify the interface for LLMs.
        time_range: Dict[str, float] = {}
        if request.time_from:
            time_range["gte"] = request.time_from.timestamp()
        if request.time_to:
            time_range["lte"] = request.time_to.timestamp()
        if time_range:
            must.append(
                qmodels.FieldCondition(
                    key="time_modified_unix",
                    range=qmodels.Range(**time_range),
                )
            )
        return qmodels.Filter(must=must)

    @staticmethod
    def _select_ticket_ids(
        points: Sequence[qmodels.ScoredPoint], limit: int
    ) -> Tuple[List[int], Dict[int, float]]:
        ticket_ids: List[int] = []
        scores: Dict[int, float] = {}
        for point in points:
            payload = point.payload or {}
            ticket_id = payload.get("ticket_id")
            if ticket_id is None:
                continue
            score = point.score or 0.0
            if ticket_id not in scores:
                ticket_ids.append(int(ticket_id))
                scores[int(ticket_id)] = score
            else:
                scores[int(ticket_id)] = max(scores[int(ticket_id)], score)
            if len(ticket_ids) >= limit:
                break
        return ticket_ids, scores

    def _fetch_ticket_payloads(self, ticket_ids: Sequence[int]) -> Dict[int, List[Dict[str, Any]]]:
        if not ticket_ids:
            return {}
        query_filter = qmodels.Filter(
            must=[
                qmodels.FieldCondition(key="ticket_id", match=qmodels.MatchAny(any=list(ticket_ids))),
            ]
        )
        records = self.qdrant_repo.scroll_tickets(query_filter=query_filter)
        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for record in records:
            payload = record.payload or {}
            ticket_id = payload.get("ticket_id")
            if ticket_id is None:
                continue
            grouped[int(ticket_id)].append(payload)
        return grouped

    def _fetch_update_payloads(
        self,
        ticket_ids: Sequence[int],
        hide_hidden: bool,
    ) -> Dict[int, List[Dict[str, Any]]]:
        if not ticket_ids:
            return {}
        must: List[qmodels.FieldCondition] = [
            qmodels.FieldCondition(key="ticket_id", match=qmodels.MatchAny(any=list(ticket_ids))),
        ]
        if hide_hidden:
            must.append(qmodels.FieldCondition(key="hidden", match=qmodels.MatchValue(value=False)))
        query_filter = qmodels.Filter(must=must)
        records = self.qdrant_repo.scroll_updates(query_filter=query_filter)
        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for record in records:
            payload = record.payload or {}
            ticket_id = payload.get("ticket_id")
            if ticket_id is None:
                continue
            grouped[int(ticket_id)].append(payload)
        return grouped

    def _build_thread(
        self,
        ticket_id: int,
        ticket_payloads: Iterable[Dict[str, Any]],
        update_payloads: Iterable[Dict[str, Any]],
        score: float,
        hide_hidden: bool,
    ) -> Optional[ThreadSearchResult]:
        chunks = ThreadService._sort_chunks(ticket_payloads)
        if not chunks:
            return None
        details_text = ThreadService._join_chunks(chunks)
        exemplar = chunks[0]

        topic = exemplar.get("topic")
        status = exemplar.get("status")
        tags = exemplar.get("tags") or []
        url_suffix = exemplar.get("url_suffix")
        time_created = ThreadService._parse_datetime(exemplar.get("time_created"))
        ticket_time_modified = ThreadService._parse_datetime(exemplar.get("time_modified"))

        updates, last_update_time = self._build_update_entries(ticket_id, update_payloads, hide_hidden)
        time_modified = self._calculate_time_modified(ticket_time_modified, time_created, last_update_time)

        thread_text = self._compose_thread_text(details_text, updates)
        full_url = f"{self.ticket_url_prefix}{url_suffix}" if url_suffix else None

        return ThreadSearchResult(
            ticket_id=ticket_id,
            topic=topic,
            status=status,
            tags=list(tags) if isinstance(tags, list) else [],
            url_suffix=url_suffix,
            url=full_url,
            time_created=time_created,
            time_modified=time_modified,
            score=score,
            thread_text=thread_text,
        )

    def _build_update_entries(
        self,
        ticket_id: int,
        payloads: Iterable[Dict[str, Any]],
        skip_hidden: bool,
    ) -> Tuple[List[ThreadEntry], Optional[datetime]]:
        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for payload in payloads:
            update_id = payload.get("update_id")
            if skip_hidden and payload.get("hidden") is True:
                continue
            if update_id is None:
                continue
            grouped[int(update_id)].append(payload)

        entries: List[ThreadEntry] = []
        last_update_time: Optional[datetime] = None
        for update_id, update_payloads in grouped.items():
            sorted_chunks = ThreadService._sort_chunks(update_payloads)
            text = ThreadService._join_chunks(sorted_chunks)
            exemplar = sorted_chunks[0] if sorted_chunks else {}
            time_created = ThreadService._parse_datetime(exemplar.get("time_created"))
            time_modified = ThreadService._parse_datetime(exemplar.get("time_modified"))
            candidate_time = time_modified or time_created
            if candidate_time and (last_update_time is None or candidate_time > last_update_time):
                last_update_time = candidate_time

            entries.append(
                ThreadEntry(
                    source="update",
                    ticket_id=ticket_id,
                    update_id=update_id,
                    user_role=exemplar.get("user_role"),
                    status=exemplar.get("status"),
                    time_created=time_created,
                    chunk_count=len(sorted_chunks),
                    text=text,
                )
            )

        entries.sort(key=lambda entry: entry.time_created or datetime.min)
        return entries, last_update_time

    @staticmethod
    def _calculate_time_modified(
        ticket_time_modified: Optional[datetime],
        ticket_time_created: Optional[datetime],
        last_update_time: Optional[datetime],
    ) -> Optional[datetime]:
        candidates = [dt for dt in [ticket_time_modified, last_update_time, ticket_time_created] if dt]
        if not candidates:
            return None
        return max(candidates)

    @staticmethod
    def _compose_thread_text(details: str, updates: Sequence[ThreadEntry]) -> str:
        def _with_role_prefix(text: str, role: Optional[str]) -> str:
            if role:
                return f"{role}: {text}"
            return text

        if not updates:
            return _with_role_prefix(details, None)

        parts: List[str] = [_with_role_prefix(details, None)]
        for entry in updates:
            header_parts = []
            if entry.time_created:
                header_parts.append(entry.time_created.isoformat())
            if entry.user_role:
                header_parts.append(entry.user_role)
            header = " | ".join(header_parts) if header_parts else "update"
            parts.append(f"\n\n[{header}]\n{_with_role_prefix(entry.text, entry.user_role)}")
        return "".join(parts)
