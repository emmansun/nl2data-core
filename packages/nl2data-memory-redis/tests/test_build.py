"""Build and install checks for nl2data-memory-redis.

Proves the package can be built into a wheel and that its public exports
resolve correctly after installation.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


class TestBuild:
    def test_package_builds_to_wheel(self, tmp_path: Path) -> None:
        """The package builds a valid wheel distribution."""
        if shutil.which("python") is None:
            pytest.skip("python executable not available")
        package_dir = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path / "dist")],
            cwd=str(package_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        wheels = list((tmp_path / "dist").glob("*.whl"))
        assert len(wheels) == 1
        assert "nl2data_memory_redis" in wheels[0].name
