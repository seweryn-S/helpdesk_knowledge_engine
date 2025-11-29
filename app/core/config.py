from __future__ import annotations

import json
import os
import re
from typing import Any, List, Optional

from pydantic import Field, HttpUrl, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import EnvSettingsSource

from app.version import __version__


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="allow",
        env_ignore_empty=True,
    )

    qdrant_host: str = Field(default="localhost")
    qdrant_port: int = Field(default=6333)
    qdrant_api_key: Optional[str] = Field(default=None)
    qdrant_use_tls: bool = Field(default=False)
    qdrant_ticket_collection: str = Field(default="hd_ticket_chunks")
    qdrant_update_collection: str = Field(default="hd_update_chunks")

    helpdesk_api_base_url: HttpUrl
    helpdesk_api_key: str
    helpdesk_ticket_url_prefix: HttpUrl

    embedding_api_base_url: HttpUrl
    embedding_api_key: Optional[str] = Field(default=None)
    embedding_model_name: str
    embedding_context_length: int = Field(default=512)
    embedding_prompt_prefix: str = Field(default="")
    embedding_dim: Optional[int] = Field(default=None)
    thread_filter_patterns: List[str] = Field(
        default_factory=lambda: [r"\.", r"^\r?\n$", r"^\.\r?\n$", r"^\r?\n\r?\n$"]
    )

    sync_checkpoint_path: str = Field(default="/var/lib/hd_ke/state.db")
    sync_page_size: int = Field(default=100)
    sync_chunk_size: int = Field(default=512)

    log_level: str = Field(default="INFO")
    hd_ke_data_dir: Optional[str] = Field(default=None)
    hd_ke_version: str = Field(default=__version__)

    @field_validator("thread_filter_patterns", mode="before")
    @classmethod
    def _parse_thread_filter_patterns(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parts = re.split(r"[,\n;]+", raw)
                return [part.strip() for part in parts if part.strip()]
            else:
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
                if isinstance(parsed, str):
                    return [parsed.strip()] if parsed.strip() else []
        raise ValueError("thread_filter_patterns must be a list or string")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        class PermissiveEnvSource(env_settings.__class__):
            def decode_complex_value(self, field_name, field, value):
                if field_name == "thread_filter_patterns":
                    return value
                try:
                    return super().decode_complex_value(field_name, field, value)
                except json.JSONDecodeError:
                    return value

        return (
            init_settings,
            PermissiveEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    def ensure_paths(self) -> None:
        """Ensure directories needed for state exist."""
        checkpoint_dir = os.path.dirname(self.sync_checkpoint_path)
        if checkpoint_dir and not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir, exist_ok=True)


def load_settings() -> Settings:
    try:
        settings = Settings()  # type: ignore[call-arg]
        settings.ensure_paths()
        return settings
    except ValidationError as exc:
        raise RuntimeError(f"Configuration invalid: {exc}") from exc
