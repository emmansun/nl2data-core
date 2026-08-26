"""Import boundary tests for nl2data-mongodb.

Each check runs in a fresh interpreter so real-service tests earlier in
the same pytest process (which legitimately load the installed driver)
cannot pollute the assertion.
"""

from __future__ import annotations

import subprocess
import sys


class TestImportBoundary:
    def test_base_import_does_not_load_pymongo(self) -> None:
        """Importing the package must not load the optional pymongo driver."""
        code = (
            "import sys\n"
            "assert 'pymongo' not in sys.modules\n"
            "import nl2data_mongodb\n"
            "assert 'pymongo' not in sys.modules\n"
            "assert 'nl2data_mongodb.metadata' not in sys.modules\n"
            "assert 'nl2data_mongodb.adapter' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_config_import_does_not_load_pymongo(self) -> None:
        code = (
            "import sys\n"
            "assert 'pymongo' not in sys.modules\n"
            "from nl2data_mongodb.config import MongoAdapterConfig\n"
            "assert 'pymongo' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_models_import_does_not_load_pymongo(self) -> None:
        code = (
            "import sys\n"
            "assert 'pymongo' not in sys.modules\n"
            "from nl2data_mongodb.models import MongoQuerySpec\n"
            "assert 'pymongo' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_pymongo_executor_module_does_not_load_until_use(self) -> None:
        code = (
            "import sys\n"
            "assert 'pymongo' not in sys.modules\n"
            "from nl2data_mongodb.pymongo_executor import PyMongoExecutor\n"
            "assert 'pymongo' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
