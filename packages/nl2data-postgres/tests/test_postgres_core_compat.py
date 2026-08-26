"""Verify the in-core compatibility export delegates to the package."""

from __future__ import annotations


class TestCoreCompatibilityExport:
    def test_core_lazy_export_returns_package_discoverer(self) -> None:
        from nl2data_core.adapters.sql import PostgresMetadataDiscoverer

        from nl2data_postgres.discovery import PostgresMetadataDiscoverer as PackageDiscoverer

        assert PostgresMetadataDiscoverer is PackageDiscoverer
