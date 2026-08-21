"""Contract tests for the stable public import boundary."""

from __future__ import annotations

import sys

DOCUMENTED_SYMBOLS = [
    "NL2DataEngine",
    "LifecycleError",
    "NL2DataError",
    "ErrorCategory",
    "ErrorCode",
    "ErrorRecord",
    "as_error_record",
    "EngineCapabilitySnapshot",
    "EngineHealth",
    "HealthStatus",
    "LifecycleState",
    "OutcomeStatus",
    "QueryContext",
    "QueryOptions",
    "QueryOutcome",
    "QueryRequest",
    "QueryResult",
]

OPTIONAL_PROVIDER_MODULES = [
    "sqlalchemy",
    "pymongo",
    "psycopg",
    "asyncpg",
    "openai",
    "langchain",
    "httpx",
    "requests",
    "fastapi",
    "flask",
    "opentelemetry",
    "prometheus_client",
    "boto3",
    "google.cloud",
]


class TestPublicImportSurface:
    def test_all_documented_symbols_are_importable(self) -> None:
        import nl2data

        for name in DOCUMENTED_SYMBOLS:
            assert hasattr(nl2data, name), f"missing public symbol: {name}"
            assert name in nl2data.__all__, f"{name} missing from __all__"

    def test_importing_nl2data_requires_no_provider_dependencies(self) -> None:
        import nl2data  # noqa: F401

        for module in OPTIONAL_PROVIDER_MODULES:
            assert module not in sys.modules, f"optional provider imported: {module}"

    def test_internal_package_not_required_for_public_import(self) -> None:
        import nl2data  # noqa: F401

        # nl2data_core is an implementation detail; public models do not
        # leak internal classes through their annotations.
        for symbol in ("QueryRequest", "QueryOutcome", "QueryResult", "ErrorRecord"):
            annotation_src = getattr(__import__("nl2data").models, symbol).model_fields
            for field in annotation_src.values():
                assert "nl2data_core" not in str(field.annotation)
