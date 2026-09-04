"""Architecture checks for the semantic control-plane manifest."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import importlib
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "docs" / "architecture" / "semantic-control-plane-manifest.yaml"


def _manifest() -> dict[str, Any]:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def _module_from_path(path: Path) -> str | None:
    relative = path.relative_to(REPO_ROOT).as_posix()
    roots = {
        "src/": "",
        "packages/nl2data-admin-service/src/": "",
        "packages/nl2data-semantic-catalog-postgres/src/": "",
    }
    for prefix in roots:
        if relative.startswith(prefix):
            module_path = relative.removeprefix(prefix).removesuffix(".py")
            if module_path.endswith("/__init__"):
                module_path = module_path[: -len("/__init__")]
            return module_path.replace("/", ".")
    return None


def _path_from_module(module: str) -> Path:
    candidates = (
        REPO_ROOT / "src" / Path(*module.split(".")).with_suffix(".py"),
        REPO_ROOT
        / "src"
        / Path(*module.split("."))
        / "__init__.py",
        REPO_ROOT
        / "packages"
        / "nl2data-admin-service"
        / "src"
        / Path(*module.split(".")).with_suffix(".py"),
        REPO_ROOT
        / "packages"
        / "nl2data-admin-service"
        / "src"
        / Path(*module.split("."))
        / "__init__.py",
        REPO_ROOT
        / "packages"
        / "nl2data-semantic-catalog-postgres"
        / "src"
        / Path(*module.split(".")).with_suffix(".py"),
        REPO_ROOT
        / "packages"
        / "nl2data-semantic-catalog-postgres"
        / "src"
        / Path(*module.split("."))
        / "__init__.py",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise AssertionError(f"module has no source file: {module}")


def _matches_module(module: str, pattern: str) -> bool:
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return module == prefix or module.startswith(prefix + ".")
    return module == pattern


def _module_matches_any(module: str, patterns: list[str]) -> bool:
    return any(_matches_module(module, pattern) for pattern in patterns)


def _iter_manifest_python_files() -> list[Path]:
    files: set[Path] = set()
    for root in _manifest()["duplicate_detection"]["scanned_roots"]:
        files.update((REPO_ROOT / root).rglob("*.py"))
    return sorted(files)


def _type_checking_spans(tree: ast.Module) -> list[tuple[int, int]]:
    """Line spans of top-level ``if TYPE_CHECKING:`` blocks.

    Those imports never execute at runtime, so they cannot create runtime
    circular imports and are excluded from the import graph.
    """

    def _is_type_checking(test: ast.expr) -> bool:
        return isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"

    return [
        (node.body[0].lineno, node.body[-1].end_lineno or node.body[-1].lineno)
        for node in tree.body
        if isinstance(node, ast.If) and _is_type_checking(node.test)
    ]


def _imports_for(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parts = module.split(".")
    # Relative imports resolve against the containing package: a plain
    # module's package is its parent, while a package's ``__init__.py``
    # belongs to the package itself.
    package = parts if path.name == "__init__.py" else parts[:-1]
    guarded = _type_checking_spans(tree)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if any(start <= node.lineno <= end for start, end in guarded):
            continue
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
            continue
        if node.level:
            base = package[: len(package) - (node.level - 1)]
            target_parts = base + ([node.module] if node.module else [])
        else:
            target_parts = [node.module] if node.module else []
        if not target_parts:
            continue
        target = ".".join(target_parts)
        imports.add(target)
        for alias in node.names:
            imports.add(f"{target}.{alias.name}")
    return imports


def _layer_for(module: str, manifest: dict[str, Any]) -> str | None:
    for layer_name, layer in manifest["layers"].items():
        if _module_matches_any(module, layer["modules"]):
            return layer_name
    return None


def _import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    modules = {
        module
        for path in _iter_manifest_python_files()
        if (module := _module_from_path(path)) is not None
    }
    for path in _iter_manifest_python_files():
        module = _module_from_path(path)
        if module is None:
            continue
        graph[module] = {
            imported
            for imported in _imports_for(path, module)
            if any(imported == item or imported.startswith(item + ".") for item in modules)
        }
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(module: str) -> list[str]:
        if module in visiting:
            return stack[stack.index(module) :] + [module]
        if module in visited:
            return []
        visiting.add(module)
        stack.append(module)
        for imported in sorted(graph.get(module, ())):
            cycle = visit(imported)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(module)
        visited.add(module)
        return []

    for module in sorted(graph):
        cycle = visit(module)
        if cycle:
            return cycle
    return []


def _class_method_names(module: ModuleType) -> set[str]:
    path = Path(module.__file__ or "")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    methods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            methods.update(
                item.name
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            )
    return methods


def test_architecture_manifest_is_versioned_and_complete() -> None:
    manifest = _manifest()
    assert manifest["schema_version"] == 1
    assert manifest["name"] == "semantic-control-plane"
    assert manifest["canonical_owners"]
    assert manifest["canonicalization"]
    assert manifest["compatibility_reexports"]
    assert manifest["layers"]
    assert manifest["prohibited_imports"]
    assert manifest["port_declarations"]
    assert manifest["hotspot_budgets"]
    assert manifest["duplicate_detection"]["exact_duplicate_allowlist"] == []


def test_canonicalization_owner_is_declared_and_resolves() -> None:
    """The single canonicalization owner exists with its strict contract."""
    owner = _manifest()["canonicalization"]["owner"]
    module = importlib.import_module(owner["module"])
    for symbol in [*owner["strict_symbols"], *owner["compatibility_symbols"]]:
        assert hasattr(module, symbol), f"{owner['module']} is missing {symbol}"


def _source_module(path: Path) -> str | None:
    """Resolve a source file under src/ or packages/*/src/ to its module."""
    relative = path.relative_to(REPO_ROOT).as_posix()
    if relative.startswith("src/"):
        candidate = relative.removeprefix("src/")
    else:
        parts = relative.split("/")
        if len(parts) > 3 and parts[0] == "packages" and parts[2] == "src":
            candidate = "/".join(parts[3:])
        else:
            return None
    candidate = candidate.removesuffix(".py")
    if candidate.endswith("/__init__"):
        candidate = candidate[: -len("/__init__")]
    return candidate.replace("/", ".")


def _canonicalization_scan_files() -> list[Path]:
    """Every runtime source file, core and optional packages alike."""
    roots = [REPO_ROOT / "src" / "nl2data_core", REPO_ROOT / "src" / "nl2data"]
    roots.extend(sorted((REPO_ROOT / "packages").glob("*/src/*")))
    files: set[Path] = set()
    for root in roots:
        files.update(root.rglob("*.py"))
    return sorted(files)


def _calls_with_keyword(tree: ast.Module, func_attr: str, keyword: str) -> list[str]:
    """Rendered source snippets of calls passing an explicit keyword."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != func_attr:
            continue
        if any(kw.arg == keyword for kw in node.keywords):
            hits.append(ast.unparse(node))
    return hits


def test_local_hashing_is_confined_to_the_canonicalization_owner() -> None:
    """No source module may compute digests outside the declared owner."""
    owner = _manifest()["canonicalization"]["owner"]["module"]
    offenders: list[str] = []
    for path in _canonicalization_scan_files():
        module = _source_module(path)
        if module is None or module == owner:
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        imports_hashlib = any(
            isinstance(node, ast.Import)
            and any(alias.name == "hashlib" for alias in node.names)
            or isinstance(node, ast.ImportFrom)
            and node.module == "hashlib"
            for node in ast.walk(tree)
        )
        if imports_hashlib:
            offenders.append(module)
    assert not offenders, f"local digest computation outside {owner}: {offenders}"


def test_deterministic_serializers_are_allowlisted_presentation_only() -> None:
    """Duplicate canonical serializers need an explicit presentation entry."""
    canonicalization = _manifest()["canonicalization"]
    owner = canonicalization["owner"]["module"]
    allowlist = {
        entry["module"] for entry in canonicalization["presentation_only_allowlist"]
    }
    offenders: list[str] = []
    for path in _canonicalization_scan_files():
        module = _source_module(path)
        if module is None or module == owner or module in allowlist:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _calls_with_keyword(tree, "dumps", "sort_keys"):
            offenders.append(module)
    assert not offenders, (
        "deterministic serializers outside the owner need a manifest "
        f"presentation_only_allowlist entry: {offenders}"
    )


def test_canonical_owner_symbols_resolve() -> None:
    for owner in _manifest()["canonical_owners"].values():
        module = importlib.import_module(owner["module"])
        for symbol in owner["symbols"]:
            assert hasattr(module, symbol), f"{owner['module']} is missing {symbol}"


def test_manifest_port_declarations_resolve_to_methods() -> None:
    for port_name, declaration in _manifest()["port_declarations"].items():
        module = importlib.import_module(declaration["owner"])
        available = _class_method_names(module)
        missing = set(declaration["required_methods"]) - available
        assert not missing, f"{port_name} is missing declared methods: {sorted(missing)}"


def test_catalog_ports_expose_aggregate_only_publish_boundary() -> None:
    """Catalog publish ports accept one PublicationAggregate, never exploded fields."""
    port_paths = (
        REPO_ROOT
        / "src"
        / "nl2data_core"
        / "control_plane"
        / "publication"
        / "ports.py",
        REPO_ROOT
        / "packages"
        / "nl2data-admin-service"
        / "src"
        / "nl2data_admin_service"
        / "protocols.py",
    )
    exploded_parameters = {
        "accepted_assertion_manifest",
        "audit",
        "verification_evidence",
    }
    for path in port_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name != "publish":
                continue
            arguments = node.args
            names = {
                arg.arg
                for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
            }
            leaked = exploded_parameters & names
            assert not leaked, f"{path.name} publish exposes exploded parameters: {sorted(leaked)}"
            assert "publication_aggregate" in names, (
                f"{path.name} publish must accept the publication aggregate"
            )
            # The aggregate is the only content parameter; forbid an optional one.
            assert "PublicationAggregate | None" not in ast.unparse(node), (
                f"{path.name} publish aggregate must be required, not nullable"
            )


def test_manifest_import_graph_has_no_cycles() -> None:
    assert not _find_cycle(_import_graph())


def test_manifest_layer_imports_are_approved() -> None:
    manifest = _manifest()
    graph = _import_graph()
    violations: list[str] = []
    for module, imports in graph.items():
        source_layer = _layer_for(module, manifest)
        if source_layer is None:
            continue
        allowed_layers = set(manifest["layers"][source_layer]["may_import"])
        for imported in imports:
            imported_layer = _layer_for(imported, manifest)
            if imported_layer is not None and imported_layer not in allowed_layers:
                violations.append(f"{module} -> {imported}")
    assert not violations


def test_manifest_prohibited_imports_are_absent() -> None:
    manifest = _manifest()
    violations: list[str] = []
    for path in _iter_manifest_python_files():
        module = _module_from_path(path)
        if module is None:
            continue
        imports = _imports_for(path, module)
        for rule_name, rule in manifest["prohibited_imports"].items():
            if not _module_matches_any(module, rule["from"]):
                continue
            for imported in imports:
                if _module_matches_any(imported, rule["to"]):
                    violations.append(f"{rule_name}: {module} -> {imported}")
    assert not violations


def test_manifest_exact_duplicate_modules_are_absent() -> None:
    manifest = _manifest()
    allowlist = set(manifest["duplicate_detection"]["exact_duplicate_allowlist"])
    by_hash: dict[str, list[str]] = defaultdict(list)
    for path in _iter_manifest_python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if any(fnmatch.fnmatch(relative, pattern) for pattern in allowlist):
            continue
        digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        by_hash[digest].append(relative)
    duplicates = [paths for paths in by_hash.values() if len(paths) > 1]
    assert not duplicates


def _physical_lines(path: Path) -> int:
    """Count the lines a hotspot budget measures.

    ``metrics.md`` defines the project's "physical lines" methodology as
    non-empty source lines, so blank separators do not consume budget;
    the manifest declares this metric and the tests below assert it.
    """
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)


def test_imports_resolve_relative_imports(tmp_path: Path) -> None:
    """Relative imports resolve to absolute modules so edges cannot hide."""
    module = tmp_path / "mod.py"
    module.write_text(
        "from . import dtos\n"
        "from .sibling import Thing\n"
        "from ..bundles import catalog\n"
        "from ...verification import suite\n",
        encoding="utf-8",
    )
    imports = _imports_for(module, "nl2data_core.control_plane.publication.mod")
    assert "nl2data_core.control_plane.publication.dtos" in imports
    assert "nl2data_core.control_plane.publication.sibling.Thing" in imports
    assert "nl2data_core.control_plane.bundles.catalog" in imports
    assert "nl2data_core.verification.suite" in imports


def test_imports_resolve_relative_imports_in_package_init(tmp_path: Path) -> None:
    package = tmp_path / "__init__.py"
    package.write_text("from . import dtos\n", encoding="utf-8")
    imports = _imports_for(package, "nl2data_admin_service")
    assert "nl2data_admin_service.dtos" in imports


def test_imports_exclude_type_checking_guards(tmp_path: Path) -> None:
    """TYPE_CHECKING-only imports never execute, so they are not edges."""
    module = tmp_path / "mod.py"
    module.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .pool import FakePostgresPool\n"
        "    from .driver import _FakeConnection\n"
        "from .keys import _event_key\n",
        encoding="utf-8",
    )
    imports = _imports_for(module, "nl2data_semantic_catalog_postgres.fake.mod")
    assert imports == {
        "typing",
        "typing.TYPE_CHECKING",
        "nl2data_semantic_catalog_postgres.fake.keys",
        "nl2data_semantic_catalog_postgres.fake.keys._event_key",
    }


def test_physical_line_budgets_measure_non_empty_lines(tmp_path: Path) -> None:
    padded = tmp_path / "padded.py"
    padded.write_text("x = 1\n\n\n# comment\ny = 2\n", encoding="utf-8")
    assert _physical_lines(padded) == 3


def test_manifest_physical_line_budgets_are_met() -> None:
    budgets = _manifest()["hotspot_budgets"]
    assert budgets["metric"] == "non_empty_lines"
    failures: list[str] = []
    for relative, budget in budgets["physical_lines"].items():
        count = _physical_lines(REPO_ROOT / relative)
        if count > budget:
            failures.append(f"{relative}: {count} > {budget}")
    assert not failures


def test_manifest_pattern_line_budgets_are_met() -> None:
    manifest = _manifest()
    budgets = manifest["hotspot_budgets"]["patterns"]
    globs = manifest["hotspot_budgets"]["pattern_globs"]
    assert set(budgets) == set(globs), "every pattern budget needs a glob"
    failures: list[str] = []
    matched: set[str] = set()
    for pattern, budget in budgets.items():
        for path in sorted(REPO_ROOT.glob(globs[pattern])):
            relative = path.relative_to(REPO_ROOT).as_posix()
            matched.add(relative)
            if _physical_lines(path) > budget:
                failures.append(f"{relative}: {_physical_lines(path)} > {budget}")
    assert not failures
    assert matched, "pattern budget globs must match at least one module"


def _domain_prefixes(manifest: dict[str, Any]) -> dict[str, str]:
    """Map each counted root directory to its module-domain prefix."""
    prefixes: dict[str, str] = {}
    for root in manifest["cross_domain_import_budget"]["counted_roots"]:
        prefix = _module_from_path(REPO_ROOT / root / "__init__.py")
        assert prefix is not None, f"counted root has no package module: {root}"
        prefixes[root] = prefix
    return prefixes


def _domain_of_module(module: str, prefixes: dict[str, str]) -> str | None:
    best: str | None = None
    for prefix in prefixes.values():
        if (
            module == prefix or module.startswith(prefix + ".")
        ) and (best is None or len(prefix) > len(best)):
            best = prefix
    return best


def test_manifest_cross_domain_import_budget_is_met() -> None:
    manifest = _manifest()
    budget = manifest["cross_domain_import_budget"]
    prefixes = _domain_prefixes(manifest)
    edges: set[tuple[str, str]] = set()
    for path in _iter_manifest_python_files():
        module = _module_from_path(path)
        if module is None:
            continue
        source_domain = _domain_of_module(module, prefixes)
        if source_domain is None:
            continue
        for imported in _imports_for(path, module):
            imported_domain = _domain_of_module(imported, prefixes)
            if imported_domain is not None and imported_domain != source_domain:
                edges.add((module, imported))
    allowed = budget["baseline"] + budget["max_new_imports"]
    assert len(edges) <= allowed, (
        f"cross-domain imports grew: {len(edges)} > {allowed} "
        "(record new edges in the manifest baseline with justification)"
    )