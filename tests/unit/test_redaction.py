"""Unit tests for telemetry safe-profile redaction."""

from __future__ import annotations

from nl2data_core.telemetry.redaction import DEFAULT_SAFE_PROFILE, SafeProfile, redact_attributes


class TestDefaultRedaction:
    def test_secret_keys_omitted(self) -> None:
        attributes = {"user": "alice", "password": "hunter2", "api_key": "k"}
        redacted = redact_attributes(attributes)
        assert redacted == {"user": "alice"}

    def test_raw_payloads_omitted(self) -> None:
        attributes = {
            "raw_query": "SELECT * FROM users",
            "raw_result": "[[1]]",
            "prompt": "free text",
        }
        assert redact_attributes(attributes) == {}

    def test_safe_attributes_preserved(self) -> None:
        attributes = {"adapter": "sql", "duration_ms": 42}
        assert redact_attributes(attributes) == {"adapter": "sql", "duration_ms": "42"}


class TestBounds:
    def test_attribute_count_is_bounded(self) -> None:
        profile = SafeProfile(max_attributes=3)
        attributes = {f"key_{i}": str(i) for i in range(20)}
        redacted = redact_attributes(attributes, profile)
        assert len(redacted) == 3

    def test_value_length_is_bounded(self) -> None:
        profile = SafeProfile(max_value_length=16)
        redacted = redact_attributes({"long": "x" * 1000}, profile)
        assert len(redacted["long"]) <= 16

    def test_default_profile_is_safe(self) -> None:
        assert DEFAULT_SAFE_PROFILE.redact_secrets is True
        assert DEFAULT_SAFE_PROFILE.redact_raw_queries is True
        assert DEFAULT_SAFE_PROFILE.redact_raw_results is True
        assert DEFAULT_SAFE_PROFILE.redact_unrestricted_prompts is True

    def test_profile_can_relax_redaction_explicitly(self) -> None:
        profile = SafeProfile(redact_secrets=False, max_attributes=64)
        redacted = redact_attributes({"token": "t"}, profile)
        assert redacted == {"token": "t"}

    def test_secret_value_patterns_redacted_even_under_safe_key(self) -> None:
        redacted = redact_attributes({"message": "connection password=hunter2 failed"})
        assert "hunter2" not in redacted["message"]
