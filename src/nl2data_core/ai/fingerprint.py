"""Stable safe fingerprints for AI runtime artifacts.

Fingerprints are computed over canonical payloads with secret-bearing keys
stripped, so equivalent artifacts in different mapping insertion orders
produce identical fingerprints and provider credentials never enter
evidence or reports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nl2data_core.adapters.fingerprint import safe_artifact_payload
from nl2data_core.canonical import sha256_fingerprint


def safe_ai_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical-safe copy with credentials and raw payload markers removed."""
    return safe_artifact_payload(payload)


def ai_fingerprint(payload: Mapping[str, Any]) -> str:
    """Compute a stable fingerprint that never includes secrets."""
    return sha256_fingerprint(safe_ai_payload(payload))
