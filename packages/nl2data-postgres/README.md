# nl2data-postgres

An optional PostgreSQL backend integration for
[nl2data-core](https://github.com/emmansun/nl2data-core). It combines
metadata discovery and governed read-only SQL execution behind the core
provider-neutral contracts.

`psycopg` is an optional dependency and is loaded lazily at first use. The
core import boundary never loads the PostgreSQL driver.

## Install

```bash
pip install nl2data-postgres
```

Requires Python 3.11+, `nl2data-core>=0.1.0`, and
`psycopg[binary,pool]>=3.1,<4`.

From a source checkout:

```bash
pip install -e packages/nl2data-postgres
```

## Public surface

```python
from nl2data_postgres import (
    PostgresAdapterConfig,
    PostgresMetadataDiscoverer,
    PostgresQueryAdapter,
)
```

- `PostgresAdapterConfig` — bounded discovery/execution configuration with
  host-injected DSN references, object/field allowlists, and result bounds.
- `PostgresMetadataDiscoverer` — the core `MetadataDiscoverer` port
  implementation for PostgreSQL.
- `PostgresQueryAdapter` — the core `QueryAdapter` port implementation
  for read-only PostgreSQL SQL.

## Configuration

Configuration is credential-free; hosts inject DSNs through a resolver
or environment at runtime. `PostgresAdapterConfig` carries only
bounded, fingerprintable settings.

```python
from nl2data_postgres import PostgresAdapterConfig

config = PostgresAdapterConfig(
    dsn_reference="env:NL2DATA_POSTGRES_DSN",
    allowed_objects={"users", "orders"},
    max_objects=256,
    max_rows=10_000,
)
```

## Discovery

```python
from nl2data_postgres import PostgresAdapterConfig, PostgresMetadataDiscoverer
from nl2data_core.metadata.protocol import MetadataDiscoveryConfig

discoverer = PostgresMetadataDiscoverer(
    config=PostgresAdapterConfig(dsn_reference="env:NL2DATA_POSTGRES_DSN"),
    allowed_objects={"users", "orders"},
)
snapshot = await discoverer.discover(MetadataDiscoveryConfig())
```

Discovery returns a core `MetadataSnapshot` with normalized tables,
columns, types, primary/foreign keys, and bounded protected statistics.

## Query execution

```python
from nl2data_postgres import PostgresAdapterConfig, PostgresQueryAdapter
from nl2data_core.adapters.models import ValidationContext

adapter = PostgresQueryAdapter(
    config=PostgresAdapterConfig(dsn_reference="env:NL2DATA_POSTGRES_DSN"),
    allowed_objects={"users"},
)
artifact = adapter.parse("SELECT id, name FROM users LIMIT 10", ValidationContext())
validated = adapter.validate(artifact, ValidationContext())
result = await adapter.execute(validated, ValidationContext())
```

Execution uses a read-only connection pool, enforces statement timeout,
limits rows/columns/bytes, and maps every result value to the protected
scalar set.

## Failure classification

| Condition | Normalized result |
| --- | --- |
| Missing driver / unreachable service | Retryable `MetadataUnavailableError` |
| Authorization or permission denial | `MetadataUnauthorizedError` |
| Lifecycle misuse (unparsed/unvalidated artifacts) | `PostgresAdapterError` |
| Guard rejection (scope/obligation/limit) | `SQLGuardError` |
| Statement/result bounds exceeded | `PostgresExecutionError` |
| Malformed SQL | `SQLParseError` |

DSNs, raw SQL, native exceptions, and credentials never cross the
boundary.

## Compatibility

The in-core `nl2data_core.adapters.sql.SqlMetadataDiscoverer` still
accepts `dialect="postgresql"` as a temporary compatibility path, but it
always uses the legacy in-core implementation and emits a deprecation
warning; it does not delegate to this package. New code should import
from `nl2data_postgres` directly.

## More documentation

- [Documentation index](../../docs/README.md)
- [Adding an adapter or provider](../../docs/development/adding-adapter-or-provider.md)
- [Secrets and live testing](../../docs/operations/secrets.md)
- [Capabilities and support](../../docs/reference/capabilities.md)
