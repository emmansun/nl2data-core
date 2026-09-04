"""Catalog envelope canonicalization-profile compatibility tests.

Covers strict-profile round trips, legacy-record classification for rows
written before profile metadata existed, fail-closed rejection of unknown
or tampered profiles, and proof that reload never silently rewrites a
stored fingerprint: the fingerprint is always revalidated under the
recorded profile and any mismatch is rejected.
"""

from __future__ import annotations

import json

import pytest

from nl2data_core.canonical import (
    CANONICALIZATION_PROFILE_JCS,
    CANONICALIZATION_PROFILE_LEGACY,
    sha256_fingerprint,
    strict_sha256_fingerprint,
)
from nl2data_semantic_catalog_postgres.envelope import (
    DEFAULT_CANONICALIZATION_PROFILE,
    ArtifactKind,
    CatalogEnvelope,
    EnvelopeRejectedError,
    decode_envelope,
    encode_envelope,
)

_KIND = ArtifactKind.SNAPSHOT
_BOUNDS = {
    "max_envelope_bytes": 1_048_576,
    "max_payload_bytes": 1_048_576,
}


def _payload() -> dict[str, object]:
    return {"snapshot_id": "snap-1", "objects": []}


def _encode_legacy_without_profile_member() -> str:
    """A pre-metadata record: legacy fingerprint, no profile member."""
    text = encode_envelope(
        _KIND,
        _payload(),
        sha256_fingerprint(_payload()),
        canonicalization_profile=CANONICALIZATION_PROFILE_LEGACY,
        **_BOUNDS,
    )
    raw = json.loads(text)
    assert raw["canonicalization_profile"] == CANONICALIZATION_PROFILE_LEGACY
    del raw["canonicalization_profile"]
    return json.dumps(raw)


class TestStrictProfileEnvelope:
    def test_default_encoding_records_the_strict_profile(self) -> None:
        text = encode_envelope(
            _KIND, _payload(), strict_sha256_fingerprint(_payload()), **_BOUNDS
        )
        assert DEFAULT_CANONICALIZATION_PROFILE == CANONICALIZATION_PROFILE_JCS
        raw = json.loads(text)
        assert raw["canonicalization_profile"] == CANONICALIZATION_PROFILE_JCS

    def test_strict_profile_round_trip_preserves_identity(self) -> None:
        fingerprint = strict_sha256_fingerprint(_payload())
        envelope = decode_envelope(
            encode_envelope(_KIND, _payload(), fingerprint, **_BOUNDS),
            expected_kind=_KIND,
            supported_schema_version=1,
            **_BOUNDS,
        )
        assert isinstance(envelope, CatalogEnvelope)
        assert envelope.canonicalization_profile == CANONICALIZATION_PROFILE_JCS
        assert envelope.fingerprint == fingerprint
        assert envelope.payload == _payload()


class TestLegacyRecordReadability:
    def test_record_without_profile_member_classifies_as_legacy(self) -> None:
        envelope = decode_envelope(
            _encode_legacy_without_profile_member(),
            expected_kind=_KIND,
            supported_schema_version=1,
            **_BOUNDS,
        )
        assert envelope.canonicalization_profile == CANONICALIZATION_PROFILE_LEGACY
        assert envelope.fingerprint == sha256_fingerprint(_payload())

    def test_explicit_legacy_profile_round_trip(self) -> None:
        fingerprint = sha256_fingerprint(_payload())
        envelope = decode_envelope(
            encode_envelope(
                _KIND,
                _payload(),
                fingerprint,
                canonicalization_profile=CANONICALIZATION_PROFILE_LEGACY,
                **_BOUNDS,
            ),
            expected_kind=_KIND,
            supported_schema_version=1,
            **_BOUNDS,
        )
        assert envelope.canonicalization_profile == CANONICALIZATION_PROFILE_LEGACY
        assert envelope.fingerprint == fingerprint


class TestIncompatibleProfileRejection:
    def test_unknown_declared_profile_fails_closed(self) -> None:
        text = encode_envelope(
            _KIND, _payload(), strict_sha256_fingerprint(_payload()), **_BOUNDS
        )
        raw = json.loads(text)
        raw["canonicalization_profile"] = "jcs-v2"
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            decode_envelope(
                json.dumps(raw),
                expected_kind=_KIND,
                supported_schema_version=1,
                **_BOUNDS,
            )
        assert excinfo.value.code == "incompatible_profile"

    def test_non_string_profile_fails_closed(self) -> None:
        text = encode_envelope(
            _KIND, _payload(), strict_sha256_fingerprint(_payload()), **_BOUNDS
        )
        raw = json.loads(text)
        raw["canonicalization_profile"] = 1
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            decode_envelope(
                json.dumps(raw),
                expected_kind=_KIND,
                supported_schema_version=1,
                **_BOUNDS,
            )
        assert excinfo.value.code == "malformed"


class TestNoSilentFingerprintRewriting:
    def test_profile_tamper_never_rewrites_the_stored_fingerprint(self) -> None:
        """A legacy record whose profile is relabeled strict must be
        rejected: its stored fingerprint is only valid under the recorded
        profile, and reload validates instead of recomputing identity."""
        # A float-valued payload renders differently under the two
        # profiles (1.0 -> "1" strict, "1.0" legacy), so the fingerprints
        # differ and relabeling cannot pass validation.
        payload = {"ratio": 1.0, "snapshot_id": "snap-1"}
        legacy_fingerprint = sha256_fingerprint(payload)
        assert legacy_fingerprint != strict_sha256_fingerprint(payload)
        text = encode_envelope(
            _KIND,
            payload,
            legacy_fingerprint,
            canonicalization_profile=CANONICALIZATION_PROFILE_LEGACY,
            **_BOUNDS,
        )
        raw = json.loads(text)
        raw["canonicalization_profile"] = CANONICALIZATION_PROFILE_JCS
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            decode_envelope(
                json.dumps(raw),
                expected_kind=_KIND,
                supported_schema_version=1,
                **_BOUNDS,
            )
        assert excinfo.value.code == "fingerprint_mismatch"

    def test_tampered_payload_fails_closed_under_recorded_profile(self) -> None:
        fingerprint = strict_sha256_fingerprint(_payload())
        text = encode_envelope(_KIND, _payload(), fingerprint, **_BOUNDS)
        raw = json.loads(text)
        raw["payload"] = {"snapshot_id": "snap-2", "objects": []}
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            decode_envelope(
                json.dumps(raw),
                expected_kind=_KIND,
                supported_schema_version=1,
                **_BOUNDS,
            )
        assert excinfo.value.code == "fingerprint_mismatch"


class TestSafeErrorNormalization:
    """Encoder rejections surface as bounded envelope errors, never as
    raw canonicalizer exceptions, so the catalog boundary stays normal."""

    def test_nonpositive_schema_version_is_malformed(self) -> None:
        text = encode_envelope(
            _KIND, _payload(), strict_sha256_fingerprint(_payload()), **_BOUNDS
        )
        raw = json.loads(text)
        raw["schema_version"] = 0
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            decode_envelope(
                json.dumps(raw),
                expected_kind=_KIND,
                supported_schema_version=1,
                **_BOUNDS,
            )
        assert excinfo.value.code == "malformed"

    def test_strict_encode_rejects_tuple_payload_as_unsafe(self) -> None:
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            encode_envelope(
                _KIND,
                {"snapshot_id": "snap-1", "objects": [("row",)]},
                strict_sha256_fingerprint({"snapshot_id": "snap-1"}),
                **_BOUNDS,
            )
        assert excinfo.value.code == "unsafe_payload"

    def test_legacy_nfc_colliding_keys_fail_closed(self) -> None:
        payload = {"cafe\u0301": 1, "caf\u00e9": 2}
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            encode_envelope(
                _KIND,
                payload,
                "sha256:" + "0" * 64,
                canonicalization_profile=CANONICALIZATION_PROFILE_LEGACY,
                **_BOUNDS,
            )
        assert excinfo.value.code == "unsafe_payload"
