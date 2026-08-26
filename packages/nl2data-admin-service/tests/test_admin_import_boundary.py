"""Import boundary tests for the admin service package."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).parent.parent / "src" / "nl2data_admin_service"
CORE_ROOT = Path(__file__).parent.parent.parent.parent / "src"


FORBIDDEN_IN_CORE = {
    "fastapi",
    "flask",
    "django",
    "starlette",
    "httpx",
    "requests",
    "nl2data_admin_service",
}


def _assert_no_forbidden_imports(path: Path, forbidden: set[str]) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                assert top not in forbidden, f"{path} imports {alias.name}"
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            top = node.module.split(".")[0]
            assert top not in forbidden, f"{path} imports {node.module}"


def test_admin_package_has_no_transport_dependencies() -> None:
    """The admin service package must not depend on HTTP/transport frameworks."""
    for path in SOURCE_ROOT.rglob("*.py"):
        _assert_no_forbidden_imports(
            path, {"fastapi", "flask", "django", "starlette", "httpx", "requests"}
        )


def test_core_remains_transport_free() -> None:
    """nl2data and nl2data_core must not import transport or admin service modules."""
    for package in ("nl2data", "nl2data_core"):
        for path in (CORE_ROOT / package).rglob("*.py"):
            _assert_no_forbidden_imports(path, FORBIDDEN_IN_CORE)


def test_admin_service_is_optional_via_sys_modules() -> None:
    """Importing nl2data does not load nl2data_admin_service."""
    code = "import nl2data; import sys; print('nl2data_admin_service' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
