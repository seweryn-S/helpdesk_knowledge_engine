import pytest
from datetime import datetime
from types import SimpleNamespace

from app.models.schemas import QueryFilters, QueryRequest
from app.services.thread_query import ThreadQueryService


class DummyEmbeddingClient:
    async def embed_texts(self, texts):
        return [[0.1] * 3 for _ in texts]


class DummyPoint:
    def __init__(self, payload, score):
        self.payload = payload
        self.score = score


class DummyRecord:
    def __init__(self, payload):
        self.payload = payload


class DummyQdrantRepo:
    def __init__(self, ticket_points, update_points, ticket_records, update_records):
        self._ticket_points = ticket_points
        self._update_points = update_points
        self._ticket_records = ticket_records
        self._update_records = update_records

    def search_tickets(self, vector, limit, query_filter=None):
        return self._ticket_points

    def search_updates(self, vector, limit, query_filter=None):
        return self._update_points

    def scroll_tickets(self, query_filter, limit=128, with_vectors=False):
        return self._ticket_records

    def scroll_updates(self, query_filter, limit=128, with_vectors=False):
        return self._update_records


def _default_settings():
    return SimpleNamespace(helpdesk_ticket_url_prefix="https://example.com/ticket?")


@pytest.mark.asyncio
async def test_query_threads_builds_full_thread_with_role_and_metadata():
    ticket_chunks = [
        {
            "ticket_id": 1,
            "detail_chunk": "Hello ",
            "chunk_no": 0,
            "chunk_start": 0,
            "topic": "Topic",
            "status": "open",
            "tags": ["vip"],
            "url_suffix": "id=1",
            "time_created": datetime(2024, 1, 1, 12, 0).isoformat(),
            "time_modified": datetime(2024, 1, 2, 8, 0).isoformat(),
        },
        {
            "ticket_id": 1,
            "detail_chunk": "world",
            "chunk_no": 1,
            "chunk_start": 6,
            "topic": "Topic",
            "status": "open",
            "tags": ["vip"],
            "url_suffix": "id=1",
            "time_created": datetime(2024, 1, 1, 12, 0).isoformat(),
            "time_modified": datetime(2024, 1, 2, 8, 0).isoformat(),
        },
    ]
    update_chunks = [
        {
            "ticket_id": 1,
            "update_id": 10,
            "detail_chunk": "first update",
            "chunk_no": 0,
            "chunk_start": 0,
            "user_role": "agent",
            "time_created": datetime(2024, 1, 3, 9, 0).isoformat(),
            "time_modified": datetime(2024, 1, 4, 10, 0).isoformat(),
        },
        {
            "ticket_id": 1,
            "update_id": 11,
            "detail_chunk": "second update",
            "chunk_no": 0,
            "chunk_start": 0,
            "user_role": "user",
            "time_created": datetime(2024, 1, 5, 8, 0).isoformat(),
        },
    ]

    repo = DummyQdrantRepo(
        ticket_points=[DummyPoint(ticket_chunks[0], 0.9)],
        update_points=[DummyPoint(update_chunks[0], 0.8)],
        ticket_records=[DummyRecord(payload) for payload in ticket_chunks],
        update_records=[DummyRecord(payload) for payload in update_chunks],
    )
    service = ThreadQueryService(DummyEmbeddingClient(), repo, _default_settings())

    response = await service.query_threads(QueryRequest(query="hello", limit=3))

    assert len(response.results) == 1
    result = response.results[0]
    assert result.ticket_id == 1
    assert result.status == "open"
    assert result.tags == ["vip"]
    assert result.url_suffix == "id=1"
    assert result.time_created == datetime(2024, 1, 1, 12, 0)
    assert result.time_modified == datetime(2024, 1, 5, 8, 0)
    assert "Hello world" in result.thread_text
    assert "agent: first update" in result.thread_text
    assert "user: second update" in result.thread_text


@pytest.mark.asyncio
async def test_query_threads_respects_hide_hidden():
    ticket_chunks = [
        {
            "ticket_id": 2,
            "detail_chunk": "Base",
            "chunk_no": 0,
            "chunk_start": 0,
            "topic": "Topic",
            "status": "open",
            "tags": [],
            "url_suffix": "id=2",
            "time_created": datetime(2024, 2, 1, 10, 0).isoformat(),
            "time_modified": datetime(2024, 2, 2, 10, 0).isoformat(),
        }
    ]
    update_chunks = [
        {
            "ticket_id": 2,
            "update_id": 21,
            "detail_chunk": "visible",
            "chunk_no": 0,
            "chunk_start": 0,
            "user_role": "agent",
            "time_created": datetime(2024, 2, 3, 9, 0).isoformat(),
            "hidden": False,
        },
        {
            "ticket_id": 2,
            "update_id": 22,
            "detail_chunk": "hidden update",
            "chunk_no": 0,
            "chunk_start": 0,
            "user_role": "agent",
            "time_created": datetime(2024, 2, 4, 9, 0).isoformat(),
            "hidden": True,
        },
    ]

    repo = DummyQdrantRepo(
        ticket_points=[DummyPoint(ticket_chunks[0], 0.7)],
        update_points=[],
        ticket_records=[DummyRecord(payload) for payload in ticket_chunks],
        update_records=[DummyRecord(payload) for payload in update_chunks],
    )
    service = ThreadQueryService(DummyEmbeddingClient(), repo, _default_settings())

    response = await service.query_threads(
        QueryRequest(query="test", limit=1, filters=QueryFilters(hide_hidden=True))
    )

    assert len(response.results) == 1
    text = response.results[0].thread_text
    assert "visible" in text
    assert "hidden update" not in text
