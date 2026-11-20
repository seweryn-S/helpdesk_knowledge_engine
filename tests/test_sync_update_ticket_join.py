# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import SyncRequest
from app.services.sync import SyncService, SyncState


class FakeHelpDeskClient:
    def __init__(self) -> None:
        self.tickets_calls: List[Dict[str, Any]] = []
        self.updates_calls: List[Tuple[Dict[str, Any], Dict[str, int]]] = []

    async def fetch_tickets(self, params: Dict[str, Any], pagination: Dict[str, int]) -> Dict[str, Any]:
        self.tickets_calls.append(params)
        tickets = [
            {
                "id": ticket_id,
                "url_suffix": f"/tickets/{ticket_id}",
                "topic": f"Ticket {ticket_id}",
                "details": "Ticket details",
                "status": "Open",
                "category": category,
                "time_created": "2024-01-01T00:00:00+00:00",
                "time_modified": "2024-01-04T00:00:00+00:00",
            }
            for ticket_id, category in [(1, "cat-keep"), (2, "cat-skip")]
        ]
        return {
            "data": [
                ticket for ticket in tickets
                if self._in_date_range(ticket, params)
                and (not params.get("category") or ticket["category"] in params["category"])
            ],
            "pagination": {"next_page": None},
        }

    @staticmethod
    def _in_date_range(record: Dict[str, Any], params: Dict[str, Any]) -> bool:
        modified_at = datetime.fromisoformat(record["time_modified"])
        after = params.get("time_modified_after")
        before = params.get("time_modified_before")
        return (not after or modified_at >= datetime.fromisoformat(after)) and (
            not before or modified_at <= datetime.fromisoformat(before)
        )

    async def fetch_updates(self, params: Dict[str, Any], pagination: Dict[str, int]) -> Dict[str, Any]:
        self.updates_calls.append((params, pagination))
        allowed_ticket_ids = params.get("ticket_id")

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
        outside_range = {**_update(103, 1), "time_modified": "2024-01-04T00:00:00+00:00"}
        updates = [_update(101, 1), _update(102, 2), outside_range]
        return {
            "data": [
                update for update in updates
                if self._in_date_range(update, params)
                and (allowed_ticket_ids is None or update["ticket_id"] in allowed_ticket_ids)
            ],
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
@pytest.mark.parametrize(
    ("filters", "expected_update_ids", "expected_ticket_ids"),
    [
        pytest.param({}, {101, 102}, None, id="date-only"),
        pytest.param({"category": ["cat-keep"]}, {101}, [1], id="category"),
        pytest.param({"ticket_id": [2]}, {102}, [2], id="ticket-id"),
        pytest.param({"category": ["absent"]}, set(), [], id="no-parent-matches"),
    ],
)
async def test_sync_updates_date_is_independent_of_parent_date(
    filters: Dict[str, Any],
    expected_update_ids: set[int],
    expected_ticket_ids: list[int] | None,
) -> None:
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
    request = SyncRequest(
        time_modified_after=datetime(2024, 1, 3, tzinfo=timezone.utc),
        time_modified_before=datetime(2024, 1, 3, 23, 59, 59, tzinfo=timezone.utc),
        **filters,
    )

    result = await service.run(AsyncMock(spec=SyncState), request)

    assert result.tickets_fetched == 0
    assert result.updates_fetched == len(expected_update_ids)
    assert {point.payload.get("update_id") for point in qdrant.update_points} == expected_update_ids

    date_params = {
        "time_modified_after": "2024-01-03T00:00:00+00:00",
        "time_modified_before": "2024-01-03T23:59:59+00:00",
    }
    parent_filters = {name: value for name, value in filters.items() if name != "ticket_id"}
    expected_tickets_calls = [{**date_params, **parent_filters}]
    if parent_filters:
        expected_tickets_calls.append(parent_filters)
    assert helpdesk.tickets_calls == expected_tickets_calls

    expected_update_calls = []
    if expected_ticket_ids != []:
        update_params = date_params.copy()
        if expected_ticket_ids is not None:
            update_params["ticket_id"] = expected_ticket_ids
        expected_update_calls.append((update_params, {"page": 1, "limit": 100}))
    assert helpdesk.updates_calls == expected_update_calls
