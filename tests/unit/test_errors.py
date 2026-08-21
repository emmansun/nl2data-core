"""Unit tests for structured public error serialization safety."""

from __future__ import annotations

from nl2data import ErrorCategory, ErrorCode, NL2DataError, as_error_record
from nl2data_core.config.models import ConfigurationError


class TestStructuredErrors:
    def test_error_serializes_with_stable_fields(self) -> None:
        error = NL2DataError(
            ErrorCategory.CONFIGURATION,
            ErrorCode.INVALID_CONFIGURATION,
            "bad configuration",
            details={"path": "service.name"},
        )
        record = error.to_record()
        dumped = record.safe_dump()
        assert dumped["code"] == ErrorCode.INVALID_CONFIGURATION.value
        assert dumped["category"] == ErrorCategory.CONFIGURATION.value
        assert dumped["retryable"] is False
        assert dumped["details"]["path"] == "service.name"

    def test_credentials_are_redacted_in_details(self) -> None:
        error = NL2DataError(
            ErrorCategory.ADAPTER,
            ErrorCode.INTERNAL_ERROR,
            "connection failed",
            details={"dsn": "postgres://user:password=supersecret@host/db", "host": "db.internal"},
        )
        record = error.to_record()
        assert "supersecret" not in record.safe_dump()["details"]["dsn"]
        assert record.safe_dump()["details"]["host"] == "db.internal"

    def test_provider_object_never_serialized(self) -> None:
        error = NL2DataError(
            ErrorCategory.ADAPTER,
            ErrorCode.INTERNAL_ERROR,
            "boom",
            details={"provider": object(), "cause": ValueError("native")},
        )
        dumped = as_error_record(error).safe_dump()
        assert dumped["details"]["provider"] == "<redacted>"

    def test_unknown_exception_becomes_safe_internal_error(self) -> None:
        record = as_error_record(RuntimeError("internal: SELECT * FROM secrets"))
        assert record.code == ErrorCode.INTERNAL_ERROR
        assert record.category == ErrorCategory.INTERNAL
        assert record.retryable is False
        assert "SELECT" not in record.message

    def test_retryability_is_derived_for_known_categories(self) -> None:
        adapter_error = NL2DataError(ErrorCategory.ADAPTER, ErrorCode.INTERNAL_ERROR, "x")
        assert adapter_error.to_record().retryable is True

    def test_configuration_errors_are_non_retryable(self) -> None:
        error = ConfigurationError(ErrorCode.UNSUPPORTED_SCHEMA_VERSION, "unsupported")
        record = error.to_record()
        assert record.retryable is False
        assert record.category == ErrorCategory.CONFIGURATION
