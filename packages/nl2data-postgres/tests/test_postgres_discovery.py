"""Unit/contract tests for the PostgreSQL metadata discoverer."""

from __future__ import annotations

import pytest
from nl2data_core.metadata.protocol import MetadataDiscoveryConfig, MetadataUnauthorizedError

from nl2data_postgres.config import PostgresAdapterConfig
from nl2data_postgres.discovery import PostgresMetadataDiscoverer


class TestPostgresMetadataDiscoverer:
    def test_capability_declares_postgresql(self) -> None:
        discoverer = PostgresMetadataDiscoverer(PostgresAdapterConfig(dsn_reference="env:X"))
        cap = discoverer.capability()
        assert cap.backend == "sql:postgresql"
        assert cap.supported is True

    async def test_discover_requires_allowed_objects(self) -> None:
        discoverer = PostgresMetadataDiscoverer(PostgresAdapterConfig(dsn_reference="env:X"))
        with pytest.raises(MetadataUnauthorizedError):
            await discoverer.discover(MetadataDiscoveryConfig())

    async def test_discover_honors_config_allowlist(self) -> None:
        discoverer = PostgresMetadataDiscoverer(
            PostgresAdapterConfig(dsn_reference="env:X"),
            allowed_objects={"users"},
        )
        with pytest.raises(MetadataUnauthorizedError):
            await discoverer.discover(
                MetadataDiscoveryConfig(allowed_objects={"not_users"})
            )
