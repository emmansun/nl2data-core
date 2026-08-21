"""Deterministic canonical serialization and fingerprint helpers.

Canonical form is stable across key insertion orders and is used for
configuration fingerprints and adapter artifact fingerprints.  Callers
must pass payloads that already exclude secrets.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any


def canonical_value(value: Any) -> Any:
    """Normalize a value into a JSON-serializable, order-independent form."""
    if isinstance(value, Mapping):
        return {
            str(k): canonical_value(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (set, frozenset)):
        return [
            canonical_value(item)
            for item in sorted(value, key=lambda item: canonical_json({"_": item}))
        ]
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return str(value)


def canonical_json(payload: Any) -> str:
    """Render a payload as a canonical JSON string."""
    return json.dumps(
        canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sha256_fingerprint(payload: Any) -> str:
    """Compute a ``sha256:<lowercase hex digest>`` fingerprint of canonical form."""
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
