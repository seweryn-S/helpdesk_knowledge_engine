from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

from app.models.schemas import SyncRequest
from app.services.sync import SyncService


class FakeHelpDeskClient:
    def __init__(self) -> None:
        self.updates_calls: List[Tuple[Dict[str, Any], Dict[str, int]]] = []

    async def fetch_updates(self, params: Dict[str, Any], pagination: Dict[str, int]) -> Dict[str, Any]:
        self.updates_calls.append((params, pagination))
        allowed_ticket_ids = set(params.get("ticket_id") or [])

        def _update(update_id: int, ticket_id: int) -> Dict[str, Any]:
            return {
                "id": update_id,
                "ticket_id": ticket_id,
                "url_suffix": f"/tickets/{ticket_id}/updates/{update_id}",
                "type": "comment",
                "new_ticket_status": "Open",
                "details": f"Update {update_id} for ticket {ticket_id}",
                "user_role": "agent",
                "time_created": "2024-01-03T00:00:00+00:00",
                "time_modified": "2024-01-03T00:00:00+00:00",
                "hidden": False,
            }

        page = pagination.get("page", 1)
        if page != 1:
            return {"data": [], "pagination": {"next_page": None}}
        data = []
        if 1 in allowed_ticket_ids:
            data.append(_update(101, 1))
        if 2 in allowed_ticket_ids:
            data.append(_update(102, 2))
        return {
            "data": data,
            "pagination": {"next_page": None},
        }


class FakeEmbeddingClient:
    resolved_dimension = 3

    async def embed_texts(self, texts: List[str], mode: str = "document") -> List[List[float]]:
        return [[0.0, 0.0, 0.0] for _ in texts]


class FakeQdrantRepo:
    def __init__(self) -> None:
        self.update_points = []

    def upsert_update_points(self, points) -> None:
        self.update_points.extend(points)


class AlwaysSemantic:
    def is_semantic(self, _: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_sync_updates_filters_to_processed_ticket_ids_when_ticket_filters_present() -> None:
    settings = SimpleNamespace(
        sync_page_size=100,
        sync_chunk_size=512,
        thread_filter_patterns=[],
        embedding_dim=3,
    )
    helpdesk = FakeHelpDeskClient()
    qdrant = FakeQdrantRepo()
    service = SyncService(settings, helpdesk, FakeEmbeddingClient(), qdrant)
    service._text_content_filter = AlwaysSemantic()
    service._ticket_count = 0
    service._update_count = 0
    service._points_upserted = 0
    service._points_hidden = 0
    service._processed_ticket_ids = {1}

    request = SyncRequest(
        time_modified_after=datetime(2024, 1, 1, tzinfo=timezone.utc),
        time_modified_before=None,
        status=None,
        category=["cat-keep"],
        tag=None,
        ticket_id=None,
        update_type=None,
        new_ticket_status=None,
        dry_run=False,
        page_limit=100,
    )

    await service._sync_updates(
        request,
        modified_after=None,
        allowed_ticket_ids=[1],
        page_limit=100,
        chunk_size=512,
        dry_run=False,
    )

    assert qdrant.update_points, "expected update points to be upserted"
    assert {point.payload.get("ticket_id") for point in qdrant.update_points} == {1}
    assert helpdesk.updates_calls, "expected updates endpoint to be called"
    params, _ = helpdesk.updates_calls[0]
    assert params.get("ticket_id") == [1]
