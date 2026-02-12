# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

from fastapi import APIRouter, Depends

from app.api.deps import get_embedding_client, get_qdrant, get_settings
from app.clients.embeddings import EmbeddingClient
from app.clients.qdrant import QdrantRepository
from app.core.config import Settings
from app.version import __version__

router = APIRouter()


@router.get(
    "/health",
    summary="Healthcheck",
    tags=["meta"],
    operation_id="health",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/info",
    summary="Basic tool info",
    tags=["meta"],
    operation_id="info",
)
async def info(
    settings: Settings = Depends(get_settings),
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
    qdrant: QdrantRepository = Depends(get_qdrant),
) -> dict[str, str | int | bool | None]:
    embedding_dim = getattr(embedding_client, "resolved_dimension", settings.embedding_dim)
    return {
        "name": "hd_ke",
        "version": __version__,
        "qdrant_ticket_collection": settings.qdrant_ticket_collection,
        "qdrant_update_collection": settings.qdrant_update_collection,
        "embedding_model": settings.embedding_model_name,
        "embedding_context_length": settings.embedding_context_length,
        "embedding_dim": embedding_dim,
        "helpdesk_api_base_url": str(settings.helpdesk_api_base_url),
    }
