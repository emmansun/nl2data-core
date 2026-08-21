"""Contract tests for the generic artifact lifecycle and stable fingerprints."""

from __future__ import annotations

from nl2data_core.adapters.fingerprint import artifact_fingerprint
from nl2data_core.adapters.models import (
    ExecutionResult,
    GeneratedArtifact,
    ParsedArtifact,
    ValidatedArtifact,
    ValidationContext,
)

FINGERPRINT = "sha256:" + "ab" * 32


class TestArtifactLifecycle:
    def test_each_stage_is_representable_with_canonical_models(self) -> None:
        context = ValidationContext(snapshot_fingerprint=FINGERPRINT)

        generated = GeneratedArtifact(
            artifact_id="art-1",
            fingerprint=FINGERPRINT,
            content_type="text/plain",
            size_bytes=12,
            metadata={"stage": "generated"},
        )
        parsed = ParsedArtifact(
            artifact_id="art-1",
            fingerprint=FINGERPRINT,
            parse_metadata={"stage": "parsed"},
        )
        validated = ValidatedArtifact(
            artifact_id="art-1",
            fingerprint=FINGERPRINT,
            snapshot_fingerprint=context.snapshot_fingerprint,
            validation_metadata={"stage": "validated"},
        )
        executed = ExecutionResult(
            result_id="res-1",
            fingerprint=FINGERPRINT,
            row_count=1,
            columns=("c",),
            duration_ms=5,
            metadata={"stage": "executed"},
        )

        assert generated.artifact_id == parsed.artifact_id == validated.artifact_id
        assert executed.fingerprint == validated.fingerprint

    def test_artifact_models_reject_unknown_fields(self) -> None:
        try:
            GeneratedArtifact(
                artifact_id="a", fingerprint=FINGERPRINT, content_type="t", raw_payload="x"
            )
        except Exception as exc:  # noqa: BLE001
            assert "raw_payload" in str(exc)
        else:
            raise AssertionError("raw payload field was accepted")

    def test_fingerprints_are_stable_across_repeated_calculation(self) -> None:
        payload = {"statement": "select 1", "snapshot": "s-1", "adapter": "sql"}
        first = artifact_fingerprint(payload)
        for _ in range(5):
            assert artifact_fingerprint(payload) == first

    def test_fingerprint_is_canonical_executable_identity(self) -> None:
        payload_a = {"statement": "select 1", "snapshot": "s-1"}
        payload_b = {"statement": "select 2", "snapshot": "s-1"}
        assert artifact_fingerprint(payload_a) != artifact_fingerprint(payload_b)
