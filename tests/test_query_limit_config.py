# Copyright (C) 2026 Seweryn Sitarski <seweryn.sitarski@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later

import importlib

import pytest
from pydantic import ValidationError

import app.models.schemas as schemas_module
from app.core.config import QUERY_LIMIT_DEFAULT, QUERY_MAX_LIMIT_DEFAULT, resolve_query_max_limit


def test_resolve_query_max_limit_default(monkeypatch) -> None:
    monkeypatch.delenv("QUERY_MAX_LIMIT", raising=False)
    assert resolve_query_max_limit() == QUERY_MAX_LIMIT_DEFAULT


def test_resolve_query_max_limit_from_env(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MAX_LIMIT", "120")
    assert resolve_query_max_limit() == 120


def test_resolve_query_max_limit_whitespace_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MAX_LIMIT", "   ")
    assert resolve_query_max_limit() == QUERY_MAX_LIMIT_DEFAULT


def test_resolve_query_max_limit_rejects_non_integer(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MAX_LIMIT", "abc")
    with pytest.raises(RuntimeError, match="QUERY_MAX_LIMIT"):
        resolve_query_max_limit()


def test_resolve_query_max_limit_rejects_non_positive(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MAX_LIMIT", "0")
    with pytest.raises(RuntimeError, match="positive"):
        resolve_query_max_limit()


def test_query_request_default_limit_bounds() -> None:
    QueryRequest = schemas_module.QueryRequest
    request = QueryRequest(query="q", limit=QUERY_MAX_LIMIT_DEFAULT)
    assert request.limit == QUERY_MAX_LIMIT_DEFAULT
    with pytest.raises(ValidationError):
        QueryRequest(query="q", limit=QUERY_MAX_LIMIT_DEFAULT + 1)
    with pytest.raises(ValidationError):
        QueryRequest(query="q", limit=0)


def test_query_request_respects_env_override(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MAX_LIMIT", "10")
    reloaded = importlib.reload(schemas_module)
    try:
        reloaded.QueryRequest(query="q", limit=10)
        with pytest.raises(ValidationError):
            reloaded.QueryRequest(query="q", limit=11)
        json_schema = reloaded.QueryRequest.model_json_schema()
        assert json_schema["properties"]["limit"]["maximum"] == 10
    finally:
        monkeypatch.delenv("QUERY_MAX_LIMIT", raising=False)
        importlib.reload(schemas_module)


def test_query_limit_default_clamped_to_max(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MAX_LIMIT", "3")
    reloaded = importlib.reload(schemas_module)
    try:
        assert reloaded.QueryRequest.model_fields["limit"].default == 3
        request = reloaded.QueryRequest(query="q")
        assert request.limit == 3
        with pytest.raises(ValidationError):
            reloaded.QueryRequest(query="q", limit=4)
        json_schema = reloaded.QueryRequest.model_json_schema()
        assert json_schema["properties"]["limit"]["default"] == 3
        assert json_schema["properties"]["limit"]["maximum"] == 3
    finally:
        monkeypatch.delenv("QUERY_MAX_LIMIT", raising=False)
        importlib.reload(schemas_module)


def test_query_limit_default_unchanged_when_max_higher(monkeypatch) -> None:
    monkeypatch.setenv("QUERY_MAX_LIMIT", "30")
    reloaded = importlib.reload(schemas_module)
    try:
        assert reloaded.QueryRequest.model_fields["limit"].default == QUERY_LIMIT_DEFAULT
        assert reloaded.QueryRequest.model_json_schema()["properties"]["limit"]["maximum"] == 30
    finally:
        monkeypatch.delenv("QUERY_MAX_LIMIT", raising=False)
        importlib.reload(schemas_module)