"""Deterministic normalization and fingerprinting for structured MQL.

Specs are strict JSON-compatible structures: normalization sorts mapping
keys, recurses into nested values, and rejects anything that is not a
plain JSON scalar or container.  Shell text and JavaScript never appear
in these structures, so fingerprints stay deterministic and safe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from nl2data_core.canonical import sha256_fingerprint

#: JSON-compatible scalar types accepted inside structured MQL values.
_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


def normalize_mql_value(value: Any) -> Any:
    """Canonicalize a JSON-compatible value (sorted keys, recursed).

    Raises :class:`TypeError` for values that are not JSON-compatible so
    specs can never smuggle native objects or shell text into a fingerprint.
    """
    if isinstance(value, Mapping):
        return {
            str(key): normalize_mql_value(item)
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, (list, tuple)):
        return [normalize_mql_value(item) for item in value]
    if isinstance(value, _SCALAR_TYPES):
        return value
    raise TypeError(f"value of type '{type(value).__name__}' is not JSON-compatible")


def assert_json_compatible(value: Any, *, path: str = "") -> None:
    """Raise :class:`TypeError` unless ``value`` is a strict JSON structure.

    Mapping keys must be strings and nested values must be JSON scalars,
    lists, or mappings; this is the typed-model defense against shell text,
    JavaScript, and native BSON objects entering a specification.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"non-string key at '{path}'")
            assert_json_compatible(item, path=f"{path}.{key}" if path else str(key))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_json_compatible(item, path=f"{path}[{index}]")
        return
    if not isinstance(value, _SCALAR_TYPES):
        raise TypeError(
            f"value of type '{type(value).__name__}' at '{path}' is not JSON-compatible"
        )


def predicate_fingerprint(field_id: str, operator: str, value: Any) -> str:
    """Stable fingerprint of one leaf predicate; matches governance obligations."""
    return sha256_fingerprint(
        {"field_id": field_id, "operator": operator, "value": normalize_mql_value(value)}
    )


def mql_metadata_fingerprint(
    collections: Mapping[str, Sequence[str]],
) -> str:
    """Fingerprint of a bounded collection/field metadata snapshot."""
    return sha256_fingerprint(
        {
            collection: sorted(fields)
            for collection, fields in sorted(collections.items(), key=lambda item: item[0])
        }
    )


def mql_spec_payload(spec: Any) -> dict[str, Any]:
    """The canonical fingerprint payload of one structured MQL spec.

    The payload excludes raw credentials by construction (specs carry no
    credentials) and keeps only canonical normalized values, so equal specs
    in different key orders produce identical payloads.
    """
    obligation = spec.tenant_obligation
    routing = spec.routing_evidence
    return {
        "spec_id": spec.spec_id,
        "operation": spec.operation.value,
        "collection": spec.collection,
        "filter": normalize_mql_value(spec.filter),
        "projection": normalize_mql_value(spec.projection),
        "sort": normalize_mql_value(spec.sort),
        "skip": spec.skip,
        "limit": spec.limit,
        "pipeline": (
            normalize_mql_value(spec.pipeline) if spec.pipeline is not None else None
        ),
        "tenant_obligation": (
            {
                "field_id": obligation.field_id,
                "operator": obligation.operator,
                "value": normalize_mql_value(obligation.value),
                "fingerprint": obligation.fingerprint,
            }
            if obligation is not None
            else None
        ),
        "routing_evidence": (
            {
                "kind": routing.kind.value,
                "reference": routing.reference,
            }
            if routing is not None
            else None
        ),
    }


def mql_spec_fingerprint(spec: Any) -> str:
    """Stable sha256 fingerprint of a structured MQL spec."""
    return sha256_fingerprint(mql_spec_payload(spec))
