# nl2data-core

An embeddable Python framework for adding governed natural-language data
access to an application. The host keeps control of identity, tenancy,
deployment, and credentials; NL2Data supplies semantic query planning and
a bounded execution path behind one public facade (`import nl2data`).

The runtime resolves requests against an authorized Semantic View, builds a
typed Semantic IR, and compiles only operations supported by the configured
adapter. Governance, authorization, and the artifact guard gate database
execution; results are normalized and protected before they cross the
facade. The base package imports only `pydantic` and `PyYAML`; database
drivers, LLM SDKs, and service backends load only when explicitly composed.

## Product fit

NL2Data is designed for Python applications that need natural-language
queries without handing the model unrestricted database access. Teams define
business entities, fields, relationships, and policies; the host supplies
trusted context and backends; the runtime validates each query against those
boundaries and returns a protected outcome with safe evidence.

- **Semantic onboarding**: author a semantic assembly or convert the
  supported WrenAI MDL JSON subset offline with [`nl2data-mdl`](docs/guides/semantic-assembly-authoring.md#wren-mdl-importer).
- **Governed SQL analytics**: filters, common aggregations, grouping,
  ordering, bounded results, authorized multi-entity inner joins, and a
  closed set of computed expressions.
- **Composable runtimes**: use SQL (SQLite/PostgreSQL), MongoDB, and optional
  model, memory, and workflow-state packages through explicit composition.

This is a library, not a hosted query service: there is no HTTP host, and the
query language is intentionally bounded rather than arbitrary SQL. See the
[capability matrix](docs/reference/capabilities.md) for backend differences
and verification status.

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

The core facade and MDL importer need only the base package. To compile SQL
queries locally, install the SQL extra:

```bash
pip install "nl2data-core[sql]"
```

For development (editable install with test/type/lint tooling):

```bash
pip install -e ".[dev]"
```

## Facade behavior without a runtime

An empty profile is intentionally non-executable. This small example shows
the safe fallback contract; it does not run a data query. For an executable
governed flow, start with the [mainflow demo](docs/guides/mainflow-demo.md)
or bind a configured runtime as described in the
[CompositionProfile reference](docs/reference/composition-profile.md).

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
cross the public boundary. A deterministic runtime requires an adapter,
policy scope, authorized view, and plan resolver. A model provider, state
store, tenant context, and other services are composed as required by the
host. See the [quickstart](docs/getting-started/quickstart.md).

## Capability and support status

| Capability | Status | Prerequisites |
| --- | --- | --- |
| Public facade, lifecycle, protected outcomes | Implemented + conformant | None |
| Semantic IR, View/Bundle resolution | Implemented + conformant | None |
| Governed workflow runtime (deterministic) | Implemented + conformant | None |
| SQL adapter and bounded query compiler (SQLite fixtures) | Implemented + conformant | `sql` extra (`sqlglot`) |
| SQL multi-entity inner joins and computed expressions | Implemented + conformant | SQL adapter; authorized relationship path |
| SQL adapter (PostgreSQL) | Implemented; service-verified in CI | `nl2data-postgres` package + service |
| MongoDB adapter (single-collection queries and aggregation) | Implemented; service-verified in CI | `nl2data-mongodb` package + service |
| Durable workflow state (SQLite) | Implemented + conformant | None |
| Shared workflow state (PostgreSQL) | Implemented; service-verified in CI | `nl2data-workflow-postgres` package + service |
| Memory (in-memory) | Implemented + conformant | None |
| Memory (Redis) | Implemented; service-verified in CI | `redis` extra + service |
| Metadata discovery (PostgreSQL/MongoDB) | Implemented; service-verified in CI | `nl2data-postgres`/`nl2data-mongodb` package + service |
| AI intent resolution + evaluation | Implemented + conformant | `nl2data-openai` for live provider |
| Value-level semantics (enum value mapping) | Implemented + conformant | None |
| WrenAI MDL to semantic-assembly importer | Implemented + conformant | `nl2data-mdl` CLI; supported MDL JSON subset |
| OpenAI structured-output provider | Implemented; live-verified on demand | `nl2data-openai` + credentials |

Status vocabulary: **Implemented** (exists in source), **Conformant**
(passes the deterministic conformance suite), **Verified** (passed a
real-service/live-provider run). Nothing in this repository claims
general **production support** for unverified adapters, transports, or
deployment topologies — see [Production readiness](docs/reference/production-readiness.md).

**Value-level semantics (v4.1)**: enum-coded fields may declare a
governed `value_mapping`. Filter values resolve by deterministic lookup
at the intent-resolution stage — **before the IR freezes** — never by
model invention (invariant N4: *no probabilistic construction;
deterministic governed lookup permitted*). Unknown values fail at the
resolution stage (`VS_001`), not at the compiler. See the
[Semantic layer](docs/architecture/semantic-layer.md).

## Limitations

- **At-least-once execution**: interrupted workflows may re-run stages;
  this core never claims exactly-once external execution.
- **No HTTP hosting**: there is no `nl2data_http` package yet; hosting
  behind HTTP is out of scope (a future host programs against the
  transport-neutral `FacadePort`).
- **Bounded query language**: SQL supports authorized inner joins and a
  closed computed-expression subset; outer joins, arbitrary SQL/CTEs, and
  general-purpose window expressions are not exposed. MongoDB does not
  support cross-collection joins or SQL computed expressions.
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
- [Capabilities and support](docs/reference/capabilities.md) ·
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
