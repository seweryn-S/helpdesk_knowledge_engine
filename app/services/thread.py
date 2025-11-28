from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Sequence

from qdrant_client import models as qmodels

from app.clients.qdrant import QdrantRepository
from app.models.schemas import ChunkMetadata, ThreadEntry, TicketDetailsResponse, TicketThreadResponse


class ThreadService:
    def __init__(self, qdrant_repo: QdrantRepository):
        self.qdrant_repo = qdrant_repo

    def get_ticket_details(self, ticket_id: int) -> TicketDetailsResponse:
        ticket_payloads = self._fetch_payloads(ticket_id, source="ticket")
        if not ticket_payloads:
            raise ValueError(f"Ticket {ticket_id} not found in Qdrant")
        return self._build_ticket_details(ticket_id, ticket_payloads)

    def get_ticket_thread(self, ticket_id: int) -> TicketThreadResponse:
        ticket_payloads = self._fetch_payloads(ticket_id, source="ticket")
        if not ticket_payloads:
            raise ValueError(f"Ticket {ticket_id} not found in Qdrant")
        ticket_details = self._build_ticket_details(ticket_id, ticket_payloads)
        update_payloads = self._fetch_payloads(ticket_id, source="update")
        updates = self._build_update_entries(ticket_id, update_payloads)
        thread_text = self._compose_thread_text(ticket_details.details, updates)
        return TicketThreadResponse(ticket=ticket_details, updates=updates, thread_text=thread_text)

    def _fetch_payloads(self, ticket_id: int, source: str) -> List[Dict[str, Any]]:
        qfilter = qmodels.Filter(
            must=[
                qmodels.FieldCondition(key="ticket_id", match=qmodels.MatchValue(value=ticket_id)),
            ]
        )
        if source == "ticket":
            records = self.qdrant_repo.scroll_tickets(query_filter=qfilter)
        else:
            records = self.qdrant_repo.scroll_updates(query_filter=qfilter)
        return [record.payload or {} for record in records]

    def _build_ticket_details(self, ticket_id: int, payloads: List[Dict[str, Any]]) -> TicketDetailsResponse:
        chunks = self._sort_chunks(payloads)
        if not chunks:
            raise ValueError(f"Ticket {ticket_id} has no chunks in Qdrant")
        details_text = self._join_chunks(chunks)
        topic = chunks[0].get("topic")
        chunk_meta = [
            ChunkMetadata(
                chunk_no=payload.get("chunk_no", idx),
                chunk_total=payload.get("chunk_total", len(chunks)),
                chunk_start=payload.get("chunk_start", 0),
                chunk_end=payload.get("chunk_end", 0),
                sentence_start=payload.get("sentence_start", 0),
                sentence_end=payload.get("sentence_end", 0),
                text=payload.get("detail_chunk") or payload.get("chunk_text") or payload.get("text") or "",
                details_hash=payload.get("details_hash"),
            )
            for idx, payload in enumerate(chunks)
        ]
        return TicketDetailsResponse(ticket_id=ticket_id, topic=topic, details=details_text, chunks=chunk_meta)

    def _build_update_entries(self, ticket_id: int, payloads: List[Dict[str, Any]]) -> List[ThreadEntry]:
        grouped: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for payload in payloads:
            update_id = payload.get("update_id")
            if update_id is None:
                continue
            grouped[int(update_id)].append(payload)

        entries: List[ThreadEntry] = []
        for update_id, update_payloads in grouped.items():
            chunks = self._sort_chunks(update_payloads)
            text = self._join_chunks(chunks)
            exemplar = chunks[0] if chunks else {}
            entries.append(
                ThreadEntry(
                    source="update",
                    ticket_id=ticket_id,
                    update_id=update_id,
                    user_role=exemplar.get("user_role"),
                    status=exemplar.get("status"),
                    time_created=self._parse_datetime(exemplar.get("time_created")),
                    chunk_count=len(chunks),
                    text=text,
                )
            )

        entries.sort(key=lambda entry: entry.time_created or datetime.min)
        return entries

    @staticmethod
    def _sort_chunks(payloads: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(
            [
                payload
                for payload in payloads
                if payload.get("detail_chunk") or payload.get("chunk_text") or payload.get("text")
            ],
            key=lambda payload: (
                payload.get("chunk_start", 0),
                payload.get("chunk_no", 0),
            ),
        )

    @staticmethod
    def _join_chunks(chunks: Sequence[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for payload in chunks:
            chunk_text = payload.get("detail_chunk")
            if chunk_text is None:
                chunk_text = payload.get("chunk_text")
            if chunk_text is None:
                chunk_text = payload.get("text", "")
            parts.append(chunk_text)
        return "".join(parts)

    @staticmethod
    def _compose_thread_text(details: str, updates: Sequence[ThreadEntry]) -> str:
        if not updates:
            return details
        parts = [details]
        for entry in updates:
            header_parts = []
            if entry.time_created:
                header_parts.append(entry.time_created.isoformat())
            if entry.user_role:
                header_parts.append(entry.user_role)
            if entry.status:
                header_parts.append(entry.status)
            header = " | ".join(header_parts) if header_parts else "update"
            parts.append(f"\n\n[{header}]\n{entry.text}")
        return "".join(parts)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
