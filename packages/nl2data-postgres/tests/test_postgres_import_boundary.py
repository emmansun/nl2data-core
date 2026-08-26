"""Import boundary tests for nl2data-postgres."""

from __future__ import annotations

import subprocess
import sys


class TestImportBoundary:
    def test_base_package_import_does_not_load_psycopg(self) -> None:
        """Importing the package must not load the optional psycopg driver."""
        code = (
            "import sys\n"
            "assert 'psycopg' not in sys.modules\n"
            "import nl2data_postgres\n"
            "assert 'psycopg' not in sys.modules\n"
            "assert 'nl2data_postgres.discovery' not in sys.modules\n"
            "assert 'nl2data_postgres.adapter' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_config_import_does_not_load_psycopg(self) -> None:
        code = (
            "import sys\n"
            "assert 'psycopg' not in sys.modules\n"
            "from nl2data_postgres.config import PostgresAdapterConfig\n"
            "assert 'psycopg' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
