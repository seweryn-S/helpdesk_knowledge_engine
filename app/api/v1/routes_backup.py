# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import get_qdrant, get_settings
from app.clients.qdrant import QdrantRepository
from app.core.config import Settings
from app.services.backup import BackupService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/admin/backup/export",
    summary="Export Qdrant collections and SQLite state to tgz",
    tags=["admin"],
)
async def export_backup(
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    qdrant: QdrantRepository = Depends(get_qdrant),
) -> FileResponse:
    service = BackupService(settings, qdrant)
    try:
        result = await service.export_backup()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Backup export failed")
        raise HTTPException(status_code=500, detail=f"Backup export failed: {exc}") from exc

    background_tasks.add_task(service.cleanup_workdir, result.workdir)
    return FileResponse(
        path=result.archive_path,
        media_type="application/gzip",
        filename=result.filename,
        background=background_tasks,
    )


@router.post(
    "/admin/backup/import",
    summary="Import Qdrant collections and SQLite state from tgz",
    tags=["admin"],
)
async def import_backup(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    qdrant: QdrantRepository = Depends(get_qdrant),
) -> dict:
    service = BackupService(settings, qdrant)
    try:
        result = await service.import_backup(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Backup import failed")
        raise HTTPException(status_code=500, detail="Backup import failed") from exc
    return {
        "collections_restored": result.collections_restored,
        "sqlite_restored_to": result.sqlite_restored_to,
    }
