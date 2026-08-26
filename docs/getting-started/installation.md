# Installation

> **Reader**: application developers. **Prerequisites**: Python 3.11+ and
> `pip`. All commands in this guide are deterministic and require no
> credentials or network service.

## Install the core library

```bash
pip install nl2data-core
```

The base package depends only on `pydantic>=2.0,<3` and `PyYAML>=6.0`.
Importing it never loads database drivers, model-provider SDKs, HTTP
frameworks, or telemetry backends — optional backends stay unloaded until
you compose them explicitly.

## Optional extras

| Extra | Provides | Dependency |
| --- | --- | --- |
| `sql` | SQL query adapter (SQLite fixtures use the standard library; `sqlglot` powers bounded SQL compilation) | `sqlglot>=25.0,<30` |
| `postgres` | In-core PostgreSQL discovery/conformance profiles | `psycopg[binary,pool]>=3.1,<4` |
| `nl2data-workflow-postgres` | Shared workflow state backend (`PostgreSQLStateStore`) as a separate package | `psycopg[binary,pool]>=3.1,<4` |

```bash
# For example: SQL adapter
pip install "nl2data-core[sql]"
```

Extras are lazy: installing them does not import anything at package
import time. Drivers are loaded only when a real service client is
first built.

## Install the optional Redis memory backend

The Redis memory backend is a separate distribution implementing the core
`MemoryProvider` contract using Redis:

```bash
pip install nl2data-memory-redis
```

It depends on `nl2data-core>=0.1.0` and `redis>=5.0,<7`. The driver is loaded
lazily at first use; importing the package does not import `redis`.

## Install the optional PostgreSQL workflow state backend

The PostgreSQL workflow state backend is a separate distribution implementing
the core workflow state-store contracts using PostgreSQL:

```bash
pip install nl2data-workflow-postgres
```

It depends on `nl2data-core>=0.1.0` and
`psycopg[binary,pool]>=3.1,<4`. The driver is loaded lazily at first use;
importing the package does not import `psycopg`.

## Install the optional OpenAI provider

The OpenAI provider is a separate distribution implementing the
provider-neutral `ModelProvider` contract:

```bash
pip install nl2data-openai
```

It depends on `nl2data-core>=0.1.0` and `openai>=1.40,<3`. The OpenAI SDK
is never imported at package import time; the client is built lazily from
injected credentials on first use.

## Install the optional PostgreSQL adapter

The PostgreSQL adapter is a separate distribution implementing the
provider-neutral metadata discovery and `QueryAdapter` contracts for
PostgreSQL:

```bash
pip install nl2data-postgres
```

It depends on `nl2data-core>=0.1.0` and
`psycopg[binary,pool]>=3.1,<4`. The driver is loaded lazily at first
use; importing the package does not import `psycopg`.

## Install the optional MongoDB adapter

The MongoDB adapter is a separate distribution implementing the
provider-neutral metadata discovery and `QueryAdapter` contracts for
MongoDB:

```bash
pip install nl2data-mongodb
```

It depends on `nl2data-core>=0.1.0` and `pymongo>=4.6,<5`. The driver
is loaded lazily at first use; importing the package does not import
`pymongo`.

## Install the optional PostgreSQL semantic catalog

The durable semantic catalog is a separate distribution implementing the
`SemanticSnapshotCatalog` boundary with PostgreSQL storage for snapshots,
proposal sets, Bundle publications, and active pointers:

```bash
pip install nl2data-semantic-catalog-postgres
```

It depends on `nl2data-core>=0.1.0` and `psycopg[binary,pool]>=3.1,<4`.
The driver is never imported at package import time; it is loaded lazily
only when a catalog is constructed from a DSN.

From a source checkout, all optional packages install with:

```bash
pip install -e ".[dev]"
pip install -e packages/nl2data-openai
pip install -e packages/nl2data-semantic-catalog-postgres
pip install -e packages/nl2data-postgres
pip install -e packages/nl2data-mongodb
pip install -e packages/nl2data-memory-redis
```

## Install for development

See [Local development](../development/local-development.md) for the
full contributor setup (virtual environment, test tooling, lint, type
checking, and package builds).

## Verify the installation

```python
import nl2data

print(nl2data.__all__)  # public API surface
```

If this prints the public symbol list, the core is installed. Continue
with the [Quickstart](quickstart.md).

## Next steps

- [Quickstart](quickstart.md) — compose a facade and submit a first query.
- [Composition and query lifecycle](../guides/composition-and-query-lifecycle.md)
  — protected outcomes, clarification, cancellation, and health operations.
- [Installation (简体中文)](installation.zh-CN.md) — 中文安装指南。
