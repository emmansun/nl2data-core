# nl2data-semantic-catalog-postgres

Optional durable PostgreSQL semantic catalog for the
[`nl2data-core`](https://github.com/nl2data/nl2data-core) metadata-to-Bundle
lifecycle.

The catalog persists safe, versioned representations of metadata snapshots,
reviewed proposal sets, immutable Semantic Model Bundle publications, active
pointers, and bounded lifecycle evidence in PostgreSQL, and coordinates
publish/activate/rollback atomically across processes and workers.

## Installation

```bash
pip install nl2data-semantic-catalog-postgres
```

The package is optional. Installing or importing `nl2data` / `nl2data-core`
never requires PostgreSQL; the `psycopg` driver is loaded lazily only when a
catalog is constructed from a DSN.

## Usage

```python
from nl2data_core.metadata.catalog import SemanticSnapshotCatalog
from nl2data_semantic_catalog_postgres import PostgreSQLSemanticCatalog
from nl2data_semantic_catalog_postgres.config import SemanticCatalogConfig

catalog: SemanticSnapshotCatalog = PostgreSQLSemanticCatalog(
    dsn=os.environ["NL2DATA_POSTGRES_DSN"],  # host-managed secret injection
    config=SemanticCatalogConfig(namespace="my_catalog"),
)
```

- Register snapshots, activate them per source/tenant scope, and reload the
  active content after restart with revalidation.
- Save reviewed proposal sets bound to their source snapshot fingerprint.
- Publish, look up, activate, and roll back immutable Semantic Model Bundles
  with core validation, dependency, freshness, drift, and scope checks.

## Safety

- Only bounded canonical envelopes are persisted: never credentials, DSNs,
  raw prompts, raw queries/results, native objects, or unrestricted source
  values.
- Every read revalidates schema version, artifact kind, fingerprint, and
  tenant/source scope; newer envelope or migration versions fail closed.
- Errors are normalized and never leak DSNs or backend exception text.
- Catalog tables are separate from workflow state tables; the two backends
  never share records.

See `docs/operations/services.md` in `nl2data-core` for service profiles and
migration guidance.
