from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Sequence

from qdrant_client import models as qmodels

from app.clients.qdrant import QdrantRepository
from app.models.schemas import (
    ChunkMetadata,
    ThreadEntry,
    TicketDetailsResponse,
    TicketThreadConciseResponse,
    TicketThreadResponse,
)


class ThreadService:
    def __init__(self, qdrant_repo: QdrantRepository | None, ticket_url_prefix: str | None = None):
        self.qdrant_repo = qdrant_repo
        self.ticket_url_prefix = ticket_url_prefix

    def get_ticket_details(self, ticket_id: int) -> TicketDetailsResponse:
        ticket_payloads = self._fetch_payloads(ticket_id, source="ticket")
        if not ticket_payloads:
            raise ValueError(f"Ticket {ticket_id} not found in Qdrant")
        return self._build_ticket_details(ticket_id, ticket_payloads)

    def get_ticket_thread(self, ticket_id: int, concise: bool = False) -> TicketThreadResponse | TicketThreadConciseResponse:
        ticket_payloads = self._fetch_payloads(ticket_id, source="ticket")
        if not ticket_payloads:
            raise ValueError(f"Ticket {ticket_id} not found in Qdrant")
        ticket_details = self._build_ticket_details(ticket_id, ticket_payloads)
        update_payloads = self._fetch_payloads(ticket_id, source="update")
        updates = self._build_update_entries(ticket_id, update_payloads)
        thread_text = self._compose_thread_text(ticket_details.details, updates)
        if concise:
            return self._build_concise_thread(ticket_id, ticket_payloads, update_payloads, thread_text)
        return TicketThreadResponse(ticket=ticket_details, updates=updates, thread_text=thread_text)

    def _fetch_payloads(self, ticket_id: int, source: str) -> List[Dict[str, Any]]:
        if self.qdrant_repo is None:
            raise RuntimeError("Qdrant repository is not configured")
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
                text=(payload.get("detail_chunk") or ""),
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

    def _build_concise_thread(
        self,
        ticket_id: int,
        ticket_payloads: List[Dict[str, Any]],
        update_payloads: List[Dict[str, Any]],
        thread_text: str,
    ) -> TicketThreadConciseResponse:
        chunks = self._sort_chunks(ticket_payloads)
        if not chunks:
            raise ValueError(f"Ticket {ticket_id} has no chunks in Qdrant")
        exemplar = chunks[0]
        topic = exemplar.get("topic")
        status = exemplar.get("status")
        tags = exemplar.get("tags") or []
        url_suffix = exemplar.get("url_suffix")
        ticket_time_created = self._parse_datetime(exemplar.get("time_created"))
        ticket_time_modified = self._parse_datetime(exemplar.get("time_modified"))
        last_update_time = self._latest_update_time(update_payloads)
        time_modified = self._calculate_time_modified(ticket_time_modified, ticket_time_created, last_update_time)
        full_url = f"{self.ticket_url_prefix}{url_suffix}" if self.ticket_url_prefix and url_suffix else None

        return TicketThreadConciseResponse(
            ticket_id=ticket_id,
            topic=topic,
            status=status,
            tags=list(tags) if isinstance(tags, list) else [],
            url_suffix=url_suffix,
            url=full_url,
            time_created=ticket_time_created,
            time_modified=time_modified,
            thread_text=thread_text,
        )

    @staticmethod
    def _sort_chunks(payloads: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(
            [
                payload
                for payload in payloads
                if payload.get("detail_chunk") is not None
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
                chunk_text = ""
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
    def _latest_update_time(payloads: Iterable[Dict[str, Any]]) -> datetime | None:
        latest: datetime | None = None
        for payload in payloads:
            for field_name in ("time_modified", "time_created"):
                candidate = ThreadService._parse_datetime(payload.get(field_name))
                if candidate and (latest is None or candidate > latest):
                    latest = candidate
        return latest

    @staticmethod
    def _calculate_time_modified(
        ticket_time_modified: datetime | None,
        ticket_time_created: datetime | None,
        last_update_time: datetime | None,
    ) -> datetime | None:
        candidates = [dt for dt in (ticket_time_modified, last_update_time, ticket_time_created) if dt]
        if not candidates:
            return None
        return max(candidates)

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
