"""Security/import boundary tests for nl2data_mongodb."""

from __future__ import annotations

import sys


class TestImportBoundary:
    def test_base_import_does_not_load_pymongo(self) -> None:
        """Importing the package should not load pymongo."""
        import nl2data_mongodb  # noqa: F401

        assert "pymongo" not in sys.modules

    def test_config_import_does_not_load_pymongo(self) -> None:
        from nl2data_mongodb.config import MongoAdapterConfig  # noqa: F401

        assert "pymongo" not in sys.modules

    def test_models_import_does_not_load_pymongo(self) -> None:
        from nl2data_mongodb.models import MongoQuerySpec  # noqa: F401

        assert "pymongo" not in sys.modules

    def test_pymongo_executor_module_does_not_load_until_use(self) -> None:
        from nl2data_mongodb.pymongo_executor import PyMongoExecutor  # noqa: F401

        assert "pymongo" not in sys.modules
