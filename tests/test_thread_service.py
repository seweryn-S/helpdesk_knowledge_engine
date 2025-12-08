from datetime import datetime

from app.services.thread import ThreadService


class DummyRecord:
    def __init__(self, payload):
        self.payload = payload


class DummyQdrantRepo:
    def __init__(self, ticket_payloads, update_payloads):
        self._ticket_payloads = ticket_payloads
        self._update_payloads = update_payloads

    def scroll_tickets(self, query_filter, limit=128, with_vectors=False):
        return [DummyRecord(payload) for payload in self._ticket_payloads]

    def scroll_updates(self, query_filter, limit=128, with_vectors=False):
        return [DummyRecord(payload) for payload in self._update_payloads]


def test_build_ticket_details_recreates_text():
    service = ThreadService(qdrant_repo=None)  # type: ignore[arg-type]
    payloads = [
        {"chunk_no": 1, "chunk_start": 4, "detail_chunk": "bar", "topic": "Topic", "details_hash": "hash"},
        {"chunk_no": 0, "chunk_start": 0, "detail_chunk": "foo", "topic": "Topic", "details_hash": "hash"},
    ]

    result = service._build_ticket_details(ticket_id=123, payloads=payloads)

    assert result.ticket_id == 123
    assert result.details == "foobar"
    assert result.topic == "Topic"
    assert len(result.chunks) == 2
    assert result.chunks[0].chunk_no == 0


def test_build_update_entries_orders_by_time():
    service = ThreadService(qdrant_repo=None)  # type: ignore[arg-type]
    payloads = [
        {
            "update_id": 2,
            "chunk_no": 0,
            "chunk_start": 0,
            "detail_chunk": "Second",
            "user_role": "agent",
            "status": "closed",
            "time_created": datetime(2024, 1, 2).isoformat(),
        },
        {
            "update_id": 1,
            "chunk_no": 0,
            "chunk_start": 0,
            "detail_chunk": "First",
            "user_role": "user",
            "status": "open",
            "time_created": datetime(2024, 1, 1).isoformat(),
        },
    ]

    entries = service._build_update_entries(ticket_id=55, payloads=payloads)

    assert [entry.update_id for entry in entries] == [1, 2]
    assert entries[0].text == "First"
    assert entries[1].user_role == "agent"


def test_get_ticket_thread_concise_merges_metadata_and_text():
    ticket_payloads = [
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
    update_payloads = [
        {
            "ticket_id": 1,
            "update_id": 10,
            "detail_chunk": "first update",
            "chunk_no": 0,
            "chunk_start": 0,
            "user_role": "agent",
            "status": "pending",
            "time_created": datetime(2024, 1, 3, 9, 0).isoformat(),
        },
        {
            "ticket_id": 1,
            "update_id": 11,
            "detail_chunk": "second update",
            "chunk_no": 0,
            "chunk_start": 0,
            "user_role": "user",
            "status": "open",
            "time_created": datetime(2024, 1, 4, 7, 0).isoformat(),
            "time_modified": datetime(2024, 1, 4, 8, 0).isoformat(),
        },
    ]
    repo = DummyQdrantRepo(ticket_payloads=ticket_payloads, update_payloads=update_payloads)
    service = ThreadService(qdrant_repo=repo, ticket_url_prefix="https://example.com/ticket?")

    response = service.get_ticket_thread(ticket_id=1, concise=True)

    assert response.ticket_id == 1
    assert response.topic == "Topic"
    assert response.tags == ["vip"]
    assert str(response.url) == "https://example.com/ticket?id=1"
    assert response.time_created == datetime(2024, 1, 1, 12, 0)
    assert response.time_modified == datetime(2024, 1, 4, 8, 0)
    assert "Hello world" in response.thread_text
    assert "first update" in response.thread_text
    assert "second update" in response.thread_text
    assert response.thread_text.index("first update") < response.thread_text.index("second update")
