"""Versioned serialization envelope for shared memory records.

Only validated :class:`MemoryRecord` safe representations cross the Redis
boundary: the envelope carries an explicit schema version and the record's
``safe_dump()`` (bounded identifiers, protected fingerprints, typed
payloads - never raw prompts, queries, rows, or native objects).
Deserialization revalidates the complete Pydantic model and rejects
malformed, unknown-version, raw-payload, or scope-invalid values with a
normalized error instead of ever returning them as recalled memory.

An explicit ``expire`` also writes a tombstone envelope on the record key;
tombstones are recognized by deserialization and skipped by recall, and
they reserve the record id for the configured retention window.
"""

from __future__ import annotations

import json
from typing import Any

from nl2data_core.memory.errors import MemoryErrorCode, MemoryInvocationError
from nl2data_core.memory.models import MemoryRecord
from pydantic import ValidationError

#: The only supported serialization schema version.
SERIALIZATION_SCHEMA_VERSION = 1

#: Stable envelope field names (bounded; unknown fields are rejected).
_SCHEMA_VERSION_KEY = "schema_version"
_RECORD_KEY = "record"
_TOMBSTONE_KEY = "tombstone"


def serialize_record(record: MemoryRecord) -> str:
    """Serialize one validated record into the deterministic envelope."""
    return json.dumps(
        {
            _SCHEMA_VERSION_KEY: SERIALIZATION_SCHEMA_VERSION,
            _RECORD_KEY: record.safe_dump(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def serialize_tombstone() -> str:
    """Serialize the expired-record tombstone envelope."""
    return json.dumps(
        {_SCHEMA_VERSION_KEY: SERIALIZATION_SCHEMA_VERSION, _TOMBSTONE_KEY: True},
        sort_keys=True,
        separators=(",", ":"),
    )


def deserialize_record_value(raw: str) -> MemoryRecord | None:
    """Validate one stored envelope into a record, or ``None`` for tombstones.

    Raises a normalized ``RECORD_REJECTED`` error for malformed JSON,
    unknown schema versions, and values that fail full :class:`MemoryRecord`
    validation; the stored value itself is never exposed in the error.
    """
    try:
        envelope = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise _invalid_data("JSONDecodeError") from error
    if not isinstance(envelope, dict) or _SCHEMA_VERSION_KEY not in envelope:
        raise _invalid_data("envelope_shape")
    if envelope[_SCHEMA_VERSION_KEY] != SERIALIZATION_SCHEMA_VERSION:
        raise MemoryInvocationError(
            MemoryErrorCode.RECORD_REJECTED,
            "memory provider returned an unsupported schema version",
            details={"schema_version": str(envelope[_SCHEMA_VERSION_KEY])},
        )
    if envelope.get(_TOMBSTONE_KEY) is True:
        if set(envelope) != {_SCHEMA_VERSION_KEY, _TOMBSTONE_KEY}:
            raise _invalid_data("envelope_fields")
        return None
    if set(envelope) != {_SCHEMA_VERSION_KEY, _RECORD_KEY}:
        raise _invalid_data("envelope_fields")
    record_value: Any = envelope.get(_RECORD_KEY)
    if not isinstance(record_value, dict):
        raise _invalid_data("record_shape")
    try:
        # ``kind`` is derived from the payload discriminator: ``safe_dump``
        # emits it for readability but it is not a model field, so it must
        # not be fed back into validation.
        record_value = {k: v for k, v in record_value.items() if k != "kind"}
        return MemoryRecord.model_validate(record_value)
    except ValidationError as error:
        raise _invalid_data("ValidationError") from error


def _invalid_data(cause_type: str) -> MemoryInvocationError:
    """The normalized data error for an incompatible stored value."""
    return MemoryInvocationError(
        MemoryErrorCode.RECORD_REJECTED,
        "memory provider returned invalid data",
        details={"cause_type": cause_type},
    )
