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
    "redis",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "mysql",
    "openai",
    "anthropic",
    "langchain",
    "langgraph",
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


class TestAiImportBoundary:
    def test_importing_the_ai_package_loads_no_optional_provider(self) -> None:
        import nl2data_core.ai  # noqa: F401

        loaded = {name.split(".")[0] for name in sys.modules}
        for forbidden in FORBIDDEN_IMPORTS:
            assert forbidden not in loaded, f"optional provider loaded: {forbidden}"

    def test_ai_modules_never_import_optional_providers(self) -> None:
        ai_root = SRC_ROOT / "nl2data_core" / "ai"
        offenders: list[str] = []
        for module_path in ai_root.rglob("*.py"):
            imported = _imported_names(module_path)
            for forbidden in FORBIDDEN_IMPORTS:
                if forbidden in imported:
                    offenders.append(
                        f"{module_path.relative_to(SRC_ROOT)} -> {forbidden}"
                    )
        assert offenders == [], f"forbidden imports found: {offenders}"


class TestAiProviderBoundary:
    async def test_fake_provider_runs_without_credentials_or_network(self) -> None:
        from nl2data_core.ai.fake import FakeModelProvider
        from nl2data_core.ai.models import ModelInvocationRequest

        provider = FakeModelProvider(default_response={"intent": {"source_id": "sales"}})
        response = await provider.generate(
            ModelInvocationRequest(request_id="r1", prompt="orders")
        )
        assert response.content == {"intent": {"source_id": "sales"}}
        assert provider.closed is False
        await provider.close()


class TestMongoImportBoundary:
    def test_importing_the_mongodb_package_loads_no_pymongo(self) -> None:
        import nl2data_core.adapters.mongodb  # noqa: F401
        import nl2data_core.adapters.mongodb.adapter  # noqa: F401
        import nl2data_core.adapters.mongodb.execution  # noqa: F401
        import nl2data_core.adapters.mongodb.pymongo_executor  # noqa: F401

        loaded = {name.split(".")[0] for name in sys.modules}
        assert "pymongo" not in loaded, "pymongo loaded with the mongodb package"

    def test_no_mongo_types_enter_public_contracts(self) -> None:
        """MongoDB is a specialization: no Mongo type name may appear in the
        public models or the framework-neutral adapter contract modules.
        """
        import nl2data.models
        import nl2data_core.adapters.models
        import nl2data_core.adapters.protocol

        public_modules = (
            nl2data.models,
            nl2data_core.adapters.models,
            nl2data_core.adapters.protocol,
        )
        for module in public_modules:
            names = [name for name in dir(module) if not name.startswith("_")]
            leaked = [name for name in names if "mongo" in name.lower()]
            assert leaked == [], f"mongo types leaked into {module.__name__}: {leaked}"


class TestRedisImportBoundary:
    def test_importing_the_memory_package_loads_no_redis(self) -> None:
        import nl2data_core.memory  # noqa: F401
        import nl2data_core.memory.fake_redis  # noqa: F401
        import nl2data_core.memory.redis_client  # noqa: F401
        import nl2data_core.memory.redis_config  # noqa: F401
        import nl2data_core.memory.redis_provider  # noqa: F401
        import nl2data_core.memory.redis_serialization  # noqa: F401

        loaded = {name.split(".")[0] for name in sys.modules}
        assert "redis" not in loaded, "redis loaded with the memory package"

    def test_no_redis_types_enter_public_contracts(self) -> None:
        """Redis is a specialization: no Redis type name may appear in the
        public models or the public composition profile.
        """
        import nl2data.composition
        import nl2data.models

        public_modules = (nl2data.models, nl2data.composition)
        for module in public_modules:
            names = [name for name in dir(module) if not name.startswith("_")]
            leaked = [name for name in names if "redis" in name.lower()]
            assert leaked == [], f"redis types leaked into {module.__name__}: {leaked}"
