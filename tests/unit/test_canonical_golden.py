"""Golden-vector tests for the strict ``jcs-v1`` canonical JSON profile.

The vectors are frozen: any change to object ordering, string escaping,
number rendering, whitespace, UTF-8 encoding, or unsafe-value rejection
fails here before persisted identities can drift silently.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

import pytest

from nl2data_core.canonical import (
    CANONICALIZATION_PROFILE_JCS,
    strict_canonical_bytes,
    strict_canonical_json,
    strict_sha256_fingerprint,
)


class _GoldenVector:
    """One frozen canonical-bytes/fingerprint pair."""

    def __init__(self, name: str, payload: object, canonical: str) -> None:
        self.name = name
        self.payload = payload
        self.canonical = canonical
        self.fingerprint = strict_sha256_fingerprint(payload)


VECTORS = [
    _GoldenVector(
        "object_member_ordering",
        {"b": [1, 2, 3], "a": {"nested": "v\u00e4l"}, "c": True, "d": None},
        '{"a":{"nested":"v\u00e4l"},"b":[1,2,3],"c":true,"d":null}',
    ),
    _GoldenVector(
        "unicode_strings_preserved",
        {"key": "caf\u00e9 \u4e2d\u6587 \U0001f600", "e\u0301": "x"},
        '{"e\u0301":"x","key":"caf\u00e9 \u4e2d\u6587 \U0001f600"}',
    ),
    _GoldenVector(
        "minimal_string_escaping",
        {"s": 'quote" back\\ slash \b\f\n\r\t ctrl\x01\x1f del\x7f line\u2028'},
        '{"s":"quote\\" back\\\\ slash \\b\\f\\n\\r\\t ctrl\\u0001\\u001f del\x7f line\u2028"}',
    ),
    _GoldenVector(
        "number_rendering",
        {
            "a": 0,
            "b": -7,
            "c": 10**21,
            "d": 0.0,
            "e": -0.0,
            "f": 2.0,
            "g": 0.5,
            "h": -0.5,
            "i": 1e16,
            "j": 1e21,
            "k": 1e-6,
            "l": 1e-7,
            "m": 1.5e-7,
            "n": 123.456,
            "o": 3.141592653589793,
            "p": 1 / 3,
            "q": 1e20,
        },
        (
            '{"a":0,"b":-7,"c":1000000000000000000000,"d":0,"e":0,"f":2,"g":0.5,'
            '"h":-0.5,"i":10000000000000000,"j":1e+21,"k":0.000001,"l":1e-7,'
            '"m":1.5e-7,"n":123.456,"o":3.141592653589793,"p":0.3333333333333333,'
            '"q":100000000000000000000}'
        ),
    ),
    _GoldenVector(
        "array_order_is_semantic",
        {"arr": ["z", "a", 10, 2]},
        '{"arr":["z","a",10,2]}',
    ),
    _GoldenVector(
        "empty_containers",
        {"obj": {}, "arr": []},
        '{"arr":[],"obj":{}}',
    ),
]

#: UTF-16 code-unit ordering differs from code-point ordering here: the
#: supplementary character U+10000 (encoded as surrogate D800 DC00) sorts
#: before U+FFFF under JCS ordering.
UTF16_ORDERING_CANONICAL = '{"\U00010000":1,"\uffff":2}'


@pytest.mark.parametrize("vector", VECTORS, ids=lambda vector: vector.name)
def test_golden_canonical_bytes_are_frozen(vector: _GoldenVector) -> None:
    assert strict_canonical_json(vector.payload) == vector.canonical
    assert strict_canonical_bytes(vector.payload) == vector.canonical.encode("utf-8")
    assert not strict_canonical_bytes(vector.payload).startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("vector", VECTORS, ids=lambda vector: vector.name)
def test_golden_fingerprints_are_frozen(vector: _GoldenVector) -> None:
    assert strict_sha256_fingerprint(vector.payload) == vector.fingerprint
    assert vector.fingerprint.startswith("sha256:")


def test_insertion_order_does_not_change_golden_bytes() -> None:
    reordered = dict(reversed(list(VECTORS[0].payload.items())))  # type: ignore[arg-type]
    assert strict_canonical_json(reordered) == VECTORS[0].canonical
    assert strict_sha256_fingerprint(reordered) == VECTORS[0].fingerprint


def test_object_keys_sort_by_utf16_code_units() -> None:
    assert strict_canonical_json({"\U00010000": 1, "\uffff": 2}) == UTF16_ORDERING_CANONICAL


def test_array_order_changes_identity() -> None:
    first = strict_sha256_fingerprint({"arr": [1, 2]})
    second = strict_sha256_fingerprint({"arr": [2, 1]})
    assert first != second


def test_composed_and_decomposed_strings_have_different_identities() -> None:
    composed = strict_sha256_fingerprint({"v": "caf\N{LATIN SMALL LETTER E WITH ACUTE}"})
    decomposed = strict_sha256_fingerprint({"v": "cafe\N{COMBINING ACUTE ACCENT}"})
    assert composed != decomposed


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1, tzinfo=UTC),
        {"set"},
        ("t",),
        b"bytes",
        Decimal("1.5"),
        ValueError("boom"),
    ],
)
def test_unsafe_values_fail_closed_with_path_and_type(value: object) -> None:
    with pytest.raises(ValueError, match="JSON-safe"):
        strict_canonical_json({"outer": {"v": value}})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_fail_closed(value: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        strict_canonical_json({"v": value})


def test_callable_and_enum_values_fail_closed() -> None:
    class _Kind(Enum):
        A = "a"

    with pytest.raises(ValueError, match="JSON-safe"):
        strict_canonical_json({"v": _Kind.A})
    with pytest.raises(ValueError, match="JSON-safe"):
        strict_canonical_json({"v": lambda: None})


def test_profile_constant_is_the_strict_identifier() -> None:
    assert CANONICALIZATION_PROFILE_JCS == "jcs-v1"
