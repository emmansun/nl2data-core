"""Canonical JSON serialization and fingerprint helpers.

Two canonicalization profiles are defined for fingerprint-critical payloads:

``jcs-v1`` (strict, identity-critical)
    The shared fingerprint-critical contract: UTF-8 JSON without a byte
    order mark, object member names sorted deterministically by UTF-16
    code units (JCS/RFC 8785 ordering), minimal whitespace, minimal JSON
    string escaping, ES6-compatible number rendering, and fail-closed
    rejection of every non-JSON value.  Domain models prepare payloads
    (datetimes, enums, sets, tuples) into JSON-safe values *before*
    canonicalization; the canonicalizer never stringifies or invents
    representations.

``legacy-deterministic-json-v1`` (compatibility)
    The historical behavior kept for records whose fingerprints were
    produced before the strict profile existed: NFC string normalization,
    set/tuple/datetime normalization, and ``str()`` coercion of unknown
    objects.  Existing fingerprints never drift; fingerprint-critical
    domains must migrate to the strict helpers, and the legacy helpers
    remain only as documented compatibility wrappers.

Fingerprints are always ``sha256:<lowercase hex digest>`` over the
canonical UTF-8 bytes.  Callers must pass payloads that already exclude
secrets.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any

#: Strict JCS-compatible canonicalization profile for identity-critical payloads.
CANONICALIZATION_PROFILE_JCS = "jcs-v1"

#: Historical deterministic JSON profile preserved for legacy records.
CANONICALIZATION_PROFILE_LEGACY = "legacy-deterministic-json-v1"

#: Every canonicalization profile this runtime understands.
SUPPORTED_CANONICALIZATION_PROFILES = frozenset(
    {CANONICALIZATION_PROFILE_JCS, CANONICALIZATION_PROFILE_LEGACY}
)

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class CanonicalizationError(ValueError):
    """A payload value cannot be canonicalized under the strict profile.

    ``path`` locates the offending value inside the payload (``$`` is the
    root) and ``value_type`` names the rejected Python type, so callers can
    return a safe structured error without exposing payload content.
    """

    def __init__(self, message: str, *, path: str = "$", value_type: str) -> None:
        super().__init__(message)
        self.path = path
        self.value_type = value_type

    def safe_payload(self) -> dict[str, str]:
        """Bounded safe error facts for structured error surfaces."""
        return {
            "path": self.path,
            "value_type": self.value_type,
            "message": str(self),
        }


def validate_json_safe(value: Any, *, path: str = "$") -> None:
    """Reject any value outside the strict JSON-safe contract.

    Accepts mappings with string keys, lists, strings, integers, finite
    numbers, booleans, and null.  Rejects datetimes, sets, tuples, bytes,
    decimals, enums, callables, exceptions, native clients, arbitrary
    objects, non-string mapping keys, NaN, and infinities.  Raises
    :class:`CanonicalizationError` with the offending path and type.
    """
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, Enum):
        raise CanonicalizationError(
            "enums must be prepared to their JSON-safe value before canonicalization",
            path=path,
            value_type=type(value).__name__,
        )
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(
                "non-finite numbers are not JSON-safe values",
                path=path,
                value_type=type(value).__name__,
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(
                    "object keys must be prepared strings",
                    path=f"{path}.{key!r}",
                    value_type=type(key).__name__,
                )
            validate_json_safe(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json_safe(item, path=f"{path}[{index}]")
        return
    raise CanonicalizationError(
        "value is not a JSON-safe object, array, string, number, boolean, or null",
        path=path,
        value_type=type(value).__name__,
    )


def _es6_number(value: float) -> str:
    """Render one finite float with ES6/JCS number formatting.

    Uses the shortest round-trip digit string (``repr``) reformatted with
    the ECMAScript presentation rules, so equal doubles always render as
    equal canonical numbers (``2.0`` becomes ``2``, ``1e16`` becomes
    ``10000000000000000``, ``1e-7`` becomes ``1e-7``).
    """
    if value == 0.0:
        return "0"
    text = repr(value)
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    if "e" in text:
        mantissa, _, exponent_text = text.partition("e")
        exponent = int(exponent_text)
    else:
        mantissa, exponent = text, 0
    if "." in mantissa:
        integer_part, _, fraction_part = mantissa.partition(".")
    else:
        integer_part, fraction_part = mantissa, ""
    digits = integer_part + fraction_part
    point = len(integer_part)
    stripped = len(digits) - len(digits.lstrip("0"))
    digits = digits[stripped:] or "0"
    point -= stripped
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]
    digit_count = len(digits)
    n = point + exponent
    if digit_count <= n <= 21:
        body = digits + "0" * (n - digit_count)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        scientific_exponent = n - 1
        mantissa_text = digits[0] + ("." + digits[1:] if digit_count > 1 else "")
        body = "{}e{}{}".format(
            mantissa_text,
            "+" if scientific_exponent >= 0 else "-",
            abs(scientific_exponent),
        )
    return "-" + body if negative else body


def _encode_string(value: str) -> str:
    """Escape one string with the minimal JSON/JCS escape set."""
    if not any(char in _ESCAPES or char < " " for char in value):
        return '"' + value + '"'
    parts = ['"']
    for char in value:
        escape = _ESCAPES.get(char)
        if escape is not None:
            parts.append(escape)
        elif char < " ":
            parts.append(f"\\u{ord(char):04x}")
        else:
            parts.append(char)
    parts.append('"')
    return "".join(parts)


def _encode(value: Any, out: list[str]) -> None:
    """Append the strict canonical JSON text of a validated JSON-safe value."""
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(_encode_string(value))
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_es6_number(value))
    elif isinstance(value, list):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, Mapping):
        out.append("{")
        first = True
        for key in sorted(value, key=lambda name: name.encode("utf-16-be")):
            if not first:
                out.append(",")
            first = False
            out.append(_encode_string(key))
            out.append(":")
            _encode(value[key], out)
        out.append("}")
    else:  # pragma: no cover - validate_json_safe rejects everything else first
        raise CanonicalizationError(
            "value is not a JSON-safe object, array, string, number, boolean, or null",
            value_type=type(value).__name__,
        )


def strict_canonical_json(payload: Any) -> str:
    """Render a JSON-safe payload as strict ``jcs-v1`` canonical JSON text."""
    validate_json_safe(payload)
    out: list[str] = []
    _encode(payload, out)
    return "".join(out)


def strict_canonical_bytes(payload: Any) -> bytes:
    """Render a JSON-safe payload as strict canonical UTF-8 bytes (no BOM)."""
    return strict_canonical_json(payload).encode("utf-8")


def strict_sha256_fingerprint(payload: Any) -> str:
    """Compute ``sha256:<lowercase hex>`` over strict canonical UTF-8 bytes."""
    digest = hashlib.sha256(strict_canonical_bytes(payload)).hexdigest()
    return f"sha256:{digest}"


def strict_blake2b_16_digest(value: str) -> str:
    """Compute a bounded blake2b-16 (128-bit) hex digest over UTF-8 bytes.

    Identity-critical callers use this when a deterministic rendered form
    would exceed a bounded identifier and a short digest fallback is
    required.  Computation stays in the canonicalization owner so no
    source module hashes locally.
    """
    return hashlib.blake2b(value.encode("utf-8"), digest_size=16).hexdigest()


def resolve_canonicalization_profile(profile: str | None) -> str:
    """Classify a recorded canonicalization profile.

    Missing metadata (``None`` or empty) classifies as the legacy profile,
    because every record written before profile metadata existed used the
    historical deterministic JSON helper.  Unknown profiles fail closed
    with :class:`CanonicalizationError`.
    """
    if profile is None or profile == "":
        return CANONICALIZATION_PROFILE_LEGACY
    if profile not in SUPPORTED_CANONICALIZATION_PROFILES:
        raise CanonicalizationError(
            f"unsupported canonicalization profile: {profile}",
            value_type="str",
        )
    return profile


def profile_fingerprint(profile: str, payload: Any) -> str:
    """Compute a fingerprint under an explicit canonicalization profile."""
    resolved = resolve_canonicalization_profile(profile)
    if resolved == CANONICALIZATION_PROFILE_JCS:
        return strict_sha256_fingerprint(payload)
    return sha256_fingerprint(payload)


# -- legacy compatibility profile (do not use for new identity-critical code) --

def canonical_value(value: Any) -> Any:
    """Normalize a value into a JSON-serializable, order-independent form.

    Legacy ``legacy-deterministic-json-v1`` behavior: NFC string
    normalization, set/tuple/datetime normalization, and ``str()`` coercion
    of unknown objects.  Fingerprint-critical domains must use the strict
    helpers instead.
    """
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
    """Render a payload as legacy deterministic canonical JSON text.

    Compatibility wrapper for the legacy profile; new fingerprint-critical
    code must use :func:`strict_canonical_json`.
    """
    return json.dumps(
        canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_fingerprint(payload: Any) -> str:
    """Compute a ``sha256:<lowercase hex>`` fingerprint of legacy canonical form.

    Compatibility wrapper for the legacy profile; new fingerprint-critical
    code must use :func:`strict_sha256_fingerprint`.
    """
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
