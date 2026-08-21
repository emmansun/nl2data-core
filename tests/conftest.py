"""Shared fixtures for the NL2Data test suite."""

from __future__ import annotations

import pytest

from nl2data import QueryContext, QueryOptions, QueryRequest
from nl2data_core.config.loader import load_config

VALID_CONFIG = {
    "schema_version": 1,
    "service": {"name": "test-service", "version": "0.1.0", "environment": "test"},
}


@pytest.fixture
def config_doc() -> dict:
    return dict(VALID_CONFIG)


@pytest.fixture
def effective_config():
    return load_config(VALID_CONFIG)


@pytest.fixture
def query_request() -> QueryRequest:
    return QueryRequest(
        request_id="req-1",
        prompt="how many orders were created yesterday?",
        options=QueryOptions(max_attempts=2, timeout_seconds=5.0),
        context=QueryContext(request_id="req-1"),
    )
