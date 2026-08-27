"""Unit/contract tests for the PostgreSQL query adapter."""

from __future__ import annotations

import pytest
from nl2data_core.adapters.models import ValidationContext
from nl2data_core.adapters.sql.guard import SQLGuardError
from nl2data_core.canonical import sha256_fingerprint

from nl2data_postgres.adapter import PostgresAdapterError, PostgresQueryAdapter
from nl2data_postgres.config import PostgresAdapterConfig


class TestPostgresQueryAdapter:
    def test_capabilities_declare_postgres(self) -> None:
        adapter = PostgresQueryAdapter(PostgresAdapterConfig(dsn_reference="env:X"))
        caps = adapter.capabilities()
        assert caps.adapter_type == "sql"
        assert "read_only" in caps.features
        #: PostgreSQL natively executes multi-entity JOIN queries, so the
        #: adapter declares the same join surface as the core SQL adapter.
        assert "join" in caps.features
        assert "multi_entity" in caps.features
        assert caps.limits.max_result_rows == 100_000

    def test_parse_valid_select(self) -> None:
        adapter = PostgresQueryAdapter(PostgresAdapterConfig(dsn_reference="env:X"))
        artifact = adapter.parse("SELECT id FROM users LIMIT 5", ValidationContext())
        assert artifact.artifact_id.startswith("pg-")
        assert artifact.parse_metadata["dialect"] == "postgres"

    def test_validate_requires_parsed_artifact(self) -> None:
        adapter = PostgresQueryAdapter(PostgresAdapterConfig(dsn_reference="env:X"))
        from nl2data_core.adapters.models import ParsedArtifact

        with pytest.raises(PostgresAdapterError):
            adapter.validate(
                ParsedArtifact(
                    artifact_id="pg-missing",
                    fingerprint="sha256:" + "0" * 64,
                    parse_metadata={},
                ),
                ValidationContext(),
            )

    async def test_execute_requires_validated_artifact(self) -> None:
        adapter = PostgresQueryAdapter(PostgresAdapterConfig(dsn_reference="env:X"))
        from nl2data_core.adapters.models import ValidatedArtifact

        with pytest.raises(PostgresAdapterError):
            await adapter.execute(
                ValidatedArtifact(
                    artifact_id="pg-missing",
                    fingerprint="sha256:" + "0" * 64,
                    validation_metadata={},
                ),
                ValidationContext(),
            )


class TestPostgresObligationEnforcement:
    def test_required_filter_obligation_is_verified_when_present(self) -> None:
        adapter = PostgresQueryAdapter(
            PostgresAdapterConfig(dsn_reference="env:X"),
            allowed_objects=frozenset({"users"}),
        )
        obligation = sha256_fingerprint({"field_id": "id", "operator": "eq", "value": 1})
        artifact = adapter.parse(
            "SELECT id FROM users WHERE id = 1 LIMIT 5", ValidationContext()
        )
        validated = adapter.validate(
            artifact,
            ValidationContext(
                required_obligation_fingerprints=frozenset({obligation}),
            ),
        )
        assert obligation in validated.obligations_verified

    def test_required_filter_obligation_omitted_is_rejected(self) -> None:
        adapter = PostgresQueryAdapter(
            PostgresAdapterConfig(dsn_reference="env:X"),
            allowed_objects=frozenset({"users"}),
        )
        obligation = sha256_fingerprint({"field_id": "id", "operator": "eq", "value": 1})
        artifact = adapter.parse("SELECT id FROM users LIMIT 5", ValidationContext())
        with pytest.raises(SQLGuardError):
            adapter.validate(
                artifact,
                ValidationContext(
                    required_obligation_fingerprints=frozenset({obligation}),
                ),
            )
