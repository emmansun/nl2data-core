"""Bounded MongoDB metadata discovery with canonical dotted paths.

Discovery never samples raw values: it lists collections (bounded),
narrows to the configured allowlist, reads at most one document per
collection, and records only the canonical dotted paths of its keys.
The snapshot fingerprint is computed over the normalized path sets so
equal snapshots stay comparable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .client import MongoClientHandle
from .models import MongoMetadataSnapshot


def _collect_paths(document: Mapping[str, Any], *, max_fields: int) -> tuple[str, ...]:
    """Canonical dotted paths of a document's keys, bounded and value-free."""
    paths: list[str] = []

    def walk(value: Any, prefix: str) -> None:
        if len(paths) >= max_fields or not isinstance(value, Mapping):
            return
        for key, item in value.items():
            if len(paths) >= max_fields:
                return
            if key == "_id":
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.append(path)
            walk(item, path)

    walk(document, "")
    return tuple(paths)


def discover_metadata(
    handle: MongoClientHandle,
    *,
    allowed_collections: frozenset[str] | None = None,
    max_collections: int = 100,
    max_fields_per_collection: int = 200,
) -> MongoMetadataSnapshot:
    """Discover bounded canonical path metadata from the bound executor.

    ``allowed_collections`` narrows discovery to the configured allowlist;
    internal ``system.`` collections are never discovered.  The returned
    snapshot carries dotted paths only - raw values are never sampled or
    exposed.
    """
    collections: dict[str, tuple[str, ...]] = {}
    for name in handle.list_collections():
        if name.startswith("system."):
            continue
        if allowed_collections is not None and name not in allowed_collections:
            continue
        if len(collections) >= max_collections:
            break
        document = handle.sample_document(name)
        paths = (
            _collect_paths(document, max_fields=max_fields_per_collection)
            if document is not None
            else ()
        )
        collections[name] = paths
    return MongoMetadataSnapshot(collections=collections)
