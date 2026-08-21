"""Security tests: import boundaries keep optional providers out of the core.

Public modules must never import optional database, LLM, HTTP, or telemetry
backend dependencies.  Two layers of defense are tested here:

1. Static: scan every module under ``src/nl2data`` and ``src/nl2data_core``
   for forbidden import statements.
2. Dynamic: importing ``nl2data`` must not load any optional provider module.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from nl2data import NL2DataEngine  # noqa: F401

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

#: Optional provider dependencies that must never be imported by the core.
FORBIDDEN_IMPORTS = [
    "sqlalchemy",
    "pymongo",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "mysql",
    "openai",
    "anthropic",
    "langchain",
    "transformers",
    "httpx",
    "requests",
    "aiohttp",
    "fastapi",
    "flask",
    "starlette",
    "opentelemetry",
    "prometheus_client",
    "statsd",
    "boto3",
    "google.cloud",
    "azure",
    "grpc",
]


def _iter_module_files() -> list[Path]:
    return list(SRC_ROOT.rglob("*.py"))


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


class TestStaticImportBoundary:
    def test_no_core_module_imports_optional_providers(self) -> None:
        offenders: list[str] = []
        for module_path in _iter_module_files():
            imported = _imported_names(module_path)
            for forbidden in FORBIDDEN_IMPORTS:
                if forbidden in imported:
                    offenders.append(f"{module_path.relative_to(SRC_ROOT)} -> {forbidden}")
        assert offenders == [], f"forbidden imports found: {offenders}"


class TestDynamicImportBoundary:
    def test_importing_nl2data_loads_no_optional_provider(self) -> None:
        loaded = {name.split(".")[0] for name in sys.modules}
        for forbidden in FORBIDDEN_IMPORTS:
            assert forbidden not in loaded, f"optional provider loaded: {forbidden}"

    def test_public_api_is_constructible_without_providers(self) -> None:
        # Instantiating the engine skeleton must not require any provider.
        from nl2data_core.config.loader import load_config

        config = load_config({"schema_version": 1, "service": {"name": "boundary"}})
        engine = NL2DataEngine(config=config)
        assert engine.lifecycle.value == "created"
