# hd_ke – Help Desk RAG tool

## Purpose
`hd_ke` is an external RAG tool that indexes Help Desk tickets and updates into Qdrant and exposes a REST API for LLMs to fetch passages with citations.

## Architecture
- **ETL/sync**: periodic fetch of tickets and updates from Help Desk API, incremental via `time_modified_after`, checkpoints stored in SQLite (`/var/lib/hd_ke/state.db`).
- **Chunking**: sentence-aware splitting (default 512 chars). Every chunk carries offsets (`chunk_start`, `chunk_end`), sentence indexes and `details_hash`, enabling perfect reconstruction straight from Qdrant.
- **Embeddings**: OpenAI‑compatible client (configurable endpoint); vector dimension detected via `/embeddings` call on startup or provided in config.
- **Index**: two Qdrant collections – `hd_ticket_chunks` (tickets with full-thread metadata) and `hd_update_chunks` (updates with lean payloads referencing the parent ticket). Each shard stores the same embedding vector, and payload holds only the `url_suffix`; the REST API rebuilds the full URL.
- **API**: FastAPI (`/api/v1/query`, `/api/v1/sync`, `/api/v1/info`, `/api/v1/tickets/{id}/details`, `/api/v1/tickets/{id}/thread`, `/health`); Swagger UI enabled.

## Configuration (`/etc/default/hd_ke` → env)
Example:
```
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_TICKET_COLLECTION=hd_ticket_chunks
QDRANT_UPDATE_COLLECTION=hd_update_chunks
HELPDESK_API_BASE_URL=https://edu.p.lodz.pl/webservice/restful/server.php
HELPDESK_API_KEY=CHANGEME
HELPDESK_TICKET_URL_PREFIX=https://edu.p.lodz.pl/blocks/helpdesk/view.php?
EMBEDDING_API_BASE_URL=http://172.22.1.5:8000/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL_NAME=your-model
EMBEDDING_CONTEXT_LENGTH=512
# Optional if known: EMBEDDING_DIM=768
EMBEDDING_PROMPT_PREFIX=
EMBEDDING_DOCUMENT_PREFIX=
SYNC_CHECKPOINT_PATH=/var/lib/hd_ke/state.db
HD_KE_DATA_DIR=/srv/hd_ke
LOG_LEVEL=INFO  # set to DEBUG to log detailed Help Desk API requests/responses
THREAD_FILTER_PATTERNS=["^\\s*\\.\\s*$","^\\s*\\r?\\n\\s*$","^\\s*\\.\\s*\\r?\\n\\s*$","^\\s*\\r?\\n\\s*\\r?\\n\\s*$"]
```

`THREAD_FILTER_PATTERNS` accepts a list of regular expressions (JSON, comma- or newline-separated) that causes tickets/updates to be skipped entirely during sync. If the variable is unset we fall back to `\.`, `^\r?\n$`, `^\.\r?\n$`, and `^\r?\n\r?\n$`. The sample `deploy/hd_ke.default` file shows a more permissive configuration (`^\s*\.\s*$`, `^\s*\r?\n\s*$`, `^\s*\.\s*\r?\n\s*$`, `^\s*\r?\n\s*\r?\n\s*$`) so that the filters still trigger when users insert stray spaces around dots or blank lines. Adjust the list to match your data (e.g. `^\s*$`) to avoid discarding valid content.

`EMBEDDING_PROMPT_PREFIX` is prepended to semantic queries (e.g. `query: <your question>`), whereas `EMBEDDING_DOCUMENT_PREFIX` is applied to document chunks during sync (e.g. `passage: <chunk>`). Both are empty by default – set them only if your embedding model requires explicit hints.

### Thread content filter
- The filter applies to both tickets and updates – once a regex matches, the record is no longer chunked, embedded or upserted to Qdrant.
- Patterns are compiled during app startup; invalid regexes abort the launch with a descriptive message.
- An `INFO` log entry is emitted whenever a ticket/update gets filtered out, making it easy to audit the effect.

## API endpoints
- `GET /api/v1/health` – healthcheck.
- `GET /api/v1/info` – version, base config, embedding model.
- `POST /api/v1/sync` – run synchronization (optional filters, `dry_run`).
- `POST /api/v1/query` – semantic search with filters and citations; by default the response hides `chunk_no`, `chunk_total`, `chunk_start`, `chunk_end`, `sentence_start`, `sentence_end`, `details_hash`, and `url_suffix`, and setting `debug=true` in the body returns the full chunk payload.
- `POST /api/v1/query/threads` – semantic search returning full threads (ticket + updates) with `user_role` prefixes in text and ticket-level metadata; identically to `/query` it hides chunk metadata and `url_suffix` unless `debug=true` is provided.
- `GET /api/v1/tickets/{ticket_id}/details` – rebuild the full ticket `details` using only Qdrant chunks.
- `GET /api/v1/tickets/{ticket_id}/thread` – return the whole ticket thread (details + chronological updates) via Qdrant-only reconstruction.

## Reconstructing content from Qdrant only
- Each chunk stores a single fragment (`detail_chunk`) together with positional metadata; there is no duplicate `text`/`full_text` payload.
- The new endpoints rely on Qdrant `scroll` + filters (`ticket_id`, `source`) to fetch all chunks, sort them and stitch back the ticket or the entire thread – no extra DB needed.
- Full Help Desk links are produced in the API layer: we only store `url_suffix` in payloads and prepend `HELPDESK_TICKET_URL_PREFIX` when serving results.

### Chunk metadata
- `chunk_no` – chunk number (0-based) within a ticket/update.
- `chunk_total` – total chunk count for the entry.
- `chunk_start` / `chunk_end` – character offsets pointing into the original `details`.
- `sentence_start` / `sentence_end` – indexes of the first and last sentence in the chunk.
- `logical_id` – deterministic identifier of the chunk stored in payload (after UUID generation it is copied to `logical_id`) used for diagnostics and remapping.

## Local run
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -e .`
3. `uvicorn app.main:app --host 0.0.0.0 --port 8000`

### Devcontainer (VSCodium / VS Code)
- `.devcontainer/` ships a Dockerfile mirroring the runtime base image (Python 3.11 slim + `build-essential`) together with `devcontainer.json`.
- Use `Dev Containers: Reopen in Container` and the workspace will mount to `/workspace`; `postCreateCommand` runs `pip install -e .[dev]`, so `python3 -m pytest` works immediately.
- The container receives environment variables from `deploy/hd_ke.default` via `${localWorkspaceFolder}` (host path), so the file is found after the bind mount. Adjust the path inside `devcontainer.json` if you need a different file.
- Workspace inside the container is `/workspaces/<repo>` using `${localWorkspaceFolderBasename}` to match the host folder name.
- Added bind mount `~/.codex` -> `/root/.codex` using `${localWorkspaceFolder}/../.codex` (home directory next to the repo) so the Codex extension can access its config directory.

## Docker
Build:
```
docker build -t hd_ke:latest .
```
Run (host data directory assumed):
```
docker run --rm -p 8000:8000 \
  --env-file /etc/default/hd_ke \
  -v /srv/hd_ke:/var/lib/hd_ke \
  hd_ke:latest
```

## Default file and systemd
- Example `/etc/default/hd_ke` in `deploy/hd_ke.default`.
- Systemd unit in `deploy/hd_ke.service` (uses `HD_KE_DATA_DIR` to bind `/var/lib/hd_ke`).

## systemd (container)
Unit `hd_ke.service` example:
```
[Unit]
Description=hd_ke RAG service
After=network.target docker.service
Requires=docker.service

[Service]
EnvironmentFile=/etc/default/hd_ke
Restart=always
ExecStartPre=/usr/bin/docker pull hd_ke:latest
ExecStart=/usr/bin/docker run --rm \
  --name hd_ke \
  --env-file /etc/default/hd_ke \
  -p 8000:8000 \
  -v ${HD_KE_DATA_DIR}:/var/lib/hd_ke \
  hd_ke:latest
ExecStop=/usr/bin/docker stop hd_ke

[Install]
WantedBy=multi-user.target
```

## Nightly sync
- `scripts/nightly_sync.py` simply calls `POST /api/v1/sync`, so every API filter stays available (`--time-modified-after`, `--status`, `--category`, `--tag`, `--ticket-id`, `--update-type`, `--new-ticket-status`, `--dry-run`, `--page-limit`).
- Logs go to STDOUT and, by default, to `/var/log/hd_ke/nightly_sync.log` with rotation (10 MB × 5). Override via `--log-file` or `HD_KE_SYNC_LOG_FILE`. Use `--log-level` / `HD_KE_SYNC_LOG_LEVEL` to change verbosity.
- Run `scripts/setup_nightly_sync_env.sh` to create an isolated `scripts/.venv_nightly` environment with dependencies from `scripts/requirements.txt`; override `PYTHON_BIN` / `VENV_PATH` to point to a different interpreter or destination path.
- The default `--api-base-url` value comes from `HD_KE_API_BASE_URL` (fallback `http://127.0.0.1:8000`), but you can always override it via CLI.
- Non-zero exit codes indicate HTTP/JSON failures so cron/systemd can alert you. Log lines summarize the filter set plus the returned counters (`tickets_fetched`, `updates_fetched`, etc.).

Example run:
```
python scripts/nightly_sync.py \
  --api-base-url http://127.0.0.1:8000 \
  --log-file /var/log/hd_ke/nightly_sync.log \
  --status Open --status Pending \
  --tag "LLM-ready"
```

Example systemd service + timer executing at 02:30 every day:
```
# /etc/systemd/system/hd_ke-nightly-sync.service
[Unit]
Description=hd_ke nightly sync
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/srv/hd_ke
ExecStart=/usr/bin/python3 scripts/nightly_sync.py --api-base-url http://127.0.0.1:8000
User=hd_ke
Group=hd_ke

# /etc/systemd/system/hd_ke-nightly-sync.timer
[Unit]
Description=Run hd_ke nightly sync at 02:30

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
```
After creating the units run `systemctl daemon-reload && systemctl enable --now hd_ke-nightly-sync.timer`.

## Changelog
- 0.9.0 – added a semantic content filter for Help Desk ticket/update details (`TextContentFilter`), tagging non-informative chunks with `semantic_empty` and storing them with zero vectors instead of real embeddings; vector search hides them by default (`hide_semantic_empty` in filters) while keeping them available when reconstructing full threads.
- 0.8.2 – switched the default `EMBEDDING_DOCUMENT_PREFIX` to an empty string so document chunks stay untouched unless a prefix is explicitly required.
- 0.8.1 – added `EMBEDDING_DOCUMENT_PREFIX` and document-mode prefixes when embedding sync chunks.
- 0.8.0 – `/query` and `/query/threads` now hide chunk metadata (`chunk_*`, `sentence_*`, `details_hash`, `url_suffix`) by default, with the new `debug` flag enabling full payloads in responses.
- 0.7.1 – recommended `THREAD_FILTER_PATTERNS` (env config) now includes whitespace-tolerant versions of the dot/CRLF filters to remove empty records before chunking.
- 0.7.0 – added `scripts/setup_nightly_sync_env.sh` + `scripts/requirements.txt` for a dedicated virtualenv tailored to `nightly_sync.py`.
- 0.6.0 – added `scripts/nightly_sync.py` helper with logging and documentation for timer/systemd setups.
- 0.5.0 – new `/query/threads` endpoint: returns full threads (ticket + updates) reconstructed from chunks with `user_role` prefixes in text and ticket metadata (status, tags, URL, time_created, time_modified from the latest entry).
- 0.4.6 – fixed parsing of `THREAD_FILTER_PATTERNS`: env source no longer forces JSON decoding, validator accepts empty, CSV, or JSON strings.
- 0.4.5 – devcontainer: `~/.codex` mount derived from the repo path (`${localWorkspaceFolder}/../.codex`) so it reliably appears in the container.
- 0.4.4 – devcontainer: fixed `~/.codex` mount by using `${localEnv:HOME}` to locate the host home directory.
- 0.4.3 – devcontainer: workspace relocated to `/workspaces/<repo>`, added `~/.codex` bind mount for the Codex extension.
- 0.4.2 – devcontainer fix: `--env-file` now points to the host path (`${localWorkspaceFolder}/deploy/hd_ke.default`), eliminating the “no such file” error on container startup.
- 0.4.1 – added a devcontainer for VSCodium/VS Code (`pip install -e .[dev]` happens automatically, so `pytest` is ready to run).
- 0.4.0 – configurable regex filters for `details` (records are dropped during sync), default `\.` pattern in `/etc/default/hd_ke`, logging of skipped records and validation of regexes on startup.
- 0.3.1 – added Polish inline comments for every field, documented chunk metadata, and stopped storing full URLs in payloads (the API builds them from `url_suffix`). **Requires re-sync** if you rely on the new URL behaviour.
- 0.3.0 – split the Qdrant index into two collections (`hd_ticket_chunks`, `hd_update_chunks`), removed duplicate text payloads (only `detail_chunk` stays) and introduced `QDRANT_TICKET_COLLECTION` / `QDRANT_UPDATE_COLLECTION`. **Requires wiping/re-syncing both collections** due to the new schema and IDs.
- 0.2.0 – sentence-aware chunking with positional metadata, new `/tickets/{id}/details` + `/tickets/{id}/thread` endpoints, and ticket/thread reconstruction backed solely by Qdrant. **Requires wiping/re-syncing the collection** because chunk layouts and IDs changed.
- 0.1.14 – fixed `QdrantRepository.search` to return the list of points (`response.points`) instead of the full `QueryResponse`, avoiding `tuple` errors when iterating in `QueryService`.
- 0.1.13 – aligned Qdrant integration with `qdrant-client 1.16.1` docs: use `qdrant_client.models` and high-level `client.query_points(...)` instead of `search`.
- 0.1.12 – switched to high-level `QdrantClient.search(...)` API and bumped requirement to `qdrant-client>=1.16.1,<2.0.0`.
- 0.1.11 – Qdrant search now uses `openapi_client.points_api.search_points(...)` with a `SearchRequest`, decoupling from the presence of `QdrantClient.search`.
- 0.1.10 – simplified Qdrant search: `QdrantRepository.search` now directly uses `QdrantClient.search(...)` as documented for `qdrant-client>=1.8.0`.
- 0.1.9 – fixed Qdrant search: prefer `QdrantClient.search` when available, otherwise call `remote.search` on the internal `_client`, with a clear upgrade hint if neither exists.
- 0.1.8 – support for both Qdrant client variants (`search` and `search_points`) in `QdrantRepository.search`, so queries work regardless of library version.
- 0.1.7 – changed Qdrant point IDs to deterministic UUIDs (instead of `ticket-...` / `update-...` strings), original IDs moved to payload as `logical_id`.
- 0.1.6 – fixed `NameError: qmodels is not defined` in `SyncService` (missing Qdrant models import).
- 0.1.5 – `Accept: application/json` header added to all Help Desk calls in `HelpDeskClient` to force JSON responses instead of XML.
- 0.1.4 – improved logging of headers (including `Content-Type`) for Help Desk calls in `HelpDeskClient`.
- 0.1.3 – `Content-Type: application/json` header also added to Help Desk dictionary GET calls in `HelpDeskClient`.
- 0.1.2 – `Content-Type: application/json` header added to Help Desk POST API calls in `HelpDeskClient`.
- 0.1.1 – DEBUG logging of full Help Desk API requests/responses in `HelpDeskClient`.
- 0.1.0 – structure, configuration, sync/query API, systemd unit, Dockerfile.
