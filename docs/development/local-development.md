# Local Development

> **Reader**: contributors to this repository. **Prerequisites**: Python
> 3.11+ (CI runs 3.11, 3.12, and 3.13), `git`. Internal `nl2data_core`
> imports are contributor-only — see
> [Adding an adapter or provider](adding-adapter-or-provider.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/nl2data/` | Public API: models, errors, facade, composition, engine. |
| `src/nl2data_core/` | Internal implementation: config, adapters, ai, workflow, memory, metadata, views, bundles, governance, tenancy, telemetry, plugins. |
| `packages/nl2data-openai/` | Optional sibling distribution: OpenAI structured-output provider. |
| `packages/nl2data-semantic-catalog-postgres/` | Optional sibling distribution: durable PostgreSQL semantic catalog. |
| `tests/` | `unit/` (deterministic), `contract/` (boundary contracts), `integration/` (composition), `security/` (threat cases), `conformance/` (reusable profiles), `evaluation/` (AI evaluation). |
| `openspec/` | OpenSpec change artifacts (design history, not end-user docs). |

## Set up a virtual environment

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate
```

## Install dependencies

```bash
pip install -e ".[dev]"              # core + test/lint/type tooling
pip install -e packages/nl2data-openai  # optional OpenAI provider (editable)
pip install -e packages/nl2data-semantic-catalog-postgres  # optional semantic catalog (editable)
```

`.[dev]` installs `pytest`, `pytest-asyncio`, `mypy`, `ruff`,
`types-PyYAML`, and `sqlglot`. Optional service drivers are **not**
installed by default; add extras when you work on those profiles:

```bash
pip install -e ".[dev,postgres]"
pip install -e "packages/nl2data-memory-redis[redis]"   # Redis memory profile
```

## Run tests

```bash
python -m pytest                 # full unit/contract/integration/security suite
python -m pytest tests/unit      # deterministic unit tests only
python -m pytest -rs             # show every skip reason (real-service profiles)
```

- Deterministic suites never require a service, driver, or credential.
- Real-service profiles (`tests/integration/test_*_real.py`,
  `tests/conformance/test_postgres_conformance.py`,
  `tests/conformance/test_mongodb_conformance.py`) **skip** — never pass —
  when the driver or service is unavailable.

## Lint and type checking

```bash
python -m ruff check src tests packages/nl2data-openai/src packages/nl2data-semantic-catalog-postgres/src packages/nl2data-admin-service/src packages/nl2data-memory-redis/src packages/nl2data-mongodb/src packages/nl2data-postgres/src packages/nl2data-workflow-postgres/src
python -m mypy src packages/nl2data-openai/src packages/nl2data-semantic-catalog-postgres/src packages/nl2data-admin-service/src packages/nl2data-memory-redis/src packages/nl2data-mongodb/src packages/nl2data-postgres/src packages/nl2data-workflow-postgres/src
```

Ruff and mypy configuration live in `pyproject.toml` (line length 100,
Python 3.11 target; mypy enforces typed definitions and no implicit
optionals). Keep both clean before pushing.

## Architecture checks

The semantic control-plane dependency graph, canonical contract owners,
compatibility re-exports, exact-duplicate detection, and hotspot line
budgets are pinned in
[`docs/architecture/semantic-control-plane-manifest.yaml`](../architecture/semantic-control-plane-manifest.yaml)
and enforced by:

```bash
python -m pytest tests/contract/test_semantic_control_plane_architecture.py
```

See [Package boundaries](../architecture/package-boundaries.md#semantic-control-plane-layers)
for the approved layer DAG. Common failures and their fixes:

| Failure | Fix |
| --- | --- |
| `test_manifest_layer_imports_are_approved` reports `A -> B` | Module in layer `A`'s set imports a module in a layer not listed in `A`'s `may_import`. Either remove the import or, if the edge is a genuine inward dependency, add the layer to `may_import` in the manifest with a justifying comment. |
| `test_manifest_prohibited_imports_are_absent` reports a rule violation | An import matched a `prohibited_imports` rule (e.g. the publication path touching mutable `AssemblyDraft`). Remove the import; do not weaken the rule without an ADR. |
| `test_manifest_physical_line_budgets_are_met` fails | A hotspot exceeded its budget. Split the module along its existing seams (repositories, capability services, gates) rather than raising the budget. Budget exceptions require a manifest revision with justification. |
| `test_manifest_exact_duplicate_modules_are_absent` fails | Two scanned files are byte-identical after newline normalization. Keep one canonical owner and make the other a re-export listed in `compatibility_reexports` — or delete the copy. |
| `test_manifest_import_graph_has_no_cycles` fails | A dependency cycle exists between scanned modules. Break it at the weakest edge, usually by moving a shared value type into a lower layer. |
| `test_canonical_owner_symbols_resolve` / `test_manifest_port_declarations_resolve_to_methods` fail | A symbol or method declared in the manifest no longer exists. Restore it or update the manifest in the same change. |

## Build packages

```bash
python -m build --wheel --outdir dist/core .
python -m build --wheel --outdir dist/openai packages/nl2data-openai
python -m build --wheel --outdir dist/postgres-catalog packages/nl2data-semantic-catalog-postgres
```

CI verifies wheel names (`nl2data_core`, `nl2data_openai`,
`nl2data_semantic_catalog_postgres`) and metadata.

## Service prerequisites for optional profiles

| Service | Env var(s) | Needed for |
| --- | --- | --- |
| PostgreSQL | `NL2DATA_POSTGRES_DSN` (default `postgresql://localhost:5432/nl2data_test`) | Shared workflow state, durable semantic catalog, PostgreSQL conformance and discovery |
| Redis | `NL2DATA_REDIS_URL` | Redis Memory provider |
| MongoDB | `NL2DATA_MONGO_URI`, `NL2DATA_MONGO_DATABASE` | MongoDB adapter conformance and discovery |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` | Live provider evaluation (never in CI by default) |

The CI integration workflow starts PostgreSQL 16, Redis 7, and MongoDB 7
with health checks (`pg_isready`, `redis-cli ping`, `mongosh ping`). Locally,
run the same services any way you prefer; missing services produce explicit
skips with `-rs`, never false passes.

## Documentation checks

Documentation quality gates run with:

```bash
python scripts/check_docs.py
```

This validates repository links, Mermaid block structure, secret-pattern
scans, bilingual navigation, and the documented import/quickstart smoke
examples. See [Documentation quality gates](../reference/production-readiness.md#documentation-quality-gates).

## Commit conventions

- One logical change per commit; OpenSpec changes track task checkboxes
  in `openspec/changes/<change>/tasks.md`.
- Update documentation in the same change as the behavior it describes —
  the docs are part of the quality gate (see
  [Reference documentation matches implementation](../reference/production-readiness.md)).
- Never commit credentials, DSNs, raw prompts, raw query results, or
  provider responses — even in tests or examples.
