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
pip install -e ".[dev,postgres,redis,mongodb]"
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
python -m ruff check src tests packages/nl2data-openai/src packages/nl2data-semantic-catalog-postgres/src
python -m mypy src packages/nl2data-openai/src packages/nl2data-semantic-catalog-postgres/src
```

Ruff and mypy configuration live in `pyproject.toml` (line length 100,
Python 3.11 target; mypy enforces typed definitions and no implicit
optionals). Keep both clean before pushing.

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
