"""Unit tests for canonical JSON and fingerprint helpers.

Covers characterization of the legacy ``legacy-deterministic-json-v1``
compatibility profile (key ordering, NFC normalization, sets, datetimes,
floats, unknown objects) and the strict ``jcs-v1`` profile basics
(JSON-safe validation, structured rejection errors, profile resolution).
Exhaustive strict-profile golden vectors live in
``tests/unit/test_canonical_golden.py``.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from enum import Enum

import pytest

from nl2data_core.canonical import (
    CANONICALIZATION_PROFILE_JCS,
    CANONICALIZATION_PROFILE_LEGACY,
    CanonicalizationError,
    canonical_json,
    canonical_value,
    resolve_canonicalization_profile,
    sha256_fingerprint,
    strict_canonical_json,
    strict_sha256_fingerprint,
    validate_json_safe,
)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


# -- legacy profile characterization (frozen behavior) -------------------------


def test_nfc_normalizes_object_keys_and_string_values() -> None:
    composed = {
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}": (
            "r\N{LATIN SMALL LETTER E WITH ACUTE}sum\N{LATIN SMALL LETTER E WITH ACUTE}"
        )
    }
    decomposed = {
        "cafe\N{COMBINING ACUTE ACCENT}": (
            "re\N{COMBINING ACUTE ACCENT}sume\N{COMBINING ACUTE ACCENT}"
        )
    }
    assert canonical_json(composed) == canonical_json(decomposed)
    assert sha256_fingerprint(composed) == sha256_fingerprint(decomposed)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_json_numbers_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="JSON compliant"):
        canonical_json({"value": value})


def test_keys_colliding_after_nfc_normalization_are_rejected() -> None:
    payload = {
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}": 1,
        "cafe\N{COMBINING ACUTE ACCENT}": 2,
    }
    with pytest.raises(ValueError, match="unique after NFC"):
        canonical_json(payload)


def test_mapping_and_set_order_remain_canonical() -> None:
    first = {"b": {3, 1, 2}, "a": "value"}
    second = {"a": "value", "b": {2, 3, 1}}
    assert canonical_json(first) == canonical_json(second)


def test_key_insertion_order_does_not_change_legacy_bytes() -> None:
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})
    assert canonical_json({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_legacy_datetimes_are_isoformatted() -> None:
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    assert canonical_json({"at": stamp}) == '{"at":"2026-01-01T00:00:00+00:00"}'


def test_legacy_tuples_are_arrays() -> None:
    assert canonical_json({"v": (1, "a")}) == '{"v":[1,"a"]}'
    assert canonical_value({"v": (frozenset({"b", "a"}),)}) == {"v": [["a", "b"]]}


def test_legacy_unknown_objects_are_stringified() -> None:
    class _Native:
        def __str__(self) -> str:
            return "native-rendering"

    assert canonical_json({"v": _Native()}) == '{"v":"native-rendering"}'


def test_legacy_float_rendering_matches_json_dumps() -> None:
    assert canonical_json({"v": 2.0}) == '{"v":2.0}'
    assert canonical_json({"v": 1e16}) == '{"v":1e+16}'
    assert canonical_json({"v": 1e-7}) == '{"v":1e-07}'


def test_legacy_fingerprint_format_is_stable() -> None:
    fingerprint = sha256_fingerprint({"a": 1, "b": [True, None, "x"]})
    assert fingerprint == sha256_fingerprint({"b": [True, None, "x"], "a": 1})
    assert _FINGERPRINT_PATTERN.fullmatch(fingerprint) is not None


# -- strict profile basics ------------------------------------------------------


def test_strict_profile_rejects_native_objects_fail_closed() -> None:
    class _Native:
        pass

    class _Kind(Enum):
        A = "a"

    unsafe: list[object] = [
        datetime.now(UTC),
        {"a"},
        ("t",),
        b"bytes",
        ValueError("boom"),
        _Kind.A,
        _Native(),
        lambda: None,
    ]
    for value in unsafe:
        with pytest.raises(CanonicalizationError) as excinfo:
            strict_canonical_json({"v": value})
        error = excinfo.value
        assert error.path == "$.v"
        assert error.value_type
        assert error.safe_payload()["path"] == "$.v"


def test_strict_profile_rejects_non_finite_numbers() -> None:
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(CanonicalizationError):
            strict_canonical_json({"v": value})


def test_strict_profile_rejects_non_string_keys() -> None:
    with pytest.raises(CanonicalizationError):
        strict_canonical_json({1: "a"})


def test_strict_profile_accepts_json_safe_values() -> None:
    payload = {"a": [1, 2.5, True, False, None, "text"], "b": {"c": {}}}
    validate_json_safe(payload)
    assert strict_canonical_json(payload) == (
        '{"a":[1,2.5,true,false,null,"text"],"b":{"c":{}}}'
    )


def test_strict_fingerprint_format_is_stable() -> None:
    fingerprint = strict_sha256_fingerprint({"a": 1, "b": [True, None, "x"]})
    assert _FINGERPRINT_PATTERN.fullmatch(fingerprint) is not None
    assert fingerprint == strict_sha256_fingerprint({"b": [True, None, "x"], "a": 1})


# -- canonicalization profile resolution ----------------------------------------


def test_missing_profile_metadata_classifies_as_legacy() -> None:
    assert resolve_canonicalization_profile(None) == CANONICALIZATION_PROFILE_LEGACY
    assert resolve_canonicalization_profile("") == CANONICALIZATION_PROFILE_LEGACY


def test_supported_profiles_resolve_to_themselves() -> None:
    assert resolve_canonicalization_profile("jcs-v1") == CANONICALIZATION_PROFILE_JCS
    assert (
        resolve_canonicalization_profile("legacy-deterministic-json-v1")
        == CANONICALIZATION_PROFILE_LEGACY
    )


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(CanonicalizationError, match="unsupported canonicalization profile"):
        resolve_canonicalization_profile("jcs-v9")
