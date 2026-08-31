"""Deterministic canonical serialization and fingerprint helpers.

Canonical form is stable across key insertion orders and is used for
configuration fingerprints and adapter artifact fingerprints.  Callers
must pass payloads that already exclude secrets.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from typing import Any


def canonical_value(value: Any) -> Any:
    """Normalize a value into a JSON-serializable, order-independent form."""
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", str(key))
            if normalized_key in normalized:
                raise ValueError("canonical object keys must be unique after NFC normalization")
            normalized[normalized_key] = canonical_value(item)
        return dict(sorted(normalized.items()))
    if isinstance(value, (set, frozenset)):
        return [
            canonical_value(item)
            for item in sorted(value, key=lambda item: canonical_json({"_": item}))
        ]
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (int, float, bool, type(None))):
        return value
    return str(value)


def canonical_json(payload: Any) -> str:
    """Render a payload as a canonical JSON string."""
    return json.dumps(
        canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_fingerprint(payload: Any) -> str:
    """Compute a ``sha256:<lowercase hex digest>`` fingerprint of canonical form."""
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
