from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.v1.routes_info import router as info_router
from app.api.v1.routes_query import router as query_router
from app.api.v1.routes_sync import router as sync_router
from app.clients.embeddings import EmbeddingClient
from app.clients.helpdesk import HelpDeskClient
from app.clients.qdrant import QdrantRepository
from app.core.config import load_settings
from app.core.logging import setup_logging
from app.version import __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    setup_logging(settings.log_level)
    app.state.settings = settings

    async with httpx.AsyncClient() as helpdesk_http, httpx.AsyncClient() as embedding_http:
        helpdesk_client = HelpDeskClient(settings, helpdesk_http)
        embedding_client = EmbeddingClient(settings, embedding_http)
        qdrant_repo = QdrantRepository(settings)

        embedding_dim = settings.embedding_dim
        if embedding_dim is None:
            embedding_dim = await embedding_client.probe_dimension()
        embedding_client.resolved_dimension = embedding_dim  # type: ignore[attr-defined]

        qdrant_repo.ensure_collections(vector_size=embedding_dim)

        app.state.helpdesk_client = helpdesk_client
        app.state.embedding_client = embedding_client
        app.state.qdrant = qdrant_repo
        yield


app = FastAPI(
    title="hd_ke",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.include_router(info_router, prefix="/api/v1")
app.include_router(sync_router, prefix="/api/v1")
app.include_router(query_router, prefix="/api/v1")
