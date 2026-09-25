#!/usr/bin/env python3
# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Re-embed all Qdrant points with a new embedding model.

Reads payload text from source collections, calls the new embedding API,
and writes vectors to new target collections. No Help Desk API calls are made.

Usage:
    python scripts/reembed.py --dry-run
    python scripts/reembed.py --batch-size 32
    python scripts/reembed.py --resume
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from qdrant_client import QdrantClient, models as qmodels

DEFAULT_CHECKPOINT_DB = "/var/lib/hd_ke/reembed_state.db"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
ENV_FILE_CANDIDATES = [
    "/etc/default/hd_ke",
    Path(__file__).resolve().parent.parent / "deploy" / "hd_ke.default",
]


def load_env_file(path: Path) -> Dict[str, str]:
    """Parse a /etc/default style env file into a dict."""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes if present
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def load_reembed_config() -> Dict[str, str]:
    """Load only the config keys needed for re-embedding.

    Priority: real env vars > /etc/default/hd_ke > deploy/hd_ke.default
    """
    # Start with environment
    cfg: Dict[str, str] = dict(os.environ)

    # Load from env file (first candidate that exists wins)
    for candidate in ENV_FILE_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            file_env = load_env_file(p)
            # Real env vars take precedence
            for k, v in file_env.items():
                if k not in os.environ or not os.environ[k]:
                    cfg[k] = v
            break

    # Validate only what we need
    required = ["QDRANT_HOST", "EMBEDDING_API_BASE_URL", "EMBEDDING_MODEL_NAME"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required config: {', '.join(missing)}. "
            f"Set them as env vars or in /etc/default/hd_ke."
        )

    return cfg


# ---------------------------------------------------------------------------
# Checkpoint / Resume support
# ---------------------------------------------------------------------------


class ReEmbedCheckpoint:
    """SQLite-based checkpoint for resumable re-embedding."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS reembed_checkpoints (
                collection TEXT PRIMARY KEY,
                last_point_id TEXT,
                processed_count INTEGER DEFAULT 0,
                total_count INTEGER DEFAULT 0,
                started_at TEXT,
                updated_at TEXT
            )"""
        )
        conn.commit()
        conn.close()

    def get(self, collection: str) -> Tuple[Optional[str], int]:
        """Return (last_point_id, processed_count) or (None, 0)."""
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT last_point_id, processed_count FROM reembed_checkpoints WHERE collection = ?",
            (collection,),
        ).fetchone()
        conn.close()
        if row:
            return row[0], row[1]
        return None, 0

    def save(self, collection: str, last_point_id: str, processed: int, total: int) -> None:
        now = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR REPLACE INTO reembed_checkpoints
               (collection, last_point_id, processed_count, total_count, started_at, updated_at)
               VALUES (?, ?, ?, ?, COALESCE(
                   (SELECT started_at FROM reembed_checkpoints WHERE collection = ?), ?
               ), ?)""",
            (collection, last_point_id, processed, total, collection, now, now),
        )
        conn.commit()
        conn.close()

    def reset(self, collection: str) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM reembed_checkpoints WHERE collection = ?", (collection,))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Progress display
# ---------------------------------------------------------------------------


class ProgressTracker:
    """Tracks and displays progress for a single collection re-embed."""

    def __init__(self, name: str, total: int):
        self.name = name
        self.total = total
        self.processed = 0
        self.embedded = 0
        self.zero_vectors = 0
        self.errors = 0
        self.start_time = time.monotonic()
        self._last_print = 0.0

    def advance(self, count: int, embedded: int = 0, zero: int = 0, errors: int = 0) -> None:
        self.processed += count
        self.embedded += embedded
        self.zero_vectors += zero
        self.errors += errors
        now = time.monotonic()
        if now - self._last_print >= 2.0 or self.processed >= self.total:
            self._print()
            self._last_print = now

    def _print(self) -> None:
        elapsed = time.monotonic() - self.start_time
        pct = (self.processed / self.total * 100) if self.total else 0.0
        rate = self.processed / elapsed if elapsed > 0 else 0
        remaining = (self.total - self.processed) / rate if rate > 0 else 0
        eta_str = f"{remaining:.0f}s" if remaining < 9999 else ">9999s"
        msg = (
            f"\r[{self.name}] {self.processed}/{self.total} ({pct:.1f}%) | "
            f"{rate:.1f} pts/s | ETA: {eta_str} | "
            f"embed={self.embedded} zero={self.zero_vectors} err={self.errors}   "
        )
        sys.stderr.write(msg)
        sys.stderr.flush()

    def finish(self) -> None:
        self._print()
        sys.stderr.write("\n")
        sys.stderr.flush()
        elapsed = time.monotonic() - self.start_time
        logging.info(
            "[%s] DONE: %d/%d points in %.1fs (embedded=%d, zero=%d, errors=%d)",
            self.name,
            self.processed,
            self.total,
            elapsed,
            self.embedded,
            self.zero_vectors,
            self.errors,
        )


# ---------------------------------------------------------------------------
# Embedding client (sync)
# ---------------------------------------------------------------------------


class SyncEmbeddingClient:
    """Synchronous wrapper around an OpenAI-compatible embedding endpoint."""

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: Optional[str] = None,
        prompt_prefix: str = "",
        document_prefix: str = "",
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.prompt_prefix = prompt_prefix
        self.document_prefix = document_prefix
        headers: Dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(timeout=timeout, headers=headers)

    def probe_dimension(self) -> int:
        """Get the embedding dimension by sending a sample text."""
        response = self._client.post(
            f"{self.base_url}/embeddings",
            json={"model": self.model_name, "input": ["dimension probe"]},
        )
        response.raise_for_status()
        data = response.json()
        return len(data["data"][0]["embedding"])

    def embed(self, texts: List[str], mode: str = "document") -> List[List[float]]:
        """Embed a list of texts. mode='document' or mode='query'."""
        if not texts:
            return []
        prefix = self.document_prefix if mode == "document" else self.prompt_prefix
        if prefix:
            texts = [f"{prefix}{t}" for t in texts]

        # Retry with backoff on transient errors
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self._client.post(
                    f"{self.base_url}/embeddings",
                    json={"model": self.model_name, "input": texts},
                )
                if response.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logging.warning("Rate limited (429), retrying in %ds", wait)
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                data = response.json()
                sorted_items = sorted(data["data"], key=lambda x: x.get("index", 0))
                return [item["embedding"] for item in sorted_items]
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** (attempt + 1)
                logging.warning("Transient error (%s), retrying in %ds", exc, wait)
                time.sleep(wait)
        raise RuntimeError("Unreachable")

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# Core re-embed logic
# ---------------------------------------------------------------------------


def reembed_collection(
    qdrant: QdrantClient,
    embedder: SyncEmbeddingClient,
    checkpoint: ReEmbedCheckpoint,
    source_name: str,
    target_name: str,
    embedding_dim: int,
    is_ticket: bool,
    batch_size: int,
    dry_run: bool,
    resume: bool,
) -> Dict[str, Any]:
    """Re-embed all points from source collection to target collection."""
    count_result = qdrant.count(source_name, exact=True)
    total = count_result.count
    logging.info("Source '%s': %d points to re-embed", source_name, total)

    if total == 0:
        return {"source": source_name, "target": target_name, "total": 0,
                "processed": 0, "embedded": 0, "zero": 0, "errors": 0}

    start_offset: Optional[str] = None
    already_done = 0
    if resume:
        last_id, already_done = checkpoint.get(source_name)
        if last_id:
            start_offset = last_id
            logging.info("Resuming '%s' after point %s (%d already done)",
                         source_name, last_id, already_done)

    if not dry_run:
        try:
            qdrant.get_collection(target_name)
            logging.info("Target collection '%s' already exists", target_name)
        except Exception:
            logging.info("Creating target collection '%s' (dim=%d, COSINE)", target_name, embedding_dim)
            qdrant.create_collection(
                collection_name=target_name,
                vectors_config=qmodels.VectorParams(
                    size=embedding_dim,
                    distance=qmodels.Distance.COSINE,
                ),
            )

    progress = ProgressTracker(source_name, total - already_done)
    zero_vector = [0.0] * embedding_dim
    scroll_limit = max(batch_size * 4, 128)
    offset = start_offset
    batch_texts: List[str] = []
    batch_records: List[Tuple[Any, Dict]] = []
    zero_points: List[qmodels.PointStruct] = []

    try:
        while True:
            records, offset = qdrant.scroll(
                collection_name=source_name,
                limit=scroll_limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not records:
                break

            for record in records:
                payload = record.payload or {}
                semantic_empty = payload.get("semantic_empty", False)

                if semantic_empty:
                    if not dry_run:
                        zero_points.append(qmodels.PointStruct(
                            id=record.id, payload=payload, vector=zero_vector,
                        ))
                    progress.advance(count=1, zero=1)
                else:
                    detail_chunk = (payload.get("detail_chunk") or "").strip()
                    if is_ticket:
                        topic = (payload.get("topic") or "").strip()
                        parts = [t for t in [topic, detail_chunk] if t]
                        text = "\n\n".join(parts)
                    else:
                        text = detail_chunk

                    if not text:
                        if not dry_run:
                            zero_points.append(qmodels.PointStruct(
                                id=record.id, payload=payload, vector=zero_vector,
                            ))
                        progress.advance(count=1, zero=1)
                    else:
                        batch_texts.append(text)
                        batch_records.append((record, payload))
                        if len(batch_texts) >= batch_size:
                            success = _flush_embedding_batch(
                                qdrant, embedder, target_name,
                                batch_texts, batch_records,
                                embedding_dim, dry_run,
                            )
                            n = len(batch_texts)
                            if success:
                                progress.advance(count=n, embedded=n if not dry_run else 0)
                                if not dry_run:
                                    last_id = batch_records[-1][0].id
                                    checkpoint.save(source_name, str(last_id),
                                                    already_done + progress.processed, total)
                            else:
                                progress.advance(count=n, errors=n)
                            batch_texts.clear()
                            batch_records.clear()

            if zero_points and not dry_run:
                qdrant.upsert(collection_name=target_name, points=zero_points)
                zero_points.clear()

            if offset is None:
                break

        if batch_texts:
            success = _flush_embedding_batch(
                qdrant, embedder, target_name,
                batch_texts, batch_records,
                embedding_dim, dry_run,
            )
            n = len(batch_texts)
            if success:
                progress.advance(count=n, embedded=n if not dry_run else 0)
            else:
                progress.advance(count=n, errors=n)

    except KeyboardInterrupt:
        logging.warning("Interrupted – progress saved, use --resume to continue")
    finally:
        progress.finish()

    return {
        "source": source_name,
        "target": target_name,
        "total": total,
        "processed": progress.processed,
        "embedded": progress.embedded,
        "zero": progress.zero_vectors,
        "errors": progress.errors,
    }


def _flush_embedding_batch(
    qdrant: QdrantClient,
    embedder: SyncEmbeddingClient,
    target_name: str,
    texts: List[str],
    records: List[Tuple[Any, Dict]],
    embedding_dim: int,
    dry_run: bool,
) -> bool:
    """Embed a batch and upsert to target. Returns True on success."""
    if dry_run:
        return True

    try:
        embeddings = embedder.embed(texts, mode="document")
    except Exception as exc:
        logging.error("Embedding batch failed (%d texts): %s", len(texts), exc)
        return False

    if len(embeddings) != len(texts):
        logging.error("Embedding count mismatch: expected %d, got %d",
                      len(texts), len(embeddings))
        return False

    points = [
        qmodels.PointStruct(id=rec.id, payload=payload, vector=vec)
        for (rec, payload), vec in zip(records, embeddings)
    ]

    for attempt in range(3):
        try:
            qdrant.upsert(collection_name=target_name, points=points)
            return True
        except Exception as exc:
            if attempt == 2:
                logging.error("Qdrant upsert failed after 3 attempts (%d points): %s",
                              len(points), exc)
                return False
            wait = 2 ** (attempt + 1)
            logging.warning("Qdrant upsert retry %d/3 in %ds: %s", attempt + 1, wait, exc)
            time.sleep(wait)

    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-embed all Qdrant points with a new embedding model (no Help Desk calls).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--batch-size", type=int, default=32,
                        help="Number of texts per embedding API call.")
    parser.add_argument("--source-ticket", default=None,
                        help="Source ticket collection (default: from config).")
    parser.add_argument("--source-update", default=None,
                        help="Source update collection (default: from config).")
    parser.add_argument("--target-suffix", default="_v2",
                        help="Suffix appended to source names for target collections.")
    parser.add_argument("--checkpoint-db", default=DEFAULT_CHECKPOINT_DB,
                        help="Path to SQLite checkpoint database.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count and validate without writing to Qdrant.")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from last saved checkpoint.")
    parser.add_argument("--reset", action="store_true",
                        help="Reset checkpoints and start from scratch.")
    parser.add_argument("--ticket-only", action="store_true",
                        help="Only re-embed the ticket collection.")
    parser.add_argument("--update-only", action="store_true",
                        help="Only re-embed the update collection.")
    parser.add_argument("--log-level", default="INFO",
                        help="Logging level (DEBUG, INFO, WARNING, ERROR).")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format=LOG_FORMAT,
        stream=sys.stderr,
    )
    # Suppress noisy httpx/qdrant debug output
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("qdrant_client").setLevel(logging.WARNING)
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning, module="qdrant_client")

    settings = load_reembed_config()

    qdrant_host = settings.get("QDRANT_HOST", "localhost").replace("http://", "").replace("https://", "")
    qdrant_port = int(settings.get("QDRANT_PORT", "6333"))
    qdrant_api_key = settings.get("QDRANT_API_KEY") or None
    qdrant_use_tls = settings.get("QDRANT_USE_TLS", "false").lower() == "true"

    source_ticket = args.source_ticket or settings.get("QDRANT_TICKET_COLLECTION", "hd_ticket_chunks")
    source_update = args.source_update or settings.get("QDRANT_UPDATE_COLLECTION", "hd_update_chunks")
    target_ticket = f"{source_ticket}{args.target_suffix}"
    target_update = f"{source_update}{args.target_suffix}"

    # Qdrant client
    scheme = "https" if qdrant_use_tls else "http"
    qdrant = QdrantClient(
        url=f"{scheme}://{qdrant_host}:{qdrant_port}",
        api_key=qdrant_api_key,
    )

    # Embedding client
    embedding_base_url = settings["EMBEDDING_API_BASE_URL"].rstrip("/")
    embedder = SyncEmbeddingClient(
        base_url=embedding_base_url,
        model_name=settings["EMBEDDING_MODEL_NAME"],
        api_key=settings.get("EMBEDDING_API_KEY") or None,
        prompt_prefix=settings.get("EMBEDDING_PROMPT_PREFIX", ""),
        document_prefix=settings.get("EMBEDDING_DOCUMENT_PREFIX", ""),
    )

    # Probe dimension
    logging.info("Probing embedding dimension from %s (model=%s)",
                 embedding_base_url, settings["EMBEDDING_MODEL_NAME"])
    try:
        embedding_dim = embedder.probe_dimension()
    except Exception as exc:
        logging.error("Failed to probe embedding dimension: %s", exc)
        embedder.close()
        return 1
    logging.info("Embedding dimension: %d", embedding_dim)

    # Checkpoint
    checkpoint = ReEmbedCheckpoint(args.checkpoint_db)
    if args.reset:
        checkpoint.reset(source_ticket)
        checkpoint.reset(source_update)
        logging.info("Checkpoints reset.")

    # Print plan
    do_tickets = not args.update_only
    do_updates = not args.ticket_only

    print("\n" + "=" * 70)
    print("  RE-EMBED PLAN")
    print("=" * 70)
    print(f"  Embedding endpoint : {embedding_base_url}")
    print(f"  Model              : {settings['EMBEDDING_MODEL_NAME']}")
    print(f"  Embedding dim      : {embedding_dim}")
    print(f"  Prompt prefix      : {settings.get('EMBEDDING_PROMPT_PREFIX', '')!r}")
    print(f"  Document prefix    : {settings.get('EMBEDDING_DOCUMENT_PREFIX', '')!r}")
    print(f"  Batch size         : {args.batch_size}")
    print(f"  Dry run            : {args.dry_run}")
    print(f"  Resume             : {args.resume}")
    print("-" * 70)
    if do_tickets:
        print(f"  Tickets  : {source_ticket} -> {target_ticket}")
    if do_updates:
        print(f"  Updates  : {source_update} -> {target_update}")
    print("=" * 70 + "\n")

    results: List[Dict[str, Any]] = []

    try:
        if do_tickets:
            result = reembed_collection(
                qdrant=qdrant, embedder=embedder, checkpoint=checkpoint,
                source_name=source_ticket, target_name=target_ticket,
                embedding_dim=embedding_dim, is_ticket=True,
                batch_size=args.batch_size, dry_run=args.dry_run,
                resume=args.resume,
            )
            results.append(result)

        if do_updates:
            result = reembed_collection(
                qdrant=qdrant, embedder=embedder, checkpoint=checkpoint,
                source_name=source_update, target_name=target_update,
                embedding_dim=embedding_dim, is_ticket=False,
                batch_size=args.batch_size, dry_run=args.dry_run,
                resume=args.resume,
            )
            results.append(result)

    finally:
        embedder.close()

    # Summary
    print("\n" + "=" * 70)
    print("  RE-EMBED SUMMARY")
    print("=" * 70)
    total_processed = 0
    total_errors = 0
    for r in results:
        print(
            f"  {r['source']} -> {r['target']}: "
            f"{r['processed']}/{r['total']} processed "
            f"(embedded={r['embedded']}, zero={r['zero']}, errors={r['errors']})"
        )
        total_processed += r["processed"]
        total_errors += r["errors"]
    print("-" * 70)
    print(f"  Total processed: {total_processed}, errors: {total_errors}")
    if total_errors > 0:
        print("  WARNING: Some points had errors. Re-run with --resume to retry.")
    if not args.dry_run:
        print(f"\n  Next: update /etc/default/hd_ke with new collection names,")
        print(f"  then restart hd_ke. Old collections are preserved.")
    print("=" * 70 + "\n")

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())