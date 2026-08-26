"""Bounded canonical JSON envelopes for persisted catalog artifacts.

Every artifact persisted by the catalog is wrapped in a versioned envelope::

    {
        "schema_version": 1,
        "kind": "snapshot" | "proposal_set" | "bundle",
        "fingerprint": "sha256:<64 hex>",
        "payload": { ... canonical safe payload ... }
    }

Encoding validates the payload *before* persistence: the payload must be a
JSON-native mapping, byte-bounded, and its canonical fingerprint must equal
the declared fingerprint, so an unsafe or inconsistent artifact is rejected
before any row is written.  Decoding revalidates everything after a read:
schema version (a newer envelope fails closed), kind, fingerprint, and byte
bounds, so a tampered, truncated, or forward-incompatible row is never
reinterpreted.  All failures raise :class:`EnvelopeRejectedError` with a
bounded safe reason code and message - never backend text or payload content.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from nl2data_core.canonical import canonical_json, sha256_fingerprint
from pydantic import BaseModel, ConfigDict, Field

#: The only envelope structure version this runtime understands.
ENVELOPE_SCHEMA_VERSION = 1

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

#: JSON-native scalar types accepted inside a safe payload.
_JSON_SCALARS = (str, int, float, bool, type(None))

#: Bounded reason codes carried by :class:`EnvelopeRejectedError`.
_MALFORMED = "malformed"
_UNSAFE_PAYLOAD = "unsafe_payload"
_UNKNOWN_KIND = "unknown_kind"
_KIND_MISMATCH = "kind_mismatch"
_NEWER_SCHEMA = "newer_schema"
_FINGERPRINT_MISMATCH = "fingerprint_mismatch"
_OVERSIZED = "oversized"


class ArtifactKind(StrEnum):
    """Kinds of artifacts the catalog persists as envelopes."""

    SNAPSHOT = "snapshot"
    PROPOSAL_SET = "proposal_set"
    BUNDLE = "bundle"


class EnvelopeRejectedError(Exception):
    """A persisted artifact failed safe envelope validation.

    ``code`` is one of the bounded reason codes above; ``message`` is a
    short safe description that never includes payload content or backend
    text.  The catalog boundary normalizes this into its public error
    vocabulary without leaking details.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def safe_payload(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _assert_json_safe(value: Any, path: str) -> None:
    """Reject any payload value that is not JSON-native and bounded."""
    if isinstance(value, Mapping):
        if len(value) > 65_536:
            raise EnvelopeRejectedError(
                _UNSAFE_PAYLOAD, "envelope payload mappings are unbounded"
            )
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise EnvelopeRejectedError(
                    _UNSAFE_PAYLOAD, "envelope payload keys must be bounded strings"
                )
            _assert_json_safe(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 65_536:
            raise EnvelopeRejectedError(
                _UNSAFE_PAYLOAD, "envelope payload collections are unbounded"
            )
        for index, item in enumerate(value):
            _assert_json_safe(item, f"{path}[{index}]")
        return
    if isinstance(value, _JSON_SCALARS):
        if isinstance(value, str) and len(value) > 1_048_576:
            raise EnvelopeRejectedError(
                _UNSAFE_PAYLOAD, "envelope payload strings are unbounded"
            )
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):  # noqa: E501
            raise EnvelopeRejectedError(
                _UNSAFE_PAYLOAD, "envelope payload floats must be finite"
            )
        return
    raise EnvelopeRejectedError(
        _UNSAFE_PAYLOAD, "envelope payloads must be JSON-native values"
    )


def _utf8_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def encode_envelope(
    kind: ArtifactKind,
    payload: Mapping[str, Any],
    fingerprint: str,
    *,
    max_envelope_bytes: int,
    max_payload_bytes: int,
) -> str:
    """Encode one artifact as a validated, bounded canonical envelope.

    Raises :class:`EnvelopeRejectedError` when the kind is unknown, the
    payload is not a bounded JSON-native mapping, its canonical fingerprint
    does not match ``fingerprint``, or either byte bound is exceeded.
    """
    if not isinstance(payload, Mapping):
        raise EnvelopeRejectedError(
            _UNSAFE_PAYLOAD, "envelope payloads must be mappings"
        )
    if re.fullmatch(_FINGERPRINT_PATTERN, fingerprint) is None:
        raise EnvelopeRejectedError(
            _FINGERPRINT_MISMATCH, "envelope fingerprint is malformed"
        )
    _assert_json_safe(payload, "payload")
    if sha256_fingerprint(payload) != fingerprint:
        raise EnvelopeRejectedError(
            _FINGERPRINT_MISMATCH,
            "envelope fingerprint does not match the canonical payload",
        )
    payload_text = canonical_json(payload)
    if _utf8_bytes(payload_text) > max_payload_bytes:
        raise EnvelopeRejectedError(_OVERSIZED, "envelope payload exceeds its bound")
    envelope_text = canonical_json(
        {
            "schema_version": ENVELOPE_SCHEMA_VERSION,
            "kind": kind.value,
            "fingerprint": fingerprint,
            "payload": json.loads(payload_text),
        }
    )
    if _utf8_bytes(envelope_text) > max_envelope_bytes:
        raise EnvelopeRejectedError(_OVERSIZED, "envelope exceeds its bound")
    return envelope_text


class CatalogEnvelope(BaseModel):
    """Validated envelope read back from the catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(ge=1)
    kind: ArtifactKind
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    payload: dict[str, Any]


def decode_envelope(
    text: str,
    *,
    expected_kind: ArtifactKind,
    supported_schema_version: int,
    max_envelope_bytes: int,
    max_payload_bytes: int,
) -> CatalogEnvelope:
    """Decode and fully revalidate one persisted envelope.

    Raises :class:`EnvelopeRejectedError` on any malformed, oversized,
    unknown-kind, newer-schema, kind-mismatched, or fingerprint-mismatched
    envelope.  A newer schema version or a fingerprint mismatch fails
    closed: the artifact is never returned to callers.
    """
    if not isinstance(text, str) or not text:
        raise EnvelopeRejectedError(_MALFORMED, "envelope is empty or malformed")
    if _utf8_bytes(text) > max_envelope_bytes:
        raise EnvelopeRejectedError(_OVERSIZED, "envelope exceeds its bound")
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise EnvelopeRejectedError(_MALFORMED, "envelope is not valid JSON") from exc
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version",
        "kind",
        "fingerprint",
        "payload",
    }:
        raise EnvelopeRejectedError(_MALFORMED, "envelope structure is invalid")
    schema_version = raw["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise EnvelopeRejectedError(_MALFORMED, "envelope schema version is invalid")
    if schema_version > supported_schema_version:
        raise EnvelopeRejectedError(
            _NEWER_SCHEMA, "envelope schema version is newer than supported"
        )
    kind_value = raw["kind"]
    if not isinstance(kind_value, str) or kind_value not in ArtifactKind._value2member_map_:
        raise EnvelopeRejectedError(_UNKNOWN_KIND, "envelope kind is unknown")
    kind = ArtifactKind(kind_value)
    if kind is not expected_kind:
        raise EnvelopeRejectedError(_KIND_MISMATCH, "envelope kind does not match")
    fingerprint = raw["fingerprint"]
    if not isinstance(fingerprint, str) or re.fullmatch(_FINGERPRINT_PATTERN, fingerprint) is None:
        raise EnvelopeRejectedError(_FINGERPRINT_MISMATCH, "envelope fingerprint is malformed")
    payload = raw["payload"]
    if not isinstance(payload, Mapping):
        raise EnvelopeRejectedError(_MALFORMED, "envelope payload is invalid")
    _assert_json_safe(payload, "payload")
    if _utf8_bytes(canonical_json(payload)) > max_payload_bytes:
        raise EnvelopeRejectedError(_OVERSIZED, "envelope payload exceeds its bound")
    if sha256_fingerprint(payload) != fingerprint:
        raise EnvelopeRejectedError(
            _FINGERPRINT_MISMATCH,
            "envelope fingerprint does not match the canonical payload",
        )
    return CatalogEnvelope(
        schema_version=schema_version,
        kind=kind,
        fingerprint=fingerprint,
        payload=dict(payload),
    )
