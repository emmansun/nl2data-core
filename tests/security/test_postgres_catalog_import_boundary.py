"""Security tests: the semantic catalog package never loads or leaks psycopg.

Three layers of defense for the sibling ``nl2data-semantic-catalog-postgres``
distribution:

1. Static import boundary: neither the core packages (``nl2data``,
   ``nl2data_core``) nor the catalog package import ``psycopg``/
   ``psycopg_pool`` at module level, and the core never references the
   optional catalog package at all.
2. Dynamic import boundary: importing every catalog module - and running
   catalog operations over an injected pool - loads no database driver.
3. Missing-driver boundary: a DSN-based pool build fails as a normalized,
   redacted catalog error, and driver error classification works by class
   name alone so injected fakes need no driver installed.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from nl2data_core.metadata import (
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataTrustLevel,
)
from nl2data_semantic_catalog_postgres.client import (
    build_pool,
    driver_available,
    is_connect_error,
    is_duplicate_key_error,
    is_serialization_error,
    is_timeout_error,
)
from nl2data_semantic_catalog_postgres.errors import (
    SemanticCatalogError,
    SemanticCatalogErrorCode,
)
from nl2data_semantic_catalog_postgres.fake_postgres import (
    FakePostgresPool,
    OperationalError,
    SerializationFailure,
    TimeoutError,
    UniqueViolation,
)
from nl2data_semantic_catalog_postgres.store import PostgreSQLSemanticCatalog

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
CATALOG_SRC_ROOT = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "nl2data-semantic-catalog-postgres"
    / "src"
)

#: Optional driver modules that must never be imported by the core or the
#: catalog package at module level.
DRIVER_MODULES = ("psycopg", "psycopg_pool")

#: DSN material that must never cross any error boundary.
_DSN = "postgresql://deploy:hunter2@localhost:5432/catalog"


def _imported_names(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _assert_no_driver_loaded(before: set[str]) -> None:
    """Assert the imports under test loaded none of the driver modules.

    Comparing against a pre-import snapshot keeps the dynamic checks
    order-independent: a real-service test earlier in the process may have
    legitimately built a pool, loading the driver.
    """
    loaded = {name.split(".")[0] for name in sys.modules} - before
    for driver in DRIVER_MODULES:
        assert driver not in loaded, f"{driver} loaded with the catalog package"


class TestStaticImportBoundary:
    def test_catalog_package_never_imports_the_driver(self) -> None:
        """psycopg may only be reached via importlib strings, never imports."""
        offenders: list[str] = []
        for module_path in CATALOG_SRC_ROOT.rglob("*.py"):
            imported = _imported_names(module_path)
            for driver in DRIVER_MODULES:
                if driver in imported:
                    offenders.append(
                        f"{module_path.relative_to(CATALOG_SRC_ROOT)} -> {driver}"
                    )
        assert offenders == [], f"module-level driver imports found: {offenders}"

    def test_core_sources_never_import_the_driver(self) -> None:
        offenders: list[str] = []
        for module_path in SRC_ROOT.rglob("*.py"):
            imported = _imported_names(module_path)
            for driver in DRIVER_MODULES:
                if driver in imported:
                    offenders.append(
                        f"{module_path.relative_to(SRC_ROOT)} -> {driver}"
                    )
        assert offenders == [], f"core driver imports found: {offenders}"

    def test_core_sources_never_import_the_catalog_package(self) -> None:
        """The optional package is a host decision; core never references it."""
        offenders: list[str] = []
        for module_path in SRC_ROOT.rglob("*.py"):
            imported = _imported_names(module_path)
            if "nl2data_semantic_catalog_postgres" in imported:
                offenders.append(str(module_path.relative_to(SRC_ROOT)))
        assert offenders == [], f"core catalog imports found: {offenders}"


class TestDynamicImportBoundary:
    def test_importing_the_catalog_package_loads_no_driver(self) -> None:
        before = {name.split(".")[0] for name in sys.modules}
        import nl2data_semantic_catalog_postgres  # noqa: F401
        import nl2data_semantic_catalog_postgres.client  # noqa: F401
        import nl2data_semantic_catalog_postgres.config  # noqa: F401
        import nl2data_semantic_catalog_postgres.envelope  # noqa: F401
        import nl2data_semantic_catalog_postgres.errors  # noqa: F401
        import nl2data_semantic_catalog_postgres.fake_postgres  # noqa: F401
        import nl2data_semantic_catalog_postgres.schema  # noqa: F401
        import nl2data_semantic_catalog_postgres.store  # noqa: F401

        _assert_no_driver_loaded(before)

    def test_catalog_operations_over_an_injected_pool_load_no_driver(self) -> None:
        before = {name.split(".")[0] for name in sys.modules}
        pool = FakePostgresPool()
        catalog = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        snapshot = _minimal_snapshot()
        scope = "sha256:" + "ab" * 32
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=scope)
        assert catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=scope) is not None
        _assert_no_driver_loaded(before)

    def test_importing_core_and_public_api_loads_no_driver(self) -> None:
        before = {name.split(".")[0] for name in sys.modules}
        import nl2data  # noqa: F401
        import nl2data_core  # noqa: F401

        _assert_no_driver_loaded(before)


class TestMissingDriverBoundary:
    def test_driver_available_requires_both_packages(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "nl2data_semantic_catalog_postgres.client.find_spec", lambda name: None
        )
        assert driver_available() is False

    def test_build_pool_fails_as_a_normalized_redacted_error(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "nl2data_semantic_catalog_postgres.client.driver_available",
            lambda: False,
        )
        with pytest.raises(SemanticCatalogError) as excinfo:
            build_pool(
                _DSN,
                pool_size=5,
                connect_timeout_seconds=5.0,
                command_timeout_seconds=30.0,
                acquire_timeout_seconds=5.0,
                schema="catalog",
            )
        error = excinfo.value
        assert error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE
        assert error.retryable
        record = error.to_record().safe_dump()
        assert record["details"]["cause_type"] == "ImportError"
        surface = f"{error} {record} {error.safe_details()}"
        for fragment in ("postgresql://", "hunter2", "localhost", "5432"):
            assert fragment not in surface

    def test_error_classification_is_driver_free(self, monkeypatch) -> None:
        """Fake driver exceptions are classified by class name alone, so the
        real driver never needs to be installed for injected fakes."""

        def no_import(name: str) -> None:
            raise ImportError(name)

        monkeypatch.setattr(
            "nl2data_semantic_catalog_postgres.client.import_module", no_import
        )
        assert is_connect_error(OperationalError("backend down"))
        assert is_timeout_error(TimeoutError("statement timed out"))
        assert is_duplicate_key_error(UniqueViolation("duplicate key"))
        assert is_serialization_error(SerializationFailure("write skew"))
        assert not is_connect_error(RuntimeError("unrelated failure"))
        assert not is_timeout_error(OperationalError("backend down"))


def _minimal_snapshot() -> MetadataSnapshot:
    return MetadataSnapshot(
        snapshot_id="snap-boundary",
        source=MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint="sha256:" + "ab" * 32,
            description="boundary check source",
        ),
        objects=(
            MetadataObject(
                object_id="orders",
                kind=MetadataObjectKind.TABLE,
                name="orders",
                fields=(
                    MetadataField(
                        field_id="order_id",
                        object_id="orders",
                        path="order_id",
                        data_type="INTEGER",
                        nullable=False,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        freshness=MetadataFreshness(
            bounded_objects=False, bounded_fields=False, sample_limit=10
        ),
        provenance=MetadataProvenance(
            discovered_by_fingerprint="sha256:" + "11" * 32,
            method="boundary",
        ),
    )
