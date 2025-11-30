import json
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse

from app.api.v1 import routes_query
from app.models.schemas import Passage, QueryRequest, QueryResponse, ThreadQueryResponse, ThreadSearchResult


class DummyQueryService:
    def __init__(self, embedding_client, qdrant, settings):
        self._response = QueryResponse(
            query="dummy",
            results=[
                Passage(
                    detail_chunk="chunk",
                    score=0.5,
                    source="ticket",
                    ticket_id=1,
                    update_id=None,
                    status="Open",
                    category=None,
                    tags=[],
                    user_role=None,
                    update_type=None,
                    time_created=None,
                    time_modified=None,
                    url_suffix="id=1",
                    url="https://example.com/ticket?id=1",
                    chunk_no=0,
                    chunk_total=2,
                    chunk_start=0,
                    chunk_end=10,
                    sentence_start=0,
                    sentence_end=1,
                    details_hash="abc",
                )
            ],
        )

    async def query(self, request: QueryRequest) -> QueryResponse:
        return QueryResponse(query=request.query, results=self._response.results)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(helpdesk_ticket_url_prefix="https://example.com/ticket?")


class DummyThreadQueryService:
    def __init__(self, embedding_client, qdrant, settings):
        self._response = ThreadQueryResponse(
            query="dummy",
            results=[
                ThreadSearchResult(
                    ticket_id=1,
                    topic="Topic",
                    status="Open",
                    tags=["vip"],
                    url_suffix="id=1",
                    url="https://example.com/ticket?id=1",
                    time_created=None,
                    time_modified=None,
                    score=0.7,
                    thread_text="user: hello",
                )
            ],
        )

    async def query_threads(self, request: QueryRequest) -> ThreadQueryResponse:
        return ThreadQueryResponse(query=request.query, results=self._response.results)


@pytest.mark.asyncio
async def test_query_endpoint_hides_debug_fields(monkeypatch):
    monkeypatch.setattr(routes_query, "QueryService", DummyQueryService)
    body = QueryRequest(query="hello")

    response = await routes_query.query(
        body=body,
        embedding_client=None,
        qdrant=None,
        settings=_settings(),
        debug=False,
    )

    assert isinstance(response, JSONResponse)
    payload = json.loads(response.body.decode())
    assert payload["query"] == "hello"
    result = payload["results"][0]
    assert "chunk_no" not in result
    assert "url_suffix" not in result
    assert result["detail_chunk"] == "chunk"
    assert result["url"] == "https://example.com/ticket?id=1"


@pytest.mark.asyncio
async def test_query_endpoint_returns_full_payload_in_debug(monkeypatch):
    monkeypatch.setattr(routes_query, "QueryService", DummyQueryService)
    body = QueryRequest(query="hello")

    response = await routes_query.query(
        body=body,
        embedding_client=None,
        qdrant=None,
        settings=_settings(),
        debug=True,
    )

    assert isinstance(response, QueryResponse)
    assert response.query == "hello"
    result = response.results[0]
    assert result.chunk_no == 0
    assert result.chunk_total == 2
    assert result.details_hash == "abc"
    assert result.url_suffix == "id=1"


@pytest.mark.asyncio
async def test_query_threads_endpoint_hides_debug_fields(monkeypatch):
    monkeypatch.setattr(routes_query, "ThreadQueryService", DummyThreadQueryService)
    body = QueryRequest(query="threads")

    response = await routes_query.query_threads(
        body=body,
        embedding_client=None,
        qdrant=None,
        settings=_settings(),
        debug=False,
    )

    assert isinstance(response, JSONResponse)
    payload = json.loads(response.body.decode())
    result = payload["results"][0]
    assert "url_suffix" not in result
    assert result["url"] == "https://example.com/ticket?id=1"


@pytest.mark.asyncio
async def test_query_threads_endpoint_returns_full_payload_in_debug(monkeypatch):
    monkeypatch.setattr(routes_query, "ThreadQueryService", DummyThreadQueryService)
    body = QueryRequest(query="threads")

    response = await routes_query.query_threads(
        body=body,
        embedding_client=None,
        qdrant=None,
        settings=_settings(),
        debug=True,
    )

    assert isinstance(response, ThreadQueryResponse)
    result = response.results[0]
    assert result.url_suffix == "id=1"
