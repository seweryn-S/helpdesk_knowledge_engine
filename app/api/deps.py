from fastapi import Request

from app.clients.embeddings import EmbeddingClient
from app.clients.helpdesk import HelpDeskClient
from app.clients.qdrant import QdrantRepository
from app.core.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_qdrant(request: Request) -> QdrantRepository:
    return request.app.state.qdrant


def get_embedding_client(request: Request) -> EmbeddingClient:
    return request.app.state.embedding_client


def get_helpdesk_client(request: Request) -> HelpDeskClient:
    return request.app.state.helpdesk_client
