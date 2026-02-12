# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import UploadFile
from qdrant_client import QdrantClient
from qdrant_client.http.models import SnapshotDescription

from app.clients.qdrant import QdrantRepository
from app.core.config import Settings
from app.version import __version__

logger = logging.getLogger(__name__)


@dataclass
class BackupExportResult:
    archive_path: Path
    workdir: Path
    filename: str
    metadata: Dict[str, object]


@dataclass
class BackupImportResult:
    collections_restored: List[str]
    sqlite_restored_to: str


class BackupService:
    def __init__(self, settings: Settings, qdrant_repo: QdrantRepository):
        self.settings = settings
        self.qdrant_repo = qdrant_repo
        self.qdrant_client: QdrantClient = qdrant_repo.client
        self._collections = [
            settings.qdrant_ticket_collection,
            settings.qdrant_update_collection,
        ]

    async def export_backup(self) -> BackupExportResult:
        workdir = self._make_workdir()
        payload_root = workdir / "payload"
        payload_root.mkdir(parents=True, exist_ok=True)

        snapshots_dir = payload_root / "qdrant"
        sqlite_dir = payload_root / "sqlite"
        metadata_path = payload_root / "metadata.json"

        snapshot_entries: Dict[str, Dict[str, object]] = {}
        created_snapshots: list[tuple[str, str]] = []

        try:
            for collection in self._collections:
                desc = await asyncio.to_thread(self._create_snapshot, collection)
                created_snapshots.append((collection, desc.name))
                snapshot_file = snapshots_dir / collection / desc.name
                snapshot_file.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(self._download_snapshot, collection, desc.name, snapshot_file)
                snapshot_entries[collection] = {
                    "snapshot_name": desc.name,
                    "snapshot_path": str(snapshot_file.relative_to(payload_root)),
                    "size": desc.size,
                    "checksum": desc.checksum,
                }

            sqlite_dir.mkdir(parents=True, exist_ok=True)
            sqlite_src = Path(self.settings.sync_checkpoint_path)
            sqlite_target = sqlite_dir / sqlite_src.name
            if sqlite_src.exists():
                await asyncio.to_thread(shutil.copy2, sqlite_src, sqlite_target)
            else:
                sqlite_target.touch()

            metadata = {
                "version": __version__,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "qdrant_host": self._rest_uri,
                "collections": snapshot_entries,
                "sqlite": {
                    "path": str(sqlite_target.relative_to(payload_root)),
                    "original_path": self.settings.sync_checkpoint_path,
                },
            }
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

            archive_name = f"hd_ke_backup_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.tgz"
            archive_path = workdir / archive_name
            await asyncio.to_thread(self._build_archive, payload_root, archive_path)

            return BackupExportResult(
                archive_path=archive_path,
                workdir=workdir,
                filename=archive_name,
                metadata=metadata,
            )
        finally:
            for collection, name in created_snapshots:
                try:
                    self.qdrant_client.delete_snapshot(collection_name=collection, snapshot_name=name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to delete temporary snapshot %s/%s: %s", collection, name, exc)

    async def import_backup(self, upload: UploadFile) -> BackupImportResult:
        workdir = Path(tempfile.mkdtemp(prefix="hdke-import-"))
        archive_path = workdir / (upload.filename or "backup.tgz")
        extract_dir = workdir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            await asyncio.to_thread(self._save_upload, upload, archive_path)
            await asyncio.to_thread(self._extract_archive, archive_path, extract_dir)
            metadata = self._load_metadata(extract_dir / "metadata.json")

            collections_meta = metadata.get("collections") or {}
            missing = [c for c in self._collections if c not in collections_meta]
            if missing:
                raise ValueError(f"Missing required collections in backup: {', '.join(missing)}")

            restored: List[str] = []
            for collection in self._collections:
                info = collections_meta.get(collection) or {}
                snapshot_rel = info.get("snapshot_path")
                if not snapshot_rel:
                    raise ValueError(f"Snapshot path missing for collection '{collection}' in metadata")
                snapshot_path = extract_dir / snapshot_rel
                self._ensure_inside(snapshot_path, extract_dir)
                if not snapshot_path.exists():
                    raise ValueError(f"Snapshot file not found for collection '{collection}'")

                checksum = info.get("checksum")
                await asyncio.to_thread(
                    self._recover_snapshot,
                    collection,
                    snapshot_path,
                    checksum,
                )
                restored.append(collection)

            sqlite_meta = metadata.get("sqlite") or {}
            sqlite_rel = sqlite_meta.get("path")
            if not sqlite_rel:
                raise ValueError("SQLite path missing in backup metadata")
            sqlite_src = extract_dir / sqlite_rel
            self._ensure_inside(sqlite_src, extract_dir)
            if not sqlite_src.exists():
                raise ValueError("SQLite database file missing in backup")

            await asyncio.to_thread(self._restore_sqlite, sqlite_src)

            return BackupImportResult(
                collections_restored=restored,
                sqlite_restored_to=self.settings.sync_checkpoint_path,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @property
    def _rest_uri(self) -> str:
        return getattr(self.qdrant_client._client, "rest_uri", "").rstrip("/")

    def _make_workdir(self) -> Path:
        base_dir = Path(self.settings.hd_ke_data_dir or tempfile.gettempdir())
        base_dir.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="hdke-backup-", dir=str(base_dir)))

    def _create_snapshot(self, collection_name: str) -> SnapshotDescription:
        snapshot = self.qdrant_client.create_snapshot(collection_name=collection_name, wait=True)
        if not snapshot or not snapshot.name:
            raise RuntimeError(f"Failed to create snapshot for collection '{collection_name}'")
        return snapshot

    def _download_snapshot(self, collection_name: str, snapshot_name: str, destination: Path) -> None:
        if not self._rest_uri:
            raise RuntimeError("Qdrant REST URI unavailable for snapshot download")

        url = f"{self._rest_uri}/collections/{collection_name}/snapshots/{snapshot_name}"
        headers = dict(getattr(self.qdrant_client._client, "_rest_headers", {}) or {})

        client = getattr(self.qdrant_client._client.openapi_client.client, "_client", None)
        if not isinstance(client, httpx.Client):
            rest_args = getattr(self.qdrant_client._client, "_rest_args", {}) or {}
            client_kwargs = {
                "headers": headers,
                "timeout": rest_args.get("timeout"),
                "verify": rest_args.get("verify"),
                "auth": rest_args.get("auth"),
                "http2": rest_args.get("http2"),
            }
            client = httpx.Client(**{k: v for k, v in client_kwargs.items() if v is not None})

        with client.stream("GET", url, headers=headers) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"Failed to download snapshot '{snapshot_name}' for collection '{collection_name}': {exc}"
                ) from exc

            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as file_handle:
                for chunk in response.iter_bytes():
                    file_handle.write(chunk)

    def _build_archive(self, payload_root: Path, archive_path: Path) -> None:
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(payload_root, arcname=".")

    def _save_upload(self, upload: UploadFile, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        upload.file.seek(0)
        with destination.open("wb") as dest:
            shutil.copyfileobj(upload.file, dest)

    def _extract_archive(self, archive_path: Path, target_dir: Path) -> None:
        with tarfile.open(archive_path, "r:*") as tar:
            self._safe_extract(tar, target_dir)

    def _safe_extract(self, tar: tarfile.TarFile, path: Path) -> None:
        for member in tar.getmembers():
            member_path = path / member.name
            if not self._is_within_directory(path, member_path):
                raise ValueError("Unsafe path detected in archive")
            if member.issym() or member.islnk():
                raise ValueError("Symlinks are not allowed in backup archive")
        tar.extractall(path)

    @staticmethod
    def _is_within_directory(directory: Path, target: Path) -> bool:
        abs_directory = directory.resolve()
        abs_target = target.resolve()
        return os.path.commonpath([abs_directory]) == os.path.commonpath([abs_directory, abs_target])

    def _load_metadata(self, metadata_path: Path) -> Dict[str, object]:
        if not metadata_path.exists():
            raise ValueError("Backup metadata.json not found")
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Backup metadata.json is invalid JSON") from exc

    def _recover_snapshot(self, collection: str, snapshot_path: Path, checksum: Optional[str]) -> None:
        api = self.qdrant_client._client.openapi_client.snapshots_api
        with snapshot_path.open("rb") as snapshot_file:
            response = api.recover_from_uploaded_snapshot(
                collection_name=collection,
                snapshot=snapshot_file,
                checksum=checksum,
                wait=True,
            )
        if not getattr(response, "result", False):
            raise RuntimeError(f"Failed to restore collection '{collection}' from snapshot")

    def _ensure_inside(self, candidate: Path, root: Path) -> None:
        if not self._is_within_directory(root, candidate):
            raise ValueError(f"Path '{candidate}' escapes extraction root")

    def _restore_sqlite(self, source: Path) -> None:
        target = Path(self.settings.sync_checkpoint_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.with_suffix(target.suffix + ".tmp")
        shutil.copy2(source, temp_target)
        os.replace(temp_target, target)

    @staticmethod
    def cleanup_workdir(path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)
