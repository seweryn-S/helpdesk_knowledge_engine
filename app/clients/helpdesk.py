# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class HelpDeskClient:
    def __init__(self, settings: Settings, http_client: httpx.AsyncClient):
        self.settings = settings
        self.http_client = http_client
        self.base_url = str(settings.helpdesk_api_base_url).rstrip("/")
        self.api_key = settings.helpdesk_api_key

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.api_key}

    @property
    def _json_headers(self) -> dict[str, str]:
        return {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def fetch_tickets(
        self,
        params: Dict[str, Any],
        pagination: Dict[str, int],
    ) -> Dict[str, Any]:
        payload = {"params": params, "pagination": pagination}
        url = f"{self.base_url}/block_helpdesk_tickets"
        headers = self._json_headers
        if logger.isEnabledFor(logging.DEBUG):
            masked_headers = dict(headers)
            if "Authorization" in masked_headers:
                masked_headers["Authorization"] = "***masked***"
            logger.debug(
                "HelpDesk request: method=%s url=%s headers=%s json=%s",
                "POST",
                url,
                masked_headers,
                payload,
            )
        response = await self.http_client.post(url, headers=headers, json=payload, timeout=30.0)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "HelpDesk response: url=%s status=%s headers=%s body=%s",
                url,
                response.status_code,
                dict(response.headers),
                response.text,
            )
        if response.status_code != 200:
            raise RuntimeError(f"HelpDesk tickets fetch failed: {response.status_code} {response.text}")
        return response.json()

    async def fetch_updates(
        self,
        params: Dict[str, Any],
        pagination: Dict[str, int],
    ) -> Dict[str, Any]:
        payload = {"params": params, "pagination": pagination}
        url = f"{self.base_url}/block_helpdesk_updates"
        headers = self._json_headers
        if logger.isEnabledFor(logging.DEBUG):
            masked_headers = dict(headers)
            if "Authorization" in masked_headers:
                masked_headers["Authorization"] = "***masked***"
            logger.debug(
                "HelpDesk request: method=%s url=%s headers=%s json=%s",
                "POST",
                url,
                masked_headers,
                payload,
            )
        response = await self.http_client.post(url, headers=headers, json=payload, timeout=30.0)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "HelpDesk response: url=%s status=%s headers=%s body=%s",
                url,
                response.status_code,
                dict(response.headers),
                response.text,
            )
        if response.status_code != 200:
            raise RuntimeError(f"HelpDesk updates fetch failed: {response.status_code} {response.text}")
        return response.json()

    async def fetch_categories(self) -> List[Dict[str, Any]]:
        return await self._get_dict_endpoint("/block_helpdesk_categories")

    async def fetch_statuses(self) -> List[Dict[str, Any]]:
        return await self._get_dict_endpoint("/block_helpdesk_statuses")

    async def fetch_tags(self) -> List[str]:
        return await self._get_dict_endpoint("/block_helpdesk_tags")

    async def fetch_update_types(self) -> List[str]:
        return await self._get_dict_endpoint("/block_helpdesk_update_types")

    async def fetch_user_roles(self) -> List[str]:
        return await self._get_dict_endpoint("/block_helpdesk_user_roles")

    async def _get_dict_endpoint(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        headers = self._json_headers
        if logger.isEnabledFor(logging.DEBUG):
            masked_headers = dict(headers)
            if "Authorization" in masked_headers:
                masked_headers["Authorization"] = "***masked***"
            logger.debug(
                "HelpDesk request: method=%s url=%s headers=%s",
                "GET",
                url,
                masked_headers,
            )
        response = await self.http_client.get(url, headers=headers, timeout=30.0)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "HelpDesk response: url=%s status=%s headers=%s body=%s",
                url,
                response.status_code,
                dict(response.headers),
                response.text,
            )
        if response.status_code != 200:
            raise RuntimeError(f"HelpDesk dictionary fetch failed: {response.status_code} {response.text}")
        return response.json()
