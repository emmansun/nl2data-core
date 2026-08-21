"""Unit tests for configuration defaults, fingerprints, immutability,
redaction and fail-closed loading.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data.errors import ErrorCode
from nl2data_core.config.loader import load_config
from nl2data_core.config.models import ConfigurationError

VALID = {"schema_version": 1, "service": {"name": "svc"}}


class TestDefaultsAndActivation:
    def test_valid_configuration_activates(self) -> None:
        config = load_config(VALID)
        assert config.service.name == "svc"
        assert config.service.environment == "development"
        assert config.runtime.max_attempts == 3
        assert config.runtime.telemetry_enabled is True
        assert config.fingerprint.startswith("sha256:")

    def test_yaml_document_loads(self) -> None:
        yaml_text = "schema_version: 1\nservice:\n  name: svc\n  environment: production\n"
        config = load_config(yaml_text)
        assert config.service.environment == "production"

    def test_supplied_values_override_defaults(self) -> None:
        config = load_config(
            {"schema_version": 1, "service": {"name": "svc"}, "runtime": {"max_attempts": 5}}
        )
        assert config.runtime.max_attempts == 5


class TestCanonicalFingerprint:
    def test_equivalent_inputs_have_same_fingerprint(self) -> None:
        first = load_config(
            {"schema_version": 1, "service": {"name": "svc"}, "runtime": {"max_attempts": 5}}
        )
        second = load_config(
            {"runtime": {"max_attempts": 5}, "service": {"name": "svc"}, "schema_version": 1}
        )
        assert first.fingerprint == second.fingerprint

    def test_different_configuration_has_different_fingerprint(self) -> None:
        first = load_config(VALID)
        second = load_config({"schema_version": 1, "service": {"name": "other"}})
        assert first.fingerprint != second.fingerprint


class TestImmutability:
    def test_snapshot_cannot_be_mutated(self) -> None:
        config = load_config(VALID)
        with pytest.raises(ValidationError):
            config.service.name = "changed"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            config.runtime.max_attempts = 9  # type: ignore[misc]

    def test_nested_snapshot_mappings_cannot_be_mutated(self) -> None:
        config = load_config(
            {
                "schema_version": 1,
                "service": {"name": "svc"},
                "extensions": {"provider": {"region": "west"}},
            }
        )
        with pytest.raises(TypeError):
            config.extensions["provider"].values["region"] = "east"  # type: ignore[index]
        with pytest.raises(TypeError):
            config.extensions["new"] = config.extensions["provider"]  # type: ignore[index]


class TestSecretRedaction:
    def test_secret_reference_not_emitted_as_plaintext(self) -> None:
        config = load_config(
            {
                "schema_version": 1,
                "service": {"name": "svc"},
                "secrets": {"db_password": {"env": "DB_PASSWORD"}},
            }
        )
        dumped = config.safe_dump()
        assert "DB_PASSWORD" in dumped["secrets"]["db_password"]["name"]
        assert "hunter2" not in repr(dumped)
        assert config.secrets["db_password"].kind == "env"

    def test_secret_reference_shorthand(self) -> None:
        config = load_config(
            {
                "schema_version": 1,
                "service": {"name": "svc"},
                "secrets": {"api_key": {"env": "API_KEY"}},
            }
        )
        assert config.secrets["api_key"].name == "API_KEY"


class TestFailClosedValidation:
    def test_unsupported_schema_version_rejected(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config({"schema_version": 99, "service": {"name": "svc"}})
        assert excinfo.value.code == ErrorCode.UNSUPPORTED_SCHEMA_VERSION
        assert excinfo.value.retryable is False

    def test_missing_schema_version_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            load_config({"service": {"name": "svc"}})

    def test_unknown_strict_field_rejected_with_path(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config({"schema_version": 1, "service": {"name": "svc", "bogus": 1}})
        assert excinfo.value.code == ErrorCode.MALFORMED_CONFIGURATION
        assert any("service.bogus" in entry for entry in excinfo.value.details["errors"])

    def test_unknown_top_level_field_rejected_with_path(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config({**VALID, "bogus": 1})
        assert excinfo.value.code == ErrorCode.MALFORMED_CONFIGURATION
        assert excinfo.value.details["path"] == "bogus"

    def test_malformed_value_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            load_config(
                {"schema_version": 1, "service": {"name": "svc"}, "runtime": {"max_attempts": -1}}
            )

    def test_plaintext_secret_is_protected_override(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config(
                {
                    "schema_version": 1,
                    "service": {"name": "svc"},
                    "secrets": {"db_password": "hunter2"},
                }
            )
        assert excinfo.value.code == ErrorCode.PROTECTED_FIELD_OVERRIDE

    def test_protected_core_section_cannot_be_overridden(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config(
                {
                    "schema_version": 1,
                    "service": {"name": "svc"},
                    "extensions": {"runtime": {"max_attempts": 99}},
                }
            )
        assert excinfo.value.code == ErrorCode.PROTECTED_FIELD_OVERRIDE

    def test_invalid_yaml_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            load_config("schema_version: [unclosed")

    def test_non_mapping_document_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            load_config("[1, 2, 3]")
