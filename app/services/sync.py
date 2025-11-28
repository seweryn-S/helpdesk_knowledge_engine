from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Pattern, Sequence, Tuple
from uuid import UUID, uuid5

import aiosqlite
from qdrant_client import models as qmodels
from app.clients.embeddings import EmbeddingClient
from app.clients.helpdesk import HelpDeskClient
from app.clients.qdrant import QdrantRepository
from app.core.config import Settings
from app.models.schemas import (
    SyncFilters,
    SyncRequest,
    SyncResult,
    TicketDTO,
    UpdateDTO,
)
from app.utils.chunking import TextChunk, chunk_text, merge_texts

logger = logging.getLogger(__name__)


class SyncState:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints (entity TEXT PRIMARY KEY, last_modified TEXT)"
            )
            await db.commit()

    async def get_checkpoint(self, entity: str) -> Optional[datetime]:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT last_modified FROM checkpoints WHERE entity = ?", (entity,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row or not row[0]:
                    return None
                return datetime.fromisoformat(row[0])

    async def set_checkpoint(self, entity: str, value: Optional[datetime]) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO checkpoints(entity, last_modified) VALUES (?, ?)",
                (entity, value.isoformat() if value else None),
            )
            await db.commit()


class SyncService:
    _POINT_ID_NAMESPACE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    def __init__(
        self,
        settings: Settings,
        helpdesk_client: HelpDeskClient,
        embedding_client: EmbeddingClient,
        qdrant_repo: QdrantRepository,
    ):
        self.settings = settings
        self.helpdesk_client = helpdesk_client
        self.embedding_client = embedding_client
        self.qdrant_repo = qdrant_repo
        self._thread_filter_patterns = self._compile_thread_filters(settings.thread_filter_patterns)

    async def run(self, state: SyncState, request: SyncRequest) -> SyncResult:
        filters = request.filters or SyncFilters()
        page_limit = request.page_limit or self.settings.sync_page_size
        chunk_size = self.settings.sync_chunk_size

        await state.init()

        ticket_since = filters.time_modified_after or await state.get_checkpoint("tickets")
        update_since = filters.time_modified_after or await state.get_checkpoint("updates")

        ticket_result = await self._sync_tickets(filters, ticket_since, page_limit, chunk_size, request.dry_run)
        update_result = await self._sync_updates(filters, update_since, page_limit, chunk_size, request.dry_run)

        if not request.dry_run:
            if ticket_result:
                await state.set_checkpoint("tickets", ticket_result)
            if update_result:
                await state.set_checkpoint("updates", update_result)

        return SyncResult(
            tickets_fetched=self._ticket_count,
            updates_fetched=self._update_count,
            points_upserted=self._points_upserted,
            points_marked_hidden=self._points_hidden,
            last_ticket_modified=ticket_result,
            last_update_modified=update_result,
        )

    async def _sync_tickets(
        self,
        filters: SyncFilters,
        modified_after: Optional[datetime],
        page_limit: int,
        chunk_size: int,
        dry_run: bool,
    ) -> Optional[datetime]:
        self._ticket_count = 0
        self._points_upserted = 0
        self._points_hidden = 0
        last_modified: Optional[datetime] = modified_after
        page = 1
        while True:
            params: Dict[str, Any] = {}
            time_after = filters.time_modified_after or modified_after
            if time_after:
                params["time_modified_after"] = time_after.isoformat()
            if filters.time_modified_before:
                params["time_modified_before"] = filters.time_modified_before.isoformat()
            if filters.status:
                params["status"] = filters.status
            if filters.category:
                params["category"] = filters.category
            if filters.tag:
                params["tag"] = filters.tag
            if filters.ticket_id:
                params["ticket_id"] = filters.ticket_id

            pagination = {"page": page, "limit": page_limit}
            data = await self.helpdesk_client.fetch_tickets(params=params, pagination=pagination)
            tickets_raw = data.get("data") or []
            if not tickets_raw:
                break
            tickets = [TicketDTO.model_validate(t) for t in tickets_raw]
            points = []
            texts = []
            metas = []
            for ticket in tickets:
                if self._should_skip_details(ticket.details):
                    logger.info("Skipping ticket %s due to thread filter match", ticket.id)
                    continue
                self._ticket_count += 1
                chunks = chunk_text(ticket.details, chunk_size)
                details_hash = self._hash_text(ticket.details)
                for idx, chunk in enumerate(chunks):
                    chunk_body = chunk.text.strip()
                    text = merge_texts([ticket.topic, chunk_body])
                    payload = self._build_ticket_payload(
                        ticket,
                        chunk,
                        idx,
                        len(chunks),
                        details_hash,
                    )
                    texts.append(text)
                    metas.append(payload)
            if texts and not dry_run:
                embeddings = await self.embedding_client.embed_texts(texts)
                for payload, vector in zip(metas, embeddings):
                    payload_copy = dict(payload)
                    raw_id = payload_copy.get("id")
                    point_id = self._make_point_id(str(raw_id)) if raw_id is not None else None
                    if raw_id is not None:
                        payload_copy["logical_id"] = raw_id  # logiczne ID chunku używane wyłącznie w payloadzie
                        payload_copy.pop("id", None)
                    points.append(
                        qmodels.PointStruct(
                            id=point_id,
                            payload=payload_copy,
                            vector=vector,
                        )
                    )
                self.qdrant_repo.upsert_ticket_points(points)
                self._points_upserted += len(points)
            if tickets:
                max_mod = max(t.time_modified for t in tickets)
                last_modified = max_mod if (not last_modified or max_mod > last_modified) else last_modified
            pagination_info = data.get("pagination") or {}
            next_page = pagination_info.get("next_page")
            if not next_page:
                break
            page = next_page
        return last_modified

    async def _sync_updates(
        self,
        filters: SyncFilters,
        modified_after: Optional[datetime],
        page_limit: int,
        chunk_size: int,
        dry_run: bool,
    ) -> Optional[datetime]:
        self._update_count = 0
        last_modified: Optional[datetime] = modified_after
        page = 1
        while True:
            params: Dict[str, Any] = {}
            time_after = filters.time_modified_after or modified_after
            if time_after:
                params["time_modified_after"] = time_after.isoformat()
            if filters.time_modified_before:
                params["time_modified_before"] = filters.time_modified_before.isoformat()
            if filters.update_type:
                params["type"] = filters.update_type
            if filters.new_ticket_status:
                params["new_ticket_status"] = filters.new_ticket_status
            if filters.ticket_id:
                params["ticket_id"] = filters.ticket_id

            pagination = {"page": page, "limit": page_limit}
            data = await self.helpdesk_client.fetch_updates(params=params, pagination=pagination)
            updates_raw = data.get("data") or []
            if not updates_raw:
                break
            updates = [UpdateDTO.model_validate(u) for u in updates_raw]
            points = []
            texts = []
            metas = []
            for update in updates:
                if self._should_skip_details(update.details):
                    logger.info(
                        "Skipping update %s (ticket %s) due to thread filter match",
                        update.id,
                        update.ticket_id,
                    )
                    continue
                self._update_count += 1
                chunks = chunk_text(update.details, chunk_size)
                details_hash = self._hash_text(update.details)
                for idx, chunk in enumerate(chunks):
                    chunk_body = chunk.text.strip()
                    text = chunk_body
                    payload = self._build_update_payload(
                        update,
                        chunk,
                        idx,
                        len(chunks),
                        details_hash,
                    )
                    texts.append(text)
                    metas.append(payload)
                    if update.hidden:
                        self._points_hidden += 1
            if texts and not dry_run:
                embeddings = await self.embedding_client.embed_texts(texts)
                for payload, vector in zip(metas, embeddings):
                    payload_copy = dict(payload)
                    raw_id = payload_copy.get("id")
                    point_id = self._make_point_id(str(raw_id)) if raw_id is not None else None
                    if raw_id is not None:
                        payload_copy["logical_id"] = raw_id  # logiczne ID chunku używane wyłącznie w payloadzie
                        payload_copy.pop("id", None)
                    points.append(
                        qmodels.PointStruct(
                            id=point_id,
                            payload=payload_copy,
                            vector=vector,
                        )
                    )
                self.qdrant_repo.upsert_update_points(points)
                self._points_upserted += len(points)
            if updates:
                max_mod = max(u.time_modified for u in updates)
                last_modified = max_mod if (not last_modified or max_mod > last_modified) else last_modified
            pagination_info = data.get("pagination") or {}
            next_page = pagination_info.get("next_page")
            if not next_page:
                break
            page = next_page
        return last_modified

    def _make_point_id(self, key: str) -> str:
        return str(uuid5(self._POINT_ID_NAMESPACE, key))

    def _build_ticket_payload(
        self,
        ticket: TicketDTO,
        chunk: TextChunk,
        chunk_no: int,
        chunk_total: int,
        details_hash: str,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": f"ticket-{ticket.id}-{chunk_no}",  # lokalny identyfikator chunku przed zamianą na UUID
            "source": "ticket",  # źródło danych – zgłoszenie
            "ticket_id": ticket.id,  # ID zgłoszenia w Help Desk
            "update_id": None,  # brak ID aktualizacji dla chunków ticketu
            "status": ticket.status,  # aktualny status zgłoszenia
            "category": ticket.category,  # kategoria zgłoszenia
            "tags": ticket.tags,  # lista tagów przypisanych do zgłoszenia
            "related_tickets": ticket.related_tickets,  # powiązane zgłoszenia
            "topic": ticket.topic,  # temat / nagłówek zgłoszenia
            "detail_chunk": chunk.text,  # fragment oryginalnego tekstu ticketu
            "chunk_no": chunk_no,  # numer chunku w obrębie zgłoszenia (0-based)
            "chunk_total": chunk_total,  # liczba chunków dla tego zgłoszenia
            "chunk_start": chunk.start,  # offset początkowy w tekście `details`
            "chunk_end": chunk.end,  # offset końcowy w tekście `details`
            "sentence_start": chunk.sentence_start,  # indeks pierwszego zdania w chunku
            "sentence_end": chunk.sentence_end,  # indeks ostatniego zdania w chunku
            "details_hash": details_hash,  # hash całego `details` do weryfikacji spójności
            "time_created": ticket.time_created.isoformat(),  # ISO timestamp utworzenia zgłoszenia
            "time_created_unix": ticket.time_created.timestamp(),  # znacznik czasu utworzenia (float)
            "time_modified": ticket.time_modified.isoformat(),  # ISO timestamp modyfikacji
            "time_modified_unix": ticket.time_modified.timestamp(),  # znacznik czasu modyfikacji (float)
            "hidden": False,  # zgłoszenia nigdy nie są oznaczane jako ukryte
            "url_suffix": ticket.url_suffix,  # suffix potrzebny do złożenia URL w API
        }
        return payload

    def _build_update_payload(
        self,
        update: UpdateDTO,
        chunk: TextChunk,
        chunk_no: int,
        chunk_total: int,
        details_hash: str,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": f"update-{update.ticket_id}-{update.id}-{chunk_no}",  # lokalny identyfikator chunku aktualizacji
            "source": "update",  # źródło danych – aktualizacja ticketu
            "ticket_id": update.ticket_id,  # ID zgłoszenia, do którego należy aktualizacja
            "update_id": update.id,  # ID aktualizacji w Help Desk
            "status": update.new_ticket_status,  # status ticketu po aktualizacji
            "update_type": update.type,  # typ aktualizacji (np. komentarz, zmiana statusu)
            "user_role": update.user_role,  # rola użytkownika dodającego aktualizację
            "detail_chunk": chunk.text,  # fragment oryginalnego tekstu aktualizacji
            "chunk_no": chunk_no,  # numer chunku aktualizacji (0-based)
            "chunk_total": chunk_total,  # liczba chunków w tej aktualizacji
            "chunk_start": chunk.start,  # offset początkowy w tekście `details`
            "chunk_end": chunk.end,  # offset końcowy w tekście `details`
            "sentence_start": chunk.sentence_start,  # indeks pierwszego zdania chunku
            "sentence_end": chunk.sentence_end,  # indeks ostatniego zdania chunku
            "details_hash": details_hash,  # hash całego tekstu aktualizacji
            "time_created": update.time_created.isoformat(),  # ISO timestamp utworzenia aktualizacji
            "time_created_unix": update.time_created.timestamp(),  # znacznik czasu utworzenia (float)
            "time_modified": update.time_modified.isoformat(),  # ISO timestamp modyfikacji aktualizacji
            "time_modified_unix": update.time_modified.timestamp(),  # znacznik czasu modyfikacji (float)
            "hidden": update.hidden,  # czy aktualizacja jest ukryta w Help Desk
            "url_suffix": update.url_suffix,  # suffix do złożenia pełnego linku w API
        }
        return payload

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _compile_thread_filters(patterns: Sequence[str]) -> List[Pattern[str]]:
        compiled: List[Pattern[str]] = []
        for raw in patterns:
            pattern = raw.strip()
            if not pattern:
                continue
            try:
                compiled.append(re.compile(pattern))
            except re.error as exc:
                raise ValueError(f"Invalid thread filter regex '{raw}': {exc}") from exc
        return compiled

    def _should_skip_details(self, details: str) -> bool:
        if not details or not self._thread_filter_patterns:
            return False
        for pattern in self._thread_filter_patterns:
            if pattern.search(details):
                logger.debug("Details filtered out by pattern '%s'", pattern.pattern)
                return True
        return False
