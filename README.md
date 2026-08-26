# nl2data-core

A governed and extensible Python framework for natural-language access to
heterogeneous enterprise data.

One public facade (`import nl2data`) composes Semantic IR, Semantic
View/Bundle resolution, a deterministic governed workflow runtime, and
optional database, memory, and model-provider backends — so that
**validation, governance, authorization, and result protection always
run before any external work**. The base package imports only `pydantic`
and `PyYAML`; no database driver, LLM SDK, HTTP framework, or telemetry
backend is ever loaded unless you compose it explicitly.

## Packaging

| Aspect | Value |
| --- | --- |
| Distribution | `nl2data-core` (Python 3.11+) |
| Public API | `import nl2data` |
| Internal API | `nl2data_core` — contributor-only, applications must not import it |
| Optional extras | `sql`, `postgres`, `redis` |
| Optional sibling | `nl2data-openai` (OpenAI structured-output provider), `nl2data-mongodb` (MongoDB adapter), `nl2data-workflow-postgres` (PostgreSQL workflow state backend) |

## Install

```bash
pip install nl2data-core
```

For development (editable install with test/type/lint tooling):

```bash
pip install -e ".[dev]"
```

## Minimal usage

```python
import asyncio

from nl2data import CompositionProfile, create_facade, OutcomeStatus, QueryRequest


async def main() -> None:
    facade = create_facade(composition=CompositionProfile())
    await facade.initialize()

    outcome = await facade.aquery(
        QueryRequest(request_id="req-1", prompt="How many orders shipped yesterday?")
    )

    # Without a configured runtime the facade returns an explicit,
    # protected not-configured outcome instead of fabricating a result.
    assert outcome.status == OutcomeStatus.NOT_CONFIGURED

    await facade.close()


asyncio.run(main())
```

Every query returns a protected `QueryOutcome`; internal details never
cross the public boundary. Bind a pre-built `WorkflowRuntimePort` or the
deterministic composition parts (adapter, policy scope, view, plan
resolver, provider, state store, tenant context — all optional) to
execute real work. See the [quickstart](docs/getting-started/quickstart.md).

## Capability and support status

| Capability | Status | Prerequisites |
| --- | --- | --- |
| Public facade, lifecycle, protected outcomes | Implemented + conformant | None |
| Semantic IR, View/Bundle resolution | Implemented + conformant | None |
| Governed workflow runtime (deterministic) | Implemented + conformant | None |
| SQL adapter (SQLite fixtures) | Implemented + conformant | `sql` extra (`sqlglot`) |
| SQL adapter (PostgreSQL) | Implemented; service-verified in CI | `nl2data-postgres` package + service |
| MongoDB adapter | Implemented; service-verified in CI | `nl2data-mongodb` package + service |
| Durable workflow state (SQLite) | Implemented + conformant | None |
| Shared workflow state (PostgreSQL) | Implemented; service-verified in CI | `nl2data-workflow-postgres` package + service |
| Memory (in-memory) | Implemented + conformant | None |
| Memory (Redis) | Implemented; service-verified in CI | `redis` extra + service |
| Metadata discovery (PostgreSQL/MongoDB) | Implemented; service-verified in CI | `nl2data-postgres`/`nl2data-mongodb` package + service |
| AI intent resolution + evaluation | Implemented + conformant | `nl2data-openai` for live provider |
| OpenAI structured-output provider | Implemented; live-verified on demand | `nl2data-openai` + credentials |

Status vocabulary: **Implemented** (exists in source), **Conformant**
(passes the deterministic conformance suite), **Verified** (passed a
real-service/live-provider run). Nothing in this repository claims
general **production support** for unverified adapters, transports, or
deployment topologies — see [Production readiness](docs/reference/production-readiness.md).

## Limitations

- **At-least-once execution**: interrupted workflows may re-run stages;
  this core never claims exactly-once external execution.
- **No HTTP hosting**: there is no `nl2data_http` package yet; hosting
  behind HTTP is out of scope (a future host programs against the
  transport-neutral `FacadePort`).
- **No streaming, agent loops, or autonomous repair** beyond the bounded
  extension points; approval-required is an internal runtime event only.
- **Process-local metadata ledger**: cross-process metadata lifecycle
  coordination is a host responsibility.
- **Real-service verification is environment-dependent**: without a
  service or driver, real-service profiles skip explicitly — never a
  false pass.

## Documentation

- [Documentation index](docs/README.md) — reader-oriented guides for
  users, integrators, architects, operators, and contributors.
- [Installation](docs/getting-started/installation.md) ·
  [Quickstart](docs/getting-started/quickstart.md)
- [Architecture overview](docs/architecture/overview.md) ·
  [Execution flow](docs/architecture/execution-flow.md) ·
  [Evidence and fingerprints](docs/architecture/evidence-and-fingerprints.md)
- [Service configuration](docs/operations/services.md) ·
  [Secrets and live testing](docs/operations/secrets.md) ·
  [Troubleshooting](docs/operations/troubleshooting.md)
- [Configuration](docs/reference/configuration.md) ·
  [Error codes](docs/reference/error-codes.md) ·
  [Compatibility](docs/reference/compatibility.md)
- 中文文档：访问[文档索引](docs/README.zh-CN.md)（English is the
  normative source; Chinese pages are staged translations）。

## Development

```bash
python -m pytest                      # full unit/contract/integration/security suite
python -m mypy src packages/nl2data-openai/src  # static type checking
python -m ruff check src tests packages/nl2data-openai/src  # lint
python scripts/check_docs.py          # documentation quality gates
```

See [Local development](docs/development/local-development.md).
