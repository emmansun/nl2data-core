"""Safe artifact canonicalization and fingerprint generation.

Fingerprints use ``sha256:<lowercase hexadecimal digest>`` and exclude
raw credentials and unapproved tenant identifiers.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nl2data_core.canonical import sha256_fingerprint

_SECRET_KEY_TOKENS = (
    "secret",
    "credential",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "private_key",
)


def _strip_sensitive(
    payload: Mapping[str, Any], approved_tenant_keys: frozenset[str]
) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        name = str(key)
        lowered = name.lower()
        if any(token in lowered for token in _SECRET_KEY_TOKENS):
            continue
        if "tenant" in lowered and name not in approved_tenant_keys:
            continue
        cleaned[name] = _strip_sensitive_value(value, approved_tenant_keys)
    return cleaned


def _strip_sensitive_value(value: Any, approved_tenant_keys: frozenset[str]) -> Any:
    if isinstance(value, Mapping):
        return _strip_sensitive(value, approved_tenant_keys)
    if isinstance(value, list):
        return [_strip_sensitive_value(item, approved_tenant_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_sensitive_value(item, approved_tenant_keys) for item in value)
    return value


def safe_artifact_payload(
    payload: Mapping[str, Any], *, approved_tenant_keys: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Return a canonical-safe copy with credentials and unapproved tenant IDs removed."""
    return _strip_sensitive(payload, approved_tenant_keys)


def artifact_fingerprint(
    payload: Mapping[str, Any], *, approved_tenant_keys: frozenset[str] = frozenset()
) -> str:
    """Compute a stable artifact fingerprint that never includes secrets.

    Equal payloads in different key orders produce the same fingerprint.
    """
    return sha256_fingerprint(
        safe_artifact_payload(payload, approved_tenant_keys=approved_tenant_keys)
    )
