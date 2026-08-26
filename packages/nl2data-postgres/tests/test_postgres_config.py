"""Unit tests for ``nl2data_postgres.config``."""

from __future__ import annotations

import pytest

from nl2data_postgres.config import PostgresAdapterConfig


class TestPostgresAdapterConfig:
    def test_default_values(self) -> None:
        config = PostgresAdapterConfig(dsn_reference="dsn:postgresql://localhost/db")
        assert config.resolve_dsn() == "postgresql://localhost/db"
        assert config.max_objects == 256
        assert config.max_rows == 100_000
        assert config.read_only is True

    def test_dsn_literal(self) -> None:
        config = PostgresAdapterConfig(dsn_reference="dsn:postgresql://localhost/db")
        assert config.resolve_dsn() == "postgresql://localhost/db"

    def test_dsn_reference_fingerprint_excludes_secrets(self) -> None:
        config = PostgresAdapterConfig(dsn_reference="env:NL2DATA_POSTGRES_DSN")
        payload = config.safe_payload()
        assert "password" not in payload
        assert "postgresql://" not in str(payload)

    def test_allowlist_must_be_bounded_identifier(self) -> None:
        with pytest.raises(ValueError):
            PostgresAdapterConfig(dsn_reference="env:X", allowed_objects={"bad name"})

    def test_pool_size_consistency(self) -> None:
        with pytest.raises(ValueError):
            PostgresAdapterConfig(dsn_reference="env:X", pool_min_size=4, pool_max_size=2)
