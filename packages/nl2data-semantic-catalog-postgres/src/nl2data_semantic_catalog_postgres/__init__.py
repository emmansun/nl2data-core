"""Optional durable PostgreSQL semantic catalog for nl2data-core.

This package implements the core :class:`SemanticSnapshotCatalog` boundary
with durable PostgreSQL storage for metadata snapshots, reviewed proposal
sets, immutable Semantic Model Bundle publications, active pointers, and
bounded lifecycle evidence.  It is optional: importing ``nl2data`` or
``nl2data_core`` never requires PostgreSQL or psycopg, and the psycopg
driver is loaded lazily only when a catalog is constructed from a DSN.

The catalog persists only bounded canonical envelopes - never credentials,
DSNs, raw prompts, raw queries/results, native driver objects, or
unrestricted source values - and revalidates fingerprints, tenant/source
scope, schema versions, and compatibility on every read and activation.
"""

from __future__ import annotations

__all__: list[str] = []
