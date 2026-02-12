#!/usr/bin/env python3
# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

import httpx
from pydantic import ValidationError

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.schemas import SyncRequest, SyncResult

DEFAULT_API_BASE_URL = os.getenv("HD_KE_API_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_LOG_PATH = os.getenv("HD_KE_SYNC_LOG_FILE", "/var/log/hd_ke/nightly_sync.log")
DEFAULT_TIMEOUT = float(os.getenv("HD_KE_SYNC_TIMEOUT", "120"))
DEFAULT_LOG_LEVEL = os.getenv("HD_KE_SYNC_LOG_LEVEL", "INFO")


def parse_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid datetime format: {value}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trigger hd_ke sync via REST API (designed for nightly cron/systemd runs).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help="Base URL of the hd_ke API service.",
    )
    parser.add_argument("--page-limit", type=int, default=None, help="Override page limit (defaults to API value).")
    parser.add_argument("--dry-run", action="store_true", help="Execute sync without writing to Qdrant.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds.")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for the API call.")
    parser.add_argument("--log-file", default=DEFAULT_LOG_PATH, help="Path to log file (set empty string to disable).")
    parser.add_argument("--log-level", default=DEFAULT_LOG_LEVEL, help="Logging level (INFO, DEBUG, ...).")

    filter_group = parser.add_argument_group("Filters", "Optional filters forwarded to the /sync API.")
    filter_group.add_argument(
        "--time-modified-after",
        type=parse_datetime,
        help="Only sync entries modified after this ISO timestamp.",
    )
    filter_group.add_argument(
        "--time-modified-before",
        type=parse_datetime,
        help="Only sync entries modified before this ISO timestamp.",
    )
    filter_group.add_argument(
        "--status",
        action="append",
        help="Ticket statuses to include. Repeat for multiple values.",
    )
    filter_group.add_argument(
        "--category",
        action="append",
        help="Ticket categories to include. Repeat for multiple values.",
    )
    filter_group.add_argument(
        "--tag",
        action="append",
        help="Ticket tags to include. Repeat for multiple values.",
    )
    filter_group.add_argument(
        "--ticket-id",
        type=int,
        action="append",
        help="Specific ticket IDs to sync. Repeat for multiple values.",
    )
    filter_group.add_argument(
        "--update-type",
        action="append",
        help="Update types to include. Repeat for multiple values.",
    )
    filter_group.add_argument(
        "--new-ticket-status",
        action="append",
        help="Ticket statuses after update to include. Repeat for multiple values.",
    )

    return parser


def configure_logging(level: str, log_file: str | None) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]

    if log_file:
        directory = os.path.dirname(log_file) or "."
        os.makedirs(directory, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
            )
        )

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=handlers,
    )


def build_filter_kwargs(args: argparse.Namespace) -> Dict[str, Any]:
    filter_kwargs: Dict[str, Any] = {}
    if args.time_modified_after:
        filter_kwargs["time_modified_after"] = args.time_modified_after
    if args.time_modified_before:
        filter_kwargs["time_modified_before"] = args.time_modified_before
    if args.status:
        filter_kwargs["status"] = args.status
    if args.category:
        filter_kwargs["category"] = args.category
    if args.tag:
        filter_kwargs["tag"] = args.tag
    if args.ticket_id:
        filter_kwargs["ticket_id"] = args.ticket_id
    if args.update_type:
        filter_kwargs["update_type"] = args.update_type
    if args.new_ticket_status:
        filter_kwargs["new_ticket_status"] = args.new_ticket_status

    return filter_kwargs


def run_sync(args: argparse.Namespace, api_base_url: str) -> int:
    filter_kwargs = build_filter_kwargs(args)
    page_limit = args.page_limit if args.page_limit is not None else SyncRequest().page_limit
    request_body = SyncRequest(**filter_kwargs, dry_run=args.dry_run, page_limit=page_limit)
    url = f"{api_base_url.rstrip('/')}/api/v1/sync"
    headers: Dict[str, str] = {}

    logging.info(
        "Triggering sync at %s (dry_run=%s, page_limit=%s, filters=%s)",
        url,
        args.dry_run,
        page_limit,
        json.dumps(filter_kwargs or {}, default=str),
    )

    try:
        with httpx.Client(timeout=args.timeout, verify=not args.insecure) as client:
            response = client.post(url, json=json.loads(request_body.model_dump_json()), headers=headers)
    except httpx.HTTPError as exc:
        logging.exception("Sync request failed: %s", exc)
        return 1

    if response.status_code >= 400:
        logging.error(
            "Sync failed with HTTP %s: %s",
            response.status_code,
            response.text,
        )
        return 1

    try:
        result = SyncResult.model_validate(response.json())
    except (ValueError, ValidationError):
        logging.exception("Sync response is invalid JSON/payload: %s", response.text)
        return 1

    logging.info(
        "Sync finished successfully: tickets=%s, updates=%s, upserts=%s, hidden_marked=%s, last_ticket=%s, last_update=%s",
        result.tickets_fetched,
        result.updates_fetched,
        result.points_upserted,
        result.points_marked_hidden,
        result.last_ticket_modified,
        result.last_update_modified,
    )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    log_file = args.log_file.strip() if isinstance(args.log_file, str) else args.log_file
    configure_logging(args.log_level, log_file)
    api_base_url = args.api_base_url
    return run_sync(args, api_base_url)


if __name__ == "__main__":
    sys.exit(main())
