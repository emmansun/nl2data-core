"""Unit tests for bounded model configuration and config integration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data.errors import ErrorCode
from nl2data_core.ai.config import ModelConfig
from nl2data_core.config.loader import load_config
from nl2data_core.config.models import ConfigurationError

VALID = {"schema_version": 1, "service": {"name": "svc"}}


class TestModelConfig:
    def test_defaults_are_bounded(self) -> None:
        config = ModelConfig()
        assert config.provider_name == "fake"
        assert config.max_input_chars == 100_000
        assert config.max_output_tokens == 4096
        assert config.timeout_seconds == 30.0
        assert config.max_attempts == 3
        assert config.fingerprint.startswith("sha256:")

    def test_out_of_bounds_values_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelConfig(max_attempts=0)
        with pytest.raises(ValidationError):
            ModelConfig(timeout_seconds=0.0)
        with pytest.raises(ValidationError):
            ModelConfig(max_output_tokens=200_000)
        with pytest.raises(ValidationError):
            ModelConfig(temperature=3.0)

    def test_equivalent_configs_have_same_fingerprint(self) -> None:
        first = ModelConfig.model_validate(
            {"max_attempts": 5, "provider_name": "fake", "model_name": "m"}
        )
        second = ModelConfig.model_validate(
            {"model_name": "m", "max_attempts": 5, "provider_name": "fake"}
        )
        assert first.fingerprint == second.fingerprint

    def test_immutable_and_secret_free(self) -> None:
        config = ModelConfig()
        dumped = config.safe_dump()
        assert "api_key" not in dumped
        assert "password" not in dumped
        with pytest.raises(ValidationError):
            config.max_attempts = 9  # type: ignore[misc]


class TestConfigIntegration:
    def test_model_section_loads_with_defaults(self) -> None:
        config = load_config({**VALID, "model": {}})
        assert config.model is not None
        assert config.model.provider_name == "fake"
        assert config.model.fingerprint.startswith("sha256:")

    def test_model_section_values_override_defaults(self) -> None:
        config = load_config(
            {
                **VALID,
                "model": {
                    "provider_name": "fake",
                    "max_attempts": 5,
                    "max_output_tokens": 1024,
                },
            }
        )
        assert config.model is not None
        assert config.model.max_attempts == 5
        assert config.model.max_output_tokens == 1024

    def test_absent_model_section_is_none(self) -> None:
        config = load_config(VALID)
        assert config.model is None

    def test_model_section_changes_config_fingerprint(self) -> None:
        without = load_config(VALID)
        with_model = load_config({**VALID, "model": {"max_attempts": 5}})
        assert without.fingerprint != with_model.fingerprint

    def test_invalid_model_section_rejected_with_path(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config({**VALID, "model": {"max_attempts": -1}})
        assert excinfo.value.code == ErrorCode.MALFORMED_CONFIGURATION
        assert any("model.max_attempts" in entry for entry in excinfo.value.details["errors"])

    def test_unknown_model_field_rejected(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config({**VALID, "model": {"api_key": "sk-123"}})
        assert excinfo.value.code == ErrorCode.MALFORMED_CONFIGURATION
        assert any("model.api_key" in entry for entry in excinfo.value.details["errors"])

    def test_model_section_cannot_be_overridden_via_extensions(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            load_config(
                {**VALID, "extensions": {"model": {"max_attempts": 99}}}
            )
        assert excinfo.value.code == ErrorCode.PROTECTED_FIELD_OVERRIDE

    def test_safe_dump_never_contains_plaintext_secrets(self) -> None:
        config = load_config(
            {
                **VALID,
                "secrets": {"provider_key": {"env": "PROVIDER_KEY"}},
                "model": {"provider_name": "fake"},
            }
        )
        dumped = config.safe_dump()
        assert dumped["secrets"]["provider_key"]["name"] == "PROVIDER_KEY"
        assert "sk-" not in repr(dumped)
