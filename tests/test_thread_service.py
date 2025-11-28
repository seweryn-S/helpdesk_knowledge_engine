from datetime import datetime

from app.services.thread import ThreadService


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
