"""Compute after-metrics for the semantic control-plane refactor (task 9.3)."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

AREAS = {
    "src/nl2data_core/assembly": None,
    "src/nl2data_core/verification": None,
    "src/nl2data_core/assembly/publishing.py": None,
    "packages/nl2data-admin-service/src/nl2data_admin_service/service.py": None,
    "packages/nl2data-semantic-catalog-postgres/src/"
    "nl2data_semantic_catalog_postgres/store.py": None,
    "packages/nl2data-semantic-catalog-postgres/src/"
    "nl2data_semantic_catalog_postgres/fake_postgres": None,
    "tests": [
        "tests/contract/test_postgres_catalog_contract.py",
        "tests/contract/test_semantic_control_plane_architecture.py",
        "tests/contract/test_semantic_control_plane_characterization.py",
        "tests/integration/test_postgres_catalog_integration.py",
        "tests/security/test_postgres_catalog_import_boundary.py",
        "tests/security/test_postgres_catalog_security.py",
    ],
}

# Cross-domain import inventory for the postgres adapter package.
POSTGRES_PKG = Path(
    "packages/nl2data-semantic-catalog-postgres/src/nl2data_semantic_catalog_postgres"
)


def iter_py(area: str, files: list[str] | None) -> list[Path]:
    if files is not None:
        return [REPO_ROOT / f for f in files]
    p = REPO_ROOT / area
    if p.is_file():
        return [p]
    # Top-level modules only, matching the before-metric methodology
    # (subpackages such as assembly/authoring were never counted).
    return sorted(p.glob("*.py"))


def metrics(paths: list[Path]) -> tuple[int, int, int]:
    lines = 0
    decls = 0
    imports = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        lines += sum(1 for line in text.splitlines() if line.strip())
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                decls += 1
            elif isinstance(node, ast.Import | ast.ImportFrom):
                imports += len(node.names)
    return lines, decls, imports


print("| Area | Files | Physical lines | Declarations | Imports |")
print("| --- | ---: | ---: | ---: | ---: |")
for area, files in AREAS.items():
    paths = iter_py(area, files)
    lines, decls, imps = metrics(paths)
    label = area
    if area == "tests":
        label = "Focused characterization and refactor tests"
    if area.endswith("fake_postgres"):
        label = area.replace(
            "packages/nl2data-semantic-catalog-postgres/src/"
            "nl2data_semantic_catalog_postgres/fake_postgres",
            ".../fake_postgres (package)",
        )
    if area.endswith("store.py") or area.endswith("service.py") or area.endswith("publishing.py"):
        label = ".../" + area.rsplit("/", 1)[-1]
    print(f"| `{label}` | {len(paths)} | {lines} | {decls} | {imps} |")

# Duplicate detection over manifest-scanned roots.
import yaml  # noqa: E402

manifest = yaml.safe_load(
    (REPO_ROOT / "docs/architecture/semantic-control-plane-manifest.yaml").read_text(
        encoding="utf-8"
    )
)
files: set[Path] = set()
for root in manifest["duplicate_detection"]["scanned_roots"]:
    files.update((REPO_ROOT / root).rglob("*.py"))
import hashlib  # noqa: E402

by_hash: dict[str, list[str]] = {}
for path in sorted(files):
    rel = path.relative_to(REPO_ROOT).as_posix()
    digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    by_hash.setdefault(digest, []).append(rel)
dups = [p for p in by_hash.values() if len(p) > 1]
print(f"\nManifest-scanned Python files: {len(files)}")
print(f"Exact duplicate module groups: {len(dups)}")
for group in dups:
    print("  duplicate group:", group)

# `Any` boundary count in the optional adapter packages (design D5: no
# untyped escapes at capability boundaries).
for pkg in (
    "packages/nl2data-admin-service/src/nl2data_admin_service",
    "packages/nl2data-semantic-catalog-postgres/src/nl2data_semantic_catalog_postgres",
):
    any_count = 0
    getattr_count = 0
    for path in (REPO_ROOT / pkg).rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        any_count += text.count("Any")
        getattr_count += text.count("getattr(")
    print(f"{pkg}: Any occurrences={any_count}, getattr calls={getattr_count}")

# Cross-domain imports leaving the postgres adapter package.
outside = set()
for path in POSTGRES_PKG.rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        for m in mods:
            if m.startswith(("nl2data_core", "nl2data_admin_service", "psycopg")):
                outside.add(m)
print("\nPostgres adapter external imports (nl2data_core/admin/driver):")
for m in sorted(outside):
    print(" ", m)
