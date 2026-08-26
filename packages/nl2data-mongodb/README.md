# nl2data-mongodb

An optional MongoDB backend integration for
[nl2data-core](https://github.com/emmansun/nl2data-core). It combines
metadata discovery and governed read-only MQL execution behind the core
provider-neutral contracts.

`pymongo` is loaded lazily at first use. The core import boundary never loads
the MongoDB driver.

## Install

```bash
pip install nl2data-mongodb
```

Requires Python 3.11+, `nl2data-core>=0.1.0`, and `pymongo>=4.6,<5`.

From a source checkout:

```bash
pip install -e packages/nl2data-mongodb
```

## Public surface

```python
from nl2data_mongodb import (
    MongoAdapterConfig,
    MongoMetadataDiscoverer,
    MongoQueryAdapter,
)
```

- `MongoAdapterConfig` — bounded discovery/execution configuration with
  host-injected URI references, collection/field allowlists, and result bounds.
- `MongoMetadataDiscoverer` — the core `MetadataDiscoverer` port
  implementation for MongoDB.
- `MongoQueryAdapter` — the core `QueryAdapter` port implementation
  for read-only structured MQL.

## Configuration

Configuration is credential-free; hosts inject the MongoDB URI through a resolver
or environment at runtime. `MongoAdapterConfig` carries only bounded,
fingerprintable settings.

```python
from nl2data_mongodb import MongoAdapterConfig

config = MongoAdapterConfig(
    uri_reference="env:NL2DATA_MONGODB_URI",
    database="app",
    allowed_collections={"users", "orders"},
    max_collections=100,
)
```

## Discovery

```python
from nl2data_mongodb import MongoAdapterConfig, MongoMetadataDiscoverer
from nl2data_core.metadata.protocol import MetadataDiscoveryConfig

discoverer = MongoMetadataDiscoverer(
    config=MongoAdapterConfig(
        uri_reference="env:NL2DATA_MONGODB_URI",
        database="app",
    ),
    allowed_collections={"users", "orders"},
)
snapshot = await discoverer.discover(MetadataDiscoveryConfig())
```

Discovery returns a core `MetadataSnapshot` with canonical dotted paths only;
raw document values are never sampled. Dynamic paths are marked `observed` and
`observed_incomplete` because bounded sampling cannot prove a complete schema.

## Query execution

```python
from nl2data_mongodb import MongoAdapterConfig, MongoQueryAdapter
from nl2data_core.adapters.models import ValidationContext

adapter = MongoQueryAdapter(
    config=MongoAdapterConfig(
        uri_reference="env:NL2DATA_MONGODB_URI",
        database="app",
    ),
    allowed_collections={"users"},
)
artifact = adapter.parse('{"spec_id":"a","operation":"find","collection":"users","projection":{"name":1},"limit":10}', ValidationContext())
validated = adapter.validate(artifact, ValidationContext())
result = await adapter.execute(validated, ValidationContext())
```

Execution uses a read-only client, enforces pipeline/stage/operator validation,
limits rows/columns/bytes, and maps every result value to the protected scalar
set.

## Failure classification

| Condition | Normalized result |
| --- | --- |
| Missing driver / unreachable service | `MongoUnavailableError` |
| Authorization or permission denial | `MetadataUnauthorizedError` |
| Lifecycle misuse (unparsed/unvalidated artifacts) | `MongoAdapterError` |
| Guard rejection (scope/obligation/limit) | `MongoAdapterError` |
| Statement/result bounds exceeded | `MongoExecutionError` |
| Malformed MQL / unsupported construct | `MongoAdapterError` |

URIs, raw BSON, native exceptions, and credentials never cross the boundary.

## Compatibility

The in-core `nl2data_core.adapters.mongodb` module remains available as a
temporary compatibility path. It delegates to this package when installed and
emits a deprecation warning; behavior is equivalent for existing hosts. New
code should import from `nl2data_mongodb` directly.

## More documentation

- [Documentation index](../../docs/README.md)
- [Adding an adapter or provider](../../docs/development/adding-adapter-or-provider.md)
- [Secrets and live testing](../../docs/operations/secrets.md)
- [Capabilities and support](../../docs/reference/capabilities.md)
