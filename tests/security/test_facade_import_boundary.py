"""Security tests: base library usage never requires optional backends.

Importing ``nl2data``, constructing a facade, and even initializing an
empty facade must not load HTTP frameworks, LangGraph, the SQL compiler,
database drivers, vendor model SDKs, or telemetry backends.  The public
boundary modules are additionally scanned statically so no optional
backend name can creep into the facade surface.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

#: Optional backends that base library usage must never load.
OPTIONAL_BACKENDS = [
    "langgraph",
    "sqlglot",
    "sqlalchemy",
    "pymongo",
    "psycopg",
    "asyncpg",
    "openai",
    "langchain",
    "httpx",
    "requests",
    "fastapi",
    "flask",
    "opentelemetry",
    "prometheus_client",
    "boto3",
    "grpc",
    "redis",
]

#: Modules that form the public boundary surface (scanned statically).
BOUNDARY_MODULES = [
    *sorted((SRC_ROOT / "nl2data").rglob("*.py")),
    SRC_ROOT / "nl2data_core" / "facade.py",
]


_PHASE_SCRIPTS = {
    "import": textwrap.dedent(
        """
        import nl2data  # noqa: F401
        import sys
        print(",".join(sorted({name.split('.')[0] for name in sys.modules})))
        """
    ),
    "construct": textwrap.dedent(
        """
        import sys
        import nl2data  # noqa: F401
        from nl2data import CompositionProfile, create_facade
        create_facade(composition=CompositionProfile())
        print(",".join(sorted({name.split('.')[0] for name in sys.modules})))
        """
    ),
    "initialize": textwrap.dedent(
        """
        import asyncio
        import sys
        import nl2data  # noqa: F401
        from nl2data import NL2Data
        asyncio.run(NL2Data().initialize())
        print(",".join(sorted({name.split('.')[0] for name in sys.modules})))
        """
    ),
}


def _subprocess_loaded_backends(phase: str) -> set[str]:
    """Run one base-usage phase in a clean interpreter and report backends.

    The full test suite legitimately loads optional backends (SQL compiler,
    drivers) through other suites, so sys.modules can only be trusted in a
    fresh subprocess.
    """
    env = {**os.environ, "PYTHONPATH": str(SRC_ROOT)}
    result = subprocess.run(
        [sys.executable, "-c", _PHASE_SCRIPTS[phase]],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return set(result.stdout.strip().split(","))


def _assert_no_backends(loaded: set[str], phase: str) -> None:
    for backend in OPTIONAL_BACKENDS:
        assert backend not in loaded, f"optional backend loaded on {phase}: {backend}"


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


class TestFacadeDynamicBoundary:
    def test_importing_nl2data_loads_no_optional_backends(self) -> None:
        _assert_no_backends(_subprocess_loaded_backends("import"), "import")

    def test_constructing_a_facade_loads_no_optional_backends(self) -> None:
        loaded = _subprocess_loaded_backends("construct")
        _assert_no_backends(loaded, "construction")
        assert "nl2data" in loaded

    def test_initializing_an_empty_facade_loads_no_optional_backends(self) -> None:
        _assert_no_backends(_subprocess_loaded_backends("initialize"), "initialize")


class TestFacadeStaticBoundary:
    def test_boundary_modules_never_import_optional_backends(self) -> None:
        offenders: list[str] = []
        for module_path in BOUNDARY_MODULES:
            imported = _imported_names(module_path)
            for backend in OPTIONAL_BACKENDS:
                if backend in imported:
                    offenders.append(f"{module_path.relative_to(SRC_ROOT)} -> {backend}")
        assert offenders == [], f"forbidden imports found: {offenders}"

    def test_facade_port_annotations_are_public_only(self) -> None:
        """The stable facade port and composition profile expose only public
        types in their signatures - never internal runner, adapter, provider,
        store, or tenant claim types.
        """
        import inspect
        import typing

        from nl2data.composition import CompositionProfile
        from nl2data.facade import FacadePort

        targets: list[tuple[str, object]] = [
            (name, hint) for name, hint in typing.get_type_hints(FacadePort).items()
        ]
        for _, member in vars(FacadePort).items():
            if inspect.isfunction(member):
                targets.extend(typing.get_type_hints(member).items())
        targets.extend(typing.get_type_hints(CompositionProfile).items())

        leaked = [
            f"{name}: {hint}"
            for name, hint in targets
            if "nl2data_core" in str(hint)
        ]
        assert leaked == [], f"internal types leaked into public port: {leaked}"
