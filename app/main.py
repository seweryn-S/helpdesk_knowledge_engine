from __future__ import annotations

import asyncio
import copy
import logging
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.v1.routes_backup import router as backup_router
from app.api.v1.routes_info import router as info_router
from app.api.v1.routes_query import router as query_router
from app.api.v1.routes_sync import router as sync_router
from app.clients.embeddings import EmbeddingClient
from app.clients.helpdesk import HelpDeskClient
from app.clients.qdrant import QdrantRepository
from app.core.config import load_settings
from app.core.logging import setup_logging
from app.version import __version__

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    setup_logging(settings.log_level)
    app.state.settings = settings

    refresh_task: asyncio.Task | None = None

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
        app.state.helpdesk_categories = list(settings.helpdesk_default_categories or [])

        def _rebuild_openapi_schema() -> None:
            try:
                _invalidate_openapi_cache()
                custom_openapi()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to rebuild OpenAPI schema: %s", exc)

        async def _refresh_categories_once() -> None:
            try:
                raw_categories = await helpdesk_client.fetch_categories()
                names = [
                    str(item.get("name"))
                    for item in raw_categories
                    if isinstance(item, dict) and item.get("name")
                ]
                if names:
                    app.state.helpdesk_categories = names
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to refresh Help Desk categories for OpenAPI: %s", exc)

        async def _refresh_categories_loop() -> None:
            interval_hours = settings.helpdesk_categories_refresh_hours or 0.0
            if interval_hours <= 0:
                return
            interval_seconds = max(interval_hours * 3600.0, 60.0)
            while True:
                await _refresh_categories_once()
                _rebuild_openapi_schema()
                await asyncio.sleep(interval_seconds)

        # Best-effort initial refresh; non-fatal on failure.
        await _refresh_categories_once()
        _rebuild_openapi_schema()

        interval_hours = settings.helpdesk_categories_refresh_hours or 0.0
        if interval_hours > 0:
            refresh_task = asyncio.create_task(_refresh_categories_loop())

        try:
            yield
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await refresh_task


app = FastAPI(
    title="hd_ke",
    version=__version__,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)

app.include_router(info_router, prefix="/api/v1")
app.include_router(sync_router, prefix="/api/v1")
app.include_router(query_router, prefix="/api/v1")
app.include_router(backup_router, prefix="/api/v1")


_openapi_cache: dict[bool, dict] = {}


def _invalidate_openapi_cache() -> None:
    _openapi_cache.clear()


def _inject_categories(schema: dict) -> dict:
    categories = getattr(getattr(app, "state", object()), "helpdesk_categories", None)
    if categories:
        try:
            components = schema.get("components", {})
            schemas = components.get("schemas", {})
            query_request = schemas.get("QueryRequest")
            if isinstance(query_request, dict):
                properties = query_request.get("properties", {})
                category_prop = properties.get("category")
                if isinstance(category_prop, dict):
                    array_schemas: list[dict] = []
                    items = category_prop.get("items")
                    if isinstance(items, dict):
                        array_schemas.append(category_prop)
                    for option in category_prop.get("anyOf", []):
                        if isinstance(option, dict) and option.get("type") == "array":
                            array_schemas.append(option)
                    for schema_option in array_schemas:
                        option_items = schema_option.get("items")
                        if isinstance(option_items, dict) and option_items.get("type") == "string":
                            option_items["enum"] = list(categories)
                            desc = category_prop.get("description") or "Allowed ticket categories."
                            suffix = f" Allowed values: {', '.join(categories)}."
                            if "Allowed values:" not in desc:
                                category_prop["description"] = desc.rstrip() + suffix
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to inject Help Desk categories into OpenAPI schema: %s", exc)
    return schema


def _filter_admin_paths(schema: dict) -> dict:
    filtered = copy.deepcopy(schema)
    paths = filtered.get("paths", {})
    to_remove: list[str] = []
    for path, methods in list(paths.items()):
        if not isinstance(methods, dict):
            continue
        for method, details in list(methods.items()):
            if not isinstance(details, dict):
                continue
            tags = details.get("tags") or []
            if str(path).startswith("/api/v1/admin") or any(
                str(tag).lower() == "admin" for tag in tags if tag is not None
            ):
                methods.pop(method, None)
        if not methods:
            to_remove.append(path)
    for path in to_remove:
        paths.pop(path, None)
    return filtered


def build_openapi_schema(include_admin: bool = False) -> dict:
    if include_admin in _openapi_cache:
        return _openapi_cache[include_admin]

    schema = get_openapi(
        title="hd_ke",
        version=__version__,
        description="Help Desk knowledge engine (RAG) for Help Desk tickets and updates.",
        routes=app.routes,
    )
    schema = _inject_categories(schema)
    if not include_admin:
        schema = _filter_admin_paths(schema)

    _openapi_cache[include_admin] = schema
    return schema


def custom_openapi() -> dict:
    return build_openapi_schema(include_admin=False)


app.openapi = custom_openapi


@app.get("/openapi-admin.json", include_in_schema=False)
async def openapi_admin() -> JSONResponse:
    return JSONResponse(build_openapi_schema(include_admin=True))


@app.get("/docs", include_in_schema=False)
async def swagger_ui() -> JSONResponse:
    return get_swagger_ui_html(
        openapi_url="/openapi-admin.json",
        title="hd_ke docs",
    )
