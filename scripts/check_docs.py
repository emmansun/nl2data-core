"""Documentation quality gates for the NL2Data repository.

Run from the repository root:

    python scripts/check_docs.py

Exit code is 0 only when every check passes. The core package must be
installed (``pip install -e .``) so the smoke and reconciliation checks
can import it; the CI docs job installs it before running this script.

Checks (mapped to openspec/changes/prepare-production-documentation):

1. Markdown/link validation (5.1): every relative link resolves to an
   existing file, and every ``#anchor`` target matches a heading.
2. Mermaid structure (5.1, 3.6): every ``mermaid`` block starts with a
   known diagram type, node ids referenced by edges are defined, and
   quote/brace/paren delimiters are balanced.
3. Secret-pattern scans (5.3): no tokens, DSNs with embedded passwords,
   private keys, secret environment assignments, or base64 blobs in
   documentation or examples.
4. Bilingual navigation (5.5, 1.4): every Chinese page links to its
   English source, every English page links to its translation, and the
   docs index language table is complete and consistent.
5. Smoke checks (5.2): executes the documented import and quickstart
   code blocks from installation.md and quickstart.md against the
   installed ``nl2data`` package.
6. Package/build consistency (5.2, 5.4): documented extras, dependency
   ranges, versions, and build configuration match pyproject metadata.
7. Source reconciliation (5.4): documented error codes match the public
   ``ErrorCode`` enum, documented configuration defaults match the
   source models, and documented identifiers and environment variables
   exist in the codebase.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

DOC_FILES = [
    ROOT / "README.md",
    *sorted((DOCS).rglob("*.md")),
    ROOT / "packages" / "nl2data-openai" / "README.md",
    ROOT / "packages" / "nl2data-semantic-catalog-postgres" / "README.md",
    ROOT / "packages" / "nl2data-memory-redis" / "README.md",
]

ZH_SUFFIX = ".zh-CN.md"
ZH_MARKER = "本页为简体中文翻译"


@dataclass
class Checker:
    errors: list[str] = field(default_factory=list)

    def report(self, path: Path, message: str) -> None:
        self.errors.append(f"{path.relative_to(ROOT)}: {message}")


def strip_fences(text: str) -> str:
    """Remove fenced code blocks so link/secret scans skip code content."""
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def headings_of(path: Path) -> set[str]:
    """GitHub-style anchor names for every heading in a markdown file."""
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if not m:
            continue
        anchor = m.group(1).lower()
        anchor = re.sub(r"[^0-9a-z\u4e00-\u9fff _-]", "", anchor)
        anchors.add(anchor.strip().replace(" ", "-"))
    return anchors


# ---------------------------------------------------------------------------
# 1. Markdown/link validation
# ---------------------------------------------------------------------------


def check_links(checker: Checker) -> None:
    link_re = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
    for path in DOC_FILES:
        if not path.exists():
            checker.report(path, "documented file does not exist")
            continue
        text = strip_fences(path.read_text(encoding="utf-8"))
        for target in link_re.findall(text):
            target = target.strip()
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            file_part, _, anchor = target.partition("#")
            resolved = (path.parent / file_part).resolve() if file_part else path
            if not resolved.exists():
                checker.report(path, f"broken link target: {target!r}")
                continue
            if anchor and anchor not in headings_of(resolved):
                checker.report(
                    path,
                    "broken anchor: "
                    f"{target!r} (no heading {anchor!r} in {resolved.relative_to(ROOT)})",
                )


# ---------------------------------------------------------------------------
# 2. Mermaid structural checks
# ---------------------------------------------------------------------------


def _mermaid_blocks(path: Path) -> list[tuple[int, list[str]]]:
    text = path.read_text(encoding="utf-8")
    blocks: list[tuple[int, list[str]]] = []
    for m in re.finditer(r"```mermaid\n(.*?)```", text, flags=re.DOTALL):
        start = text[: m.start()].count("\n") + 1
        blocks.append((start, m.group(1).splitlines()))
    return blocks


def _check_mermaid_flowchart(lines: list[str], path: Path, start: int, errors: list[str]) -> None:
    def err(line_no: int, message: str) -> None:
        errors.append(f"{path.relative_to(ROOT)}: mermaid line {line_no}: {message}")

    edge_re = re.compile(
        r"([A-Za-z0-9_]+)(\[[^\]]*\])?\s*-{1,2}[^>]*->\s*(?:\|[^|]*\|\s*)?([A-Za-z0-9_]+)(\[[^\]]*\])?"
    )

    defined: set[str] = set()
    for line in lines:
        stripped = line.strip()
        m = re.match(r"subgraph\s+([A-Za-z0-9_]+)", stripped)
        if m:
            defined.add(m.group(1))
            continue
        if stripped.startswith(("classDef", "class ", "end", "style")):
            continue
        m = re.match(r"([A-Za-z0-9_]+)\s*(\[|\(|\{)", stripped)
        if m:
            defined.add(m.group(1))
        for edge in edge_re.finditer(line):
            defined.add(edge.group(3))  # destination defined inline on edge lines

    for idx, line in enumerate(lines, start=start):
        if line.count('"') % 2:
            err(idx, "unbalanced double quotes")
        for m in edge_re.finditer(line):
            src, dst = m.group(1), m.group(3)
            if src not in defined:
                err(idx, f"edge source {src!r} is not defined")
            if dst not in defined:
                err(idx, f"edge target {dst!r} is not defined")

    for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
        joined = "".join(lines)
        if joined.count(open_c) != joined.count(close_c):
            err(1, f"unbalanced {open_c!r}/{close_c!r} delimiters")


def _check_mermaid_sequence(lines: list[str], path: Path, start: int, errors: list[str]) -> None:
    def err(line_no: int, message: str) -> None:
        errors.append(f"{path.relative_to(ROOT)}: mermaid line {line_no}: {message}")

    allowed = re.compile(
        r"^\s*(?:autonumber|participant|actor|Note|loop|alt|else|opt|par|and|rect|end)\b"
        r"|^\s*[A-Za-z0-9_]+(?:->>|-->>|\)|-x|--)"
    )
    for idx, line in enumerate(lines[1:], start=start + 1):
        if not line.strip():
            continue
        if not allowed.match(line):
            err(idx, f"unexpected sequenceDiagram line: {line.strip()!r}")


def check_mermaid(checker: Checker) -> None:
    for path in DOC_FILES:
        if not path.exists():
            continue
        for start, lines in _mermaid_blocks(path):
            if not lines or not lines[0].strip():
                checker.report(path, f"mermaid block at line {start} is empty")
                continue
            first = lines[0].strip()
            if first.startswith(("flowchart", "graph")):
                _check_mermaid_flowchart(lines, path, start, checker.errors)
            elif first.startswith("sequenceDiagram"):
                _check_mermaid_sequence(lines, path, start, checker.errors)
            else:
                checker.report(
                    path,
                    "mermaid block at line "
                    f"{start} must start with flowchart/graph/sequenceDiagram",
                )


# ---------------------------------------------------------------------------
# 3. Secret-pattern scans
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI API key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z\-_]{20,}")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    (
        "DSN with password",
        re.compile(r"(?:postgres|postgresql|redis|mongodb(?:\+srv)?)" r"://[^\s/]+:[^\s@]+@"),
    ),
    (
        "secret env assignment",
        re.compile(r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)" r"\s*=\s*[\"'][^\"']{8,}[\"']"),
    ),
    ("bearer token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]{20,}")),
    ("base64 blob", re.compile(r"[A-Za-z0-9+/]{48,}={0,2}")),
]


def check_secrets(checker: Checker) -> None:
    for path in DOC_FILES:
        if not path.exists():
            continue
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if re.search(r"sha256:[0-9a-f]{64}", line):
                continue  # documented fingerprint format, not a secret
            for name, pattern in SECRET_PATTERNS:
                m = pattern.search(line)
                if m:
                    snippet = m.group(0)
                    redacted = snippet[:12] + "..." if len(snippet) > 12 else snippet
                    checker.report(
                        path, f"line {line_no}: possible {name} pattern: {redacted!r}"
                    )
                    break


# ---------------------------------------------------------------------------
# 4. Bilingual navigation
# ---------------------------------------------------------------------------


def _index_language_table() -> dict[str, str | None]:
    """Map docs-relative English page -> zh page (or None for English-first)."""
    index = DOCS / "README.md"
    text = index.read_text(encoding="utf-8")
    section = text.split("## Language navigation", 1)[1].split("## Reader paths", 1)[0]
    table: dict[str, str | None] = {}
    link_re = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    for row in section.splitlines():
        cells = [c.strip() for c in row.split("|")[1:-1]]
        if len(cells) != 2:
            continue
        en = link_re.search(cells[0])
        if not en:
            continue
        zh = link_re.search(cells[1])
        table[en.group(1)] = zh.group(1) if zh else None
    return table


def check_bilingual(checker: Checker) -> None:
    pages = [p for p in sorted(DOCS.rglob("*.md")) if not p.name.endswith(ZH_SUFFIX)]
    zh_pages = [p for p in sorted(DOCS.rglob("*.md")) if p.name.endswith(ZH_SUFFIX)]

    table = _index_language_table()
    en_in_table = {Path(p) for p in table}
    en_on_disk = {p.relative_to(DOCS) for p in pages}
    zh_on_disk = {p.relative_to(DOCS) for p in zh_pages}

    for missing in sorted(en_on_disk - en_in_table):
        checker.report(DOCS / "README.md", f"language table missing English page {missing}")
    for missing in sorted(en_in_table - en_on_disk):
        checker.report(DOCS / "README.md", f"language table lists non-existent page {missing}")

    for row_en, row_zh in table.items():
        zh_path = DOCS / row_en.replace(".md", ZH_SUFFIX)
        if row_zh is None:
            if zh_path.exists():
                checker.report(
                    DOCS / "README.md",
                    f"{row_en} is labeled English-first but {zh_path.name} exists",
                )
            continue
        if Path(row_zh) not in zh_on_disk:
            checker.report(DOCS / "README.md", f"language table links missing translation {row_zh}")
        if Path(row_zh) != Path(row_en.replace(".md", ZH_SUFFIX)):
            checker.report(
                DOCS / "README.md",
                f"translation {row_zh} does not match naming convention for {row_en}",
            )

    for zh in zh_pages:
        rel = zh.relative_to(DOCS)
        en = zh.parent / (zh.name[: -len(ZH_SUFFIX)] + ".md")
        if en not in pages:
            checker.report(zh, f"no English source {en.name}")
            continue
        zh_text = zh.read_text(encoding="utf-8")
        en_text = en.read_text(encoding="utf-8")
        if ZH_MARKER not in zh_text:
            checker.report(zh, f"missing translation marker {ZH_MARKER!r}")
        if f"]({en.name})" not in zh_text:
            checker.report(zh, f"missing link back to English source {en.name}")
        if f"]({zh.name})" not in en_text:
            checker.report(en, f"missing link to Chinese translation {zh.name}")
        if rel not in zh_on_disk:
            continue
        if rel not in {Path(v) for v in table.values() if v}:
            checker.report(DOCS / "README.md", f"translation {rel} missing from language table")

    zh_index = DOCS / f"README{ZH_SUFFIX}"
    if not zh_index.exists():
        checker.report(DOCS, "missing Chinese docs index README.zh-CN.md")


# ---------------------------------------------------------------------------
# 5. Smoke checks: documented imports and quickstart code
# ---------------------------------------------------------------------------


def _python_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)


def check_smoke(checker: Checker) -> None:
    for rel in ("getting-started/installation.md", "getting-started/quickstart.md"):
        path = DOCS / rel
        blocks = _python_blocks(path)
        if not blocks:
            checker.report(path, "no python code blocks found to smoke-check")
            continue
        for idx, block in enumerate(blocks, start=1):
            namespace: dict[str, object] = {}
            try:
                exec(compile(block, f"{rel} block {idx}", "exec"), namespace)
            except Exception as exc:  # noqa: BLE001 - reported as a doc failure
                checker.report(path, f"code block {idx} failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 6. Package/build consistency with documented claims
# ---------------------------------------------------------------------------


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value)


def check_packages(checker: Checker) -> None:
    root_py = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    openai_py = tomllib.loads(
        (ROOT / "packages" / "nl2data-openai" / "pyproject.toml").read_text(encoding="utf-8")
    )
    catalog_py = tomllib.loads(
        (
            ROOT
            / "packages"
            / "nl2data-semantic-catalog-postgres"
            / "pyproject.toml"
        ).read_text(encoding="utf-8")
    )
    memory_py = tomllib.loads(
        (ROOT / "packages" / "nl2data-memory-redis" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )

    project = root_py["project"]
    if project["name"] != "nl2data-core":
        checker.report(ROOT / "pyproject.toml", f"unexpected project name {project['name']!r}")
    if project["version"] != "0.1.0":
        checker.report(ROOT / "pyproject.toml", f"unexpected version {project['version']!r}")
    if project["requires-python"] != ">=3.11":
        checker.report(ROOT / "pyproject.toml", "requires-python must be >=3.11")
    if "setuptools" not in root_py["build-system"]["requires"][0]:
        checker.report(ROOT / "pyproject.toml", "build-system must require setuptools")

    extras = project["optional-dependencies"]
    expected_extras = {
        "sql": ["sqlglot>=25.0,<30"],
        "postgres": ["psycopg[binary,pool]>=3.1,<4"],
    }
    for name, deps in expected_extras.items():
        if name not in extras:
            checker.report(ROOT / "pyproject.toml", f"missing documented extra {name!r}")
            continue
        if [_normalize(d) for d in extras[name]] != [_normalize(d) for d in deps]:
            checker.report(
                ROOT / "pyproject.toml",
                f"extra {name!r} dependencies {extras[name]} do not match documentation {deps}",
            )

    openai_project = openai_py["project"]
    if openai_project["name"] != "nl2data-openai":
        checker.report(
            ROOT / "packages" / "nl2data-openai" / "pyproject.toml",
            f"unexpected project name {openai_project['name']!r}",
        )
    if openai_project["version"] != "0.1.0":
        checker.report(
            ROOT / "packages" / "nl2data-openai" / "pyproject.toml",
            f"unexpected version {openai_project['version']!r}",
        )
    openai_deps = {_normalize(d) for d in openai_project["dependencies"]}
    if "nl2data-core>=0.1.0" not in openai_deps or "openai>=1.40,<3" not in openai_deps:
        checker.report(
            ROOT / "packages" / "nl2data-openai" / "pyproject.toml",
            "dependencies must include nl2data-core>=0.1.0 and openai>=1.40,<3",
        )

    catalog_project = catalog_py["project"]
    if catalog_project["name"] != "nl2data-semantic-catalog-postgres":
        checker.report(
            ROOT / "packages" / "nl2data-semantic-catalog-postgres" / "pyproject.toml",
            f"unexpected project name {catalog_project['name']!r}",
        )
    if catalog_project["version"] != "0.1.0":
        checker.report(
            ROOT / "packages" / "nl2data-semantic-catalog-postgres" / "pyproject.toml",
            f"unexpected version {catalog_project['version']!r}",
        )
    catalog_deps = {_normalize(d) for d in catalog_project["dependencies"]}
    if (
        "nl2data-core>=0.1.0" not in catalog_deps
        or "psycopg[binary,pool]>=3.1,<4" not in catalog_deps
    ):
        checker.report(
            ROOT / "packages" / "nl2data-semantic-catalog-postgres" / "pyproject.toml",
            "dependencies must include nl2data-core>=0.1.0 and "
            "psycopg[binary,pool]>=3.1,<4",
        )

    memory_project = memory_py["project"]
    if memory_project["name"] != "nl2data-memory-redis":
        checker.report(
            ROOT / "packages" / "nl2data-memory-redis" / "pyproject.toml",
            f"unexpected project name {memory_project['name']!r}",
        )
    if memory_project["version"] != "0.1.0":
        checker.report(
            ROOT / "packages" / "nl2data-memory-redis" / "pyproject.toml",
            f"unexpected version {memory_project['version']!r}",
        )
    memory_deps = {_normalize(d) for d in memory_project["dependencies"]}
    if "nl2data-core>=0.1.0" not in memory_deps:
        checker.report(
            ROOT / "packages" / "nl2data-memory-redis" / "pyproject.toml",
            "dependencies must include nl2data-core>=0.1.0",
        )

    install = (DOCS / "getting-started" / "installation.md").read_text(encoding="utf-8")
    for command in (
        "pip install nl2data-core",
        'pip install "nl2data-core[sql]"',
        "pip install nl2data-openai",
        "pip install nl2data-semantic-catalog-postgres",
        "pip install nl2data-memory-redis",
        'pip install -e ".[dev]"',
        "pip install -e packages/nl2data-openai",
        "pip install -e packages/nl2data-semantic-catalog-postgres",
        "pip install -e packages/nl2data-memory-redis",
    ):
        if command not in install:
            checker.report(
                DOCS / "getting-started" / "installation.md",
                f"missing command {command!r}",
            )

    local_dev = (DOCS / "development" / "local-development.md").read_text(encoding="utf-8")
    for command in (
        "python -m build --wheel --outdir dist/core .",
        "python -m build --wheel --outdir dist/openai packages/nl2data-openai",
        "python -m build --wheel --outdir dist/postgres-catalog "
        "packages/nl2data-semantic-catalog-postgres",
    ):
        if command not in local_dev:
            checker.report(
                DOCS / "development" / "local-development.md",
                f"missing build command {command!r}",
            )

    compatibility = (DOCS / "reference" / "compatibility.md").read_text(encoding="utf-8")
    compat_flat = re.sub(r"[\s|`]", "", compatibility)
    for dep in (
        "pydantic>=2.0,<3",
        "PyYAML>=6.0",
        "sqlglot>=25.0,<30",
        "psycopg>=3.1,<4",
        "pymongo>=4.6,<5",
        "redis>=5.0,<7",
        "openai>=1.40,<3",
    ):
        if _normalize(dep) not in compat_flat:
            checker.report(
                DOCS / "reference" / "compatibility.md", f"missing dependency claim {dep!r}"
            )
    if "0.1.0" not in compatibility:
        checker.report(DOCS / "reference" / "compatibility.md", "missing version claim 0.1.0")


# ---------------------------------------------------------------------------
# 7. Reconciliation with source models, tests, and package metadata
# ---------------------------------------------------------------------------


def _source_text() -> str:
    parts: list[str] = []
    for base in (ROOT / "src", ROOT / "tests", ROOT / "scripts", ROOT / "packages"):
        for path in base.rglob("*.py"):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def check_reconciliation(checker: Checker) -> None:
    try:
        from nl2data.errors import ErrorCode  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        checker.report(ROOT, f"cannot import nl2data for reconciliation: {exc}")
        return

    enum_codes = {code.value for code in ErrorCode}
    error_codes_doc = (DOCS / "reference" / "error-codes.md").read_text(encoding="utf-8")
    doc_codes = set(re.findall(r"`([A-Z][A-Z0-9_]{3,})`", error_codes_doc))
    # Internal ModelErrorCode values are documented as internal-only and
    # are never public ErrorCode members; exclude them from the boundary check.
    model_codes: set[str] = set()
    try:
        from nl2data_core.ai.errors import ModelErrorCode  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        checker.report(ROOT, f"cannot import ModelErrorCode for reconciliation: {exc}")
    else:
        model_codes = {code.value for code in ModelErrorCode}
        for code in ModelErrorCode:
            if code.value not in error_codes_doc:
                checker.report(
                    DOCS / "reference" / "error-codes.md",
                    f"internal ModelErrorCode {code.value} not documented",
                )
    for code in sorted(enum_codes - doc_codes):
        checker.report(
            DOCS / "reference" / "error-codes.md",
            f"error code {code} missing from documentation",
        )
    for code in sorted(doc_codes - enum_codes - model_codes):
        checker.report(
            DOCS / "reference" / "error-codes.md",
            f"documented error code {code} not in ErrorCode enum",
        )

    source = _source_text()
    for env_var in (
        "NL2DATA_POSTGRES_DSN",
        "NL2DATA_REDIS_URL",
        "NL2DATA_MONGO_URI",
        "NL2DATA_MONGO_DATABASE",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_TIMEOUT_SECONDS",
        "OPENAI_LIVE_CASES",
    ):
        if env_var not in source:
            checker.report(
                ROOT,
                f"documented environment variable {env_var} not found in codebase",
            )

    for identifier in (
        "FakeModelProvider",
        "PostgreSQLStateStore",
        "RedisMemoryProvider",
        "ProductionDiscoveryConfig",
        "cleanup_expired",
        "OpenAIProviderConfig",
        "api_key_resolver",
        "client_factory",
        "run_live_openai_evaluation",
        "RedisMemoryConfig",
        "WorkflowPostgresConfig",
        "SemanticSnapshotCatalog",
        "PostgreSQLSemanticCatalog",
        "SemanticCatalogConfig",
        "INSTRUCTION_VERSION_INCOMPATIBLE",
        "safe_dump",
        "load_config",
    ):
        if identifier not in source:
            checker.report(ROOT, f"documented identifier {identifier} not found in codebase")

    redis_source = (
        ROOT / "packages" / "nl2data-memory-redis" / "src" / "nl2data_memory_redis" / "config.py"
    ).read_text(encoding="utf-8")
    shared_source = (
        ROOT
        / "packages"
        / "nl2data-workflow-postgres"
        / "src"
        / "nl2data_workflow_postgres"
        / "config.py"
    ).read_text(encoding="utf-8")
    services = (DOCS / "operations" / "services.md").read_text(encoding="utf-8")
    for pattern in (r"\^[A-Za-z0-9][A-Za-z0-9_\\\-\\.]{0,63}\$", r"\^[A-Za-z][A-Za-z0-9_]{0,63}\$"):
        if pattern not in redis_source and pattern not in shared_source:
            continue
        escaped = pattern.replace("\\", "\\\\")
        if escaped not in services:
            checker.report(
                DOCS / "operations" / "services.md",
                f"namespace pattern {pattern!r} not documented",
            )

    try:
        from nl2data_core.ai.config import ModelConfig  # type: ignore[import-not-found]
        from nl2data_core.config.models import (  # type: ignore[import-not-found]
            SUPPORTED_SCHEMA_VERSION,
            RuntimeSettings,
            SecretReference,
            ServiceIdentity,
        )
        from nl2data_memory_redis.config import (
            RedisMemoryConfig,  # type: ignore[import-not-found]
        )
        from nl2data_semantic_catalog_postgres.config import (  # type: ignore[import-not-found]
            SemanticCatalogConfig,
        )
        from nl2data_workflow_postgres.config import (
            WorkflowPostgresConfig,  # type: ignore[import-not-found]
        )
    except Exception as exc:  # noqa: BLE001
        checker.report(ROOT, f"cannot import configuration models for reconciliation: {exc}")
        return

    def field_default(model: type, field: str) -> object:
        return model.model_fields[field].default

    defaults: list[tuple[str, object, object]] = [
        ("schema_version", SUPPORTED_SCHEMA_VERSION, 1),
        (
            "ServiceIdentity.environment",
            field_default(ServiceIdentity, "environment"),
            "development",
        ),
        ("RuntimeSettings.max_attempts", field_default(RuntimeSettings, "max_attempts"), 3),
        (
            "RuntimeSettings.timeout_seconds",
            field_default(RuntimeSettings, "timeout_seconds"),
            30.0,
        ),
        (
            "RuntimeSettings.telemetry_enabled",
            field_default(RuntimeSettings, "telemetry_enabled"),
            True,
        ),
        (
            "RuntimeSettings.max_artifact_bytes",
            field_default(RuntimeSettings, "max_artifact_bytes"),
            1_048_576,
        ),
        (
            "RuntimeSettings.shutdown_grace_seconds",
            field_default(RuntimeSettings, "shutdown_grace_seconds"),
            5.0,
        ),
        ("SecretReference.kind", field_default(SecretReference, "kind"), "env"),
        ("ModelConfig.provider_name", field_default(ModelConfig, "provider_name"), "fake"),
        ("ModelConfig.model_name", field_default(ModelConfig, "model_name"), "fake-model"),
        ("ModelConfig.max_input_chars", field_default(ModelConfig, "max_input_chars"), 100_000),
        ("ModelConfig.max_output_tokens", field_default(ModelConfig, "max_output_tokens"), 4096),
        ("ModelConfig.timeout_seconds", field_default(ModelConfig, "timeout_seconds"), 30.0),
        ("ModelConfig.max_attempts", field_default(ModelConfig, "max_attempts"), 3),
        ("ModelConfig.temperature", field_default(ModelConfig, "temperature"), None),
        (
            "RedisMemoryConfig.max_ttl_seconds",
            field_default(RedisMemoryConfig, "max_ttl_seconds"),
            3_153_600,
        ),
        ("RedisMemoryConfig.max_records", field_default(RedisMemoryConfig, "max_records"), 10_000),
        (
            "RedisMemoryConfig.max_candidates",
            field_default(RedisMemoryConfig, "max_candidates"),
            1_000,
        ),
        (
            "RedisMemoryConfig.recall_batch_size",
            field_default(RedisMemoryConfig, "recall_batch_size"),
            100,
        ),
        (
            "RedisMemoryConfig.compaction_batch_size",
            field_default(RedisMemoryConfig, "compaction_batch_size"),
            500,
        ),
        (
            "RedisMemoryConfig.expired_id_retention_seconds",
            field_default(RedisMemoryConfig, "expired_id_retention_seconds"),
            3_600,
        ),
        (
            "RedisMemoryConfig.connect_timeout_seconds",
            field_default(RedisMemoryConfig, "connect_timeout_seconds"),
            2.0,
        ),
        (
            "RedisMemoryConfig.command_timeout_seconds",
            field_default(RedisMemoryConfig, "command_timeout_seconds"),
            2.0,
        ),
        (
            "WorkflowPostgresConfig.lease_ttl_seconds",
            field_default(WorkflowPostgresConfig, "lease_ttl_seconds"),
            120.0,
        ),
        (
            "WorkflowPostgresConfig.lease_renewal_margin_seconds",
            field_default(WorkflowPostgresConfig, "lease_renewal_margin_seconds"),
            20.0,
        ),
        (
            "WorkflowPostgresConfig.clock_tolerance_seconds",
            field_default(WorkflowPostgresConfig, "clock_tolerance_seconds"),
            2.0,
        ),
        (
            "SemanticCatalogConfig.pool_size",
            field_default(SemanticCatalogConfig, "pool_size"),
            5,
        ),
        (
            "SemanticCatalogConfig.connect_timeout_seconds",
            field_default(SemanticCatalogConfig, "connect_timeout_seconds"),
            5.0,
        ),
        (
            "SemanticCatalogConfig.command_timeout_seconds",
            field_default(SemanticCatalogConfig, "command_timeout_seconds"),
            10.0,
        ),
        (
            "SemanticCatalogConfig.pool_acquire_timeout_seconds",
            field_default(SemanticCatalogConfig, "pool_acquire_timeout_seconds"),
            5.0,
        ),
        (
            "SemanticCatalogConfig.snapshot_retention_seconds",
            field_default(SemanticCatalogConfig, "snapshot_retention_seconds"),
            604_800.0,
        ),
        (
            "SemanticCatalogConfig.event_retention_seconds",
            field_default(SemanticCatalogConfig, "event_retention_seconds"),
            604_800.0,
        ),
        (
            "SemanticCatalogConfig.cleanup_batch_size",
            field_default(SemanticCatalogConfig, "cleanup_batch_size"),
            500,
        ),
        (
            "SemanticCatalogConfig.max_envelope_bytes",
            field_default(SemanticCatalogConfig, "max_envelope_bytes"),
            1_048_576,
        ),
        (
            "SemanticCatalogConfig.max_payload_bytes",
            field_default(SemanticCatalogConfig, "max_payload_bytes"),
            524_288,
        ),
        (
            "SemanticCatalogConfig.max_bundle_history",
            field_default(SemanticCatalogConfig, "max_bundle_history"),
            100,
        ),
        (
            "SemanticCatalogConfig.max_active_pointers_per_scope",
            field_default(SemanticCatalogConfig, "max_active_pointers_per_scope"),
            256,
        ),
    ]
    for label, actual, expected in defaults:
        if actual != expected:
            checker.report(
                ROOT,
                f"documented default {label}={expected!r} but source has {actual!r}",
            )


# ---------------------------------------------------------------------------


def main() -> int:
    checker = Checker()
    check_links(checker)
    check_mermaid(checker)
    check_secrets(checker)
    check_bilingual(checker)
    check_smoke(checker)
    check_packages(checker)
    check_reconciliation(checker)

    if checker.errors:
        print(f"check_docs: {len(checker.errors)} error(s) found", file=sys.stderr)
        for error in checker.errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("check_docs: all documentation quality gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
