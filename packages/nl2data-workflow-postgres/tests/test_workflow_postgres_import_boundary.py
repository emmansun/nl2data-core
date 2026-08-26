"""Import-boundary tests for nl2data-workflow-postgres.

Proves that importing the package does not load the optional ``psycopg``
driver and that base ``nl2data`` / ``nl2data_core`` remain PostgreSQL-free.
"""

from __future__ import annotations

import sys


class TestLazyDriverLoading:
    def test_importing_package_does_not_load_psycopg(self) -> None:
        before = set(sys.modules)
        import nl2data_workflow_postgres  # noqa: F401
        after = set(sys.modules)
        loaded = after - before
        assert "psycopg" not in loaded
        assert "psycopg_pool" not in loaded

    def test_driver_available_checks_spec_without_importing(self) -> None:
        from nl2data_workflow_postgres import driver_available

        before = set(sys.modules)
        result = driver_available()
        after = set(sys.modules)
        assert isinstance(result, bool)
        # driver_available only checks module specs, never imports psycopg.
        assert "psycopg" not in (after - before)
        assert "psycopg_pool" not in (after - before)


class TestBaseImportRemainsPostgresFree:
    def test_importing_nl2data_core_workflow_does_not_load_psycopg(self) -> None:
        before = set(sys.modules)
        import nl2data_core.workflow  # noqa: F401
        after = set(sys.modules)
        loaded = after - before
        assert "psycopg" not in loaded
        assert "psycopg_pool" not in loaded

    def test_importing_nl2data_does_not_load_psycopg(self) -> None:
        before = set(sys.modules)
        import nl2data  # noqa: F401
        after = set(sys.modules)
        loaded = after - before
        assert "psycopg" not in loaded
        assert "psycopg_pool" not in loaded
