from fastapi import APIRouter, Depends

from app.api.deps import (
    get_embedding_client,
    get_helpdesk_client,
    get_qdrant,
    get_settings,
)
from app.clients.embeddings import EmbeddingClient
from app.clients.helpdesk import HelpDeskClient
from app.clients.qdrant import QdrantRepository
from app.core.config import Settings
from app.models.schemas import SyncRequest, SyncResult
from app.services.sync import SyncService, SyncState

router = APIRouter()


@router.post(
    "/sync",
    response_model=SyncResult,
    summary="Run sync/ETL from Help Desk to Qdrant",
    description="Fetch tickets and updates incrementally and upsert into Qdrant. Uses checkpoints in SQLite unless overridden by filters.",
    tags=["sync"],
)
async def run_sync(
    body: SyncRequest,
    settings: Settings = Depends(get_settings),
    helpdesk_client: HelpDeskClient = Depends(get_helpdesk_client),
    embedding_client: EmbeddingClient = Depends(get_embedding_client),
    qdrant: QdrantRepository = Depends(get_qdrant),
) -> SyncResult:
    state = SyncState(settings.sync_checkpoint_path)
    service = SyncService(settings, helpdesk_client, embedding_client, qdrant)
    return await service.run(state, body)
