"""Unit tests for strict canonical JSON and fingerprint normalization."""

from __future__ import annotations

import math

import pytest

from nl2data_core.canonical import canonical_json, sha256_fingerprint


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