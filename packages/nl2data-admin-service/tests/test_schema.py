"""Schema generation and validation tests."""

from __future__ import annotations

from pydantic import BaseModel

from nl2data_admin_service.schema import build_schema, validate_schema


def test_schema_builds_for_contract_version() -> None:
    schema = build_schema("v1")
    assert schema.contract_version == "v1"
    assert "ReviewCommand" in schema.commands
    assert "BundleLifecycleCommand" in schema.commands
    assert "SnapshotDetail" in schema.results
    assert "BundleDetail" in schema.results
    assert "BundleValidationResult" in schema.results


def test_schema_validation_passes() -> None:
    schema = build_schema("v1")
    issues = validate_schema(schema)
    assert issues == []


def test_all_schema_models_are_base_models() -> None:
    schema = build_schema("v1")
    for model in {**schema.commands, **schema.results}.values():
        assert issubclass(model, BaseModel)
