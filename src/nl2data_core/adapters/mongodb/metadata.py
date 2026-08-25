"""MongoDB metadata discovery normalized into the common snapshot contract.

Discovery never samples raw values: it lists collections (bounded),
narrows to the configured allowlist, reads at most one document per
collection, and records only the canonical dotted paths of its keys.
Dynamic MongoDB paths are marked ``observed`` with ``observed_incomplete``
so they are never treated as complete schema declarations.  The snapshot
fingerprint is computed over the normalized path sets so equal snapshots
stay comparable across backend mapping orders.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.metadata.models import (
    MetadataEvidence,
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataTrustLevel,
)
from nl2data_core.metadata.protocol import (
    MetadataDiscoveryCapability,
    MetadataDiscoveryConfig,
    MetadataDiscoveryError,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
)

from .client import MongoClientHandle
from .models import MongoUnavailableError


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
    source_id: str = "mongodb",
    allowed_collections: frozenset[str] | None = None,
    max_collections: int = 100,
    max_fields_per_collection: int = 200,
    allowed_fields: frozenset[str] | None = None,
) -> MetadataSnapshot:
    """Discover bounded canonical path metadata from the bound executor.

    ``allowed_collections`` narrows discovery to the configured allowlist;
    internal ``system.`` collections are never discovered.  The returned
    snapshot carries dotted paths only - raw values are never sampled or
    exposed - and every object is marked ``observed_incomplete`` because a
    single bounded document cannot prove a complete schema.
    """
    collections: dict[str, tuple[str, ...]] = {}
    bounded_objects = False
    bounded_fields = False
    bounded_samples = False
    for name in handle.list_collections():
        if name.startswith("system."):
            continue
        if allowed_collections is not None and name not in allowed_collections:
            continue
        if len(collections) >= max_collections:
            bounded_objects = True
            break
        document = handle.sample_document(name)
        if document is not None:
            # Sampling is inherently bounded: at most one document per
            # collection is ever observed, never the full document set.
            bounded_samples = True
        paths = (
            _collect_paths(document, max_fields=max_fields_per_collection)
            if document is not None
            else ()
        )
        if allowed_fields is not None:
            paths = tuple(path for path in paths if path in allowed_fields)
        if len(paths) == max_fields_per_collection and document is not None:
            bounded_fields = True
        collections[name] = paths

    evidence: list[MetadataEvidence] = []
    objects: list[MetadataObject] = []
    for name, paths in collections.items():
        reference = sha256_fingerprint({"collection": name, "paths": sorted(paths)})
        evidence.append(
            MetadataEvidence(
                evidence_id=f"mongo-obj-{name}",
                kind="path_observation",
                reference=reference,
                description="bounded dotted-path observation",
            )
        )
        objects.append(
            MetadataObject(
                object_id=name,
                kind=MetadataObjectKind.COLLECTION,
                name=name,
                fields=tuple(
                    MetadataField(
                        field_id=path,
                        object_id=name,
                        path=path,
                        data_type="document",
                        nullable=True,
                        trust_level=MetadataTrustLevel.OBSERVED,
                    )
                    for path in paths
                ),
                trust_level=MetadataTrustLevel.OBSERVED,
                observed_incomplete=True,
            )
        )

    source_digest = sha256_fingerprint(
        {
            "collections": sorted(collections),
            "references": sorted(item.reference for item in evidence),
        }
    )
    return MetadataSnapshot(
        snapshot_id=f"mongo-{source_digest[-16:]}",
        source=MetadataSourceReference(
            source_id=source_id,
            catalog_fingerprint=source_digest,
            description="bounded mongodb path observation",
        ),
        objects=tuple(objects),
        freshness=MetadataFreshness(
            bounded_objects=bounded_objects,
            bounded_fields=bounded_fields,
            bounded_samples=bounded_samples,
            # At most one document per collection is ever sampled.
            sample_limit=1,
        ),
        provenance=MetadataProvenance(
            discovered_by_fingerprint=sha256_fingerprint({"backend": "mongodb"}),
            method="mongo_path_discovery",
            evidence=tuple(evidence),
        ),
    )


class MongoMetadataDiscoverer:
    """Provider-neutral discovery over one bound MongoDB client handle.

    The discoverer adapts the existing bounded path discovery into the
    common ``MetadataSnapshot`` contract; the call-time
    :class:`MetadataDiscoveryConfig` narrows the collection/field allowlist
    and bounds the command timeout.  A closed or unavailable handle fails
    with a safe retryable :class:`MetadataUnavailableError`.
    """

    def __init__(
        self,
        handle: MongoClientHandle,
        *,
        source_id: str = "mongodb",
        allowed_collections: frozenset[str] | None = None,
        max_collections: int = 100,
        max_fields_per_collection: int = 200,
    ) -> None:
        self._handle = handle
        self._source_id = source_id
        self._allowed_collections = allowed_collections
        self._max_collections = max_collections
        self._max_fields_per_collection = max_fields_per_collection

    def capability(self) -> MetadataDiscoveryCapability:
        """Declare the discovery bounds this backend supports."""
        return MetadataDiscoveryCapability(
            backend="mongodb",
            supported=True,
            max_objects=self._max_collections,
            max_fields_per_object=self._max_fields_per_collection,
            supports_statistics=False,
            supports_sampling=True,
            description="bounded dotted-path discovery without raw values",
        )

    async def discover(self, config: MetadataDiscoveryConfig) -> MetadataSnapshot:
        """Discover a bounded canonical snapshot, failing closed on unavailability."""
        if not self._handle.available():
            raise MetadataUnavailableError(
                "the mongodb driver or service is unavailable for discovery",
                details={"cause_type": "Unavailable"},
            )
        effective_collections = (
            self._allowed_collections
            if self._allowed_collections is not None
            else None
        )
        if config.allowed_objects:
            if effective_collections is None:
                effective_collections = config.allowed_objects
            else:
                effective_collections &= config.allowed_objects
        if effective_collections is not None and not effective_collections:
            raise MetadataUnauthorizedError(
                "no collections are authorized for metadata discovery",
                details={"authorized_collections": "0"},
            )
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    discover_metadata,
                    self._handle,
                    source_id=self._source_id,
                    allowed_collections=effective_collections,
                    max_collections=min(config.max_objects, self._max_collections),
                    max_fields_per_collection=min(
                        config.max_fields_per_object, self._max_fields_per_collection
                    ),
                    allowed_fields=config.allowed_fields or None,
                ),
                timeout=config.timeout_seconds,
            )
        except (MongoUnavailableError, MetadataUnavailableError) as error:
            raise MetadataUnavailableError(
                "mongodb metadata discovery is unavailable",
                details={"cause_type": type(error).__name__},
            ) from error
        except TimeoutError as error:
            raise MetadataDiscoveryError(
                "mongodb metadata discovery exceeded the authorized timeout",
                details={"timeout_seconds": str(config.timeout_seconds)},
            ) from error
