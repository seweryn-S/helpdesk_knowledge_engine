from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.deps import get_embedding_client, get_qdrant, get_settings
from app.clients.embeddings import EmbeddingClient
from app.clients.qdrant import QdrantRepository
from app.core.config import Settings
from app.models.schemas import (
    QueryRequest,
    QueryResponse,
    ThreadQueryResponse,
    TicketDetailsResponse,
    TicketThreadResponse,
)
from app.services.query import QueryService
from app.services.thread import ThreadService
from app.services.thread_query import ThreadQueryService

DEBUG_ONLY_FIELDS = {
    "chunk_no",
    "chunk_total",
    "chunk_start",
    "chunk_end",
    "sentence_start",
    "sentence_end",
    "details_hash",
    "url_suffix",
}

router = APIRouter()


def _sanitize_response(model: QueryResponse | ThreadQueryResponse) -> JSONResponse:
    response_payload = model.model_dump(mode="json")
    for result in response_payload.get("results", []):
        if isinstance(result, dict):
            for field in DEBUG_ONLY_FIELDS:
                result.pop(field, None)
    return JSONResponse(content=response_payload)


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Search Help Desk knowledge base with semantic similarity",
    description="Returns top passages (tickets and updates) with metadata and citations. Supports filters by status, category, tags, roles, update types, and time range.",
    tags=["query"],
)
async def query(
    body: QueryRequest,
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
    qdrant: QdrantRepository = Depends(get_qdrant),
    settings: Settings = Depends(get_settings),
) -> QueryResponse:
    service = QueryService(embedding_client, qdrant, settings)
    response = await service.query(body)
    if body.debug:
        return response
    return _sanitize_response(response)


@router.post(
    "/query/threads",
    response_model=ThreadQueryResponse,
    summary="Search Help Desk knowledge base and return full threads",
    description="Returns top tickets as fully reconstructed threads (ticket details + updates) with metadata assembled from Qdrant payloads.",
    tags=["query"],
)
async def query_threads(
    body: QueryRequest,
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
    qdrant: QdrantRepository = Depends(get_qdrant),
    settings: Settings = Depends(get_settings),
) -> ThreadQueryResponse:
    service = ThreadQueryService(embedding_client, qdrant, settings)
    response = await service.query_threads(body)
    if body.debug:
        return response
    return _sanitize_response(response)


@router.get(
    "/tickets/{ticket_id}/details",
    response_model=TicketDetailsResponse,
    summary="Recreate full ticket details from Qdrant chunks",
    description="Uses Qdrant payload metadata to join all ticket chunks back into the original Help Desk ticket body.",
    tags=["query"],
)
async def ticket_details(
    ticket_id: int,
    qdrant: QdrantRepository = Depends(get_qdrant),
) -> TicketDetailsResponse:
    service = ThreadService(qdrant)
    try:
        return service.get_ticket_details(ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/tickets/{ticket_id}/thread",
    response_model=TicketThreadResponse,
    summary="Return full ticket thread (details + updates)",
    description="Fetches ticket chunks and all update chunks for a ticket, rebuilds the original text for each entry, and returns a concatenated thread.",
    tags=["query"],
)
async def ticket_thread(
    ticket_id: int,
    qdrant: QdrantRepository = Depends(get_qdrant),
) -> TicketThreadResponse:
    service = ThreadService(qdrant)
    try:
        return service.get_ticket_thread(ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
