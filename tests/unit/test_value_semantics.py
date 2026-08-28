"""Unit tests for value-level semantics (change: semantic-value-semantics).

Covers the ``ValueSemantics`` model contract (bounds, immutability,
canonical payloads, safe-content validators), the N6 omit-when-unset
fingerprint invariant across descriptor, snapshot, and bundle layers,
and the D4 snapshot-breaking chain for any declared-mapping content
edit (stale bundle rejection and republication).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.unit.test_semantic_model_bundles import (
    fp,
    make_bundle,
    make_descriptor,
    make_field,
    make_source,
)

from nl2data_core.bundles import (
    BundleCompatibility,
    BundleProvenance,
    BundleQualityStatus,
    SemanticModelBundle,
    validate_bundle,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.views import (
    ValueSemantics,
)


def make_vs(**overrides) -> ValueSemantics:
    values = {
        "value_mapping": {"paid": "PAID", "refunded": "REFUNDED"},
        "display_order": ("paid", "refunded"),
        "sample_values": ("PAID", "REFUNDED"),
    }
    values.update(overrides)
    return ValueSemantics(**values)


def with_status_semantics(descriptor_fingerprint: str, **vs_overrides):
    """A descriptor whose ``order.status`` field carries value semantics."""
    descriptor = make_descriptor(catalog_fingerprint=descriptor_fingerprint)
    order = descriptor.entities[1]
    status = next(f for f in order.fields if f.field_id == "status")
    semantic_status = status.model_copy(
        update={"value_semantics": make_vs(**vs_overrides)}
    )
    fields = tuple(
        semantic_status if f.field_id == "status" else f for f in order.fields
    )
    return descriptor.model_copy(
        update={
            "entities": tuple(
                order.model_copy(update={"fields": fields})
                if e.entity_id == "order"
                else e
                for e in descriptor.entities
            )
        }
    )


class TestValueSemanticsModel:
    def test_defaults_and_known_terms(self) -> None:
        semantics = make_vs()
        assert semantics.pii is False
        assert semantics.unknown_value_policy == "reject"
        assert semantics.known_business_terms == ("paid", "refunded")

    def test_empty_mapping_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_vs(value_mapping={})

    def test_boolean_stored_values_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_vs(value_mapping={"paid": True})

    def test_float_stored_values_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_vs(value_mapping={"paid": 1.5})

    def test_boolean_sample_values_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_vs(sample_values=(True,))

    def test_mapping_entry_count_is_bounded(self) -> None:
        too_many = {f"term_{i}": f"S{i}" for i in range(4_097)}
        with pytest.raises(ValidationError):
            make_vs(value_mapping=too_many)
        at_limit = {f"term_{i}": f"S{i}" for i in range(4_096)}
        assert len(make_vs(value_mapping=at_limit).value_mapping) == 4_096

    def test_mapping_key_length_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_vs(value_mapping={"x" * 129: "PAID"})
        assert make_vs(value_mapping={"x" * 128: "PAID"}) is not None

    def test_display_order_terms_are_bounded_and_unique(self) -> None:
        with pytest.raises(ValidationError):
            make_vs(display_order=("",))
        with pytest.raises(ValidationError):
            make_vs(display_order=("x" * 129,))
        with pytest.raises(ValidationError):
            make_vs(display_order=("paid", "paid"))

    def test_unknown_value_policy_is_a_strict_literal(self) -> None:
        with pytest.raises(ValidationError):
            make_vs(unknown_value_policy="ignore")

    def test_unknown_members_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_vs(stored_enum=("PAID",))

    def test_json_wire_safe_without_frozensets(self) -> None:
        semantics = make_vs()
        payload = json.loads(semantics.model_dump_json())
        assert payload["value_mapping"] == {
            "paid": "PAID",
            "refunded": "REFUNDED",
        }
        assert payload["display_order"] == ["paid", "refunded"]

    def test_model_is_frozen(self) -> None:
        semantics = make_vs()
        with pytest.raises((TypeError, ValidationError)):
            semantics.pii = True
        with pytest.raises(TypeError):
            semantics.value_mapping["paid"] = "EVIL"  # type: ignore[index]

    def test_sample_values_are_documented_as_prompt_context_only(self) -> None:
        doc = ValueSemantics.__doc__ or ""
        assert "sample_values" in doc
        assert "prompt-context" in doc


class TestN6OmitWhenUnset:
    def test_unset_member_is_omitted_from_canonical_payload(self) -> None:
        field = make_field("status")
        assert "value_semantics" not in field.canonical_payload()

    def test_set_member_is_included_in_canonical_payload(self) -> None:
        field = make_field("status").model_copy(
            update={"value_semantics": make_vs()}
        )
        payload = field.canonical_payload()
        assert payload["value_semantics"] == make_vs().canonical_payload()

    def test_descriptor_fingerprint_unchanged_by_explicitly_unset_member(
        self,
    ) -> None:
        implicit = make_field("status")
        explicit = make_field("status", value_semantics=None)
        assert implicit == explicit
        assert implicit.canonical_payload() == explicit.canonical_payload()

    def test_snapshot_fingerprint_unchanged_by_explicitly_unset_member(
        self,
    ) -> None:
        baseline = make_descriptor()
        explicit = make_descriptor(
            entities=tuple(
                entity.model_copy(
                    update={
                        "fields": tuple(
                            f.model_copy(update={"value_semantics": None})
                            for f in entity.fields
                        )
                    }
                )
                for entity in baseline.entities
            )
        )
        assert sha256_fingerprint(baseline.canonical_payload()) == (
            sha256_fingerprint(explicit.canonical_payload())
        )

    def test_bundle_fingerprint_unchanged_by_explicitly_unset_member(
        self,
    ) -> None:
        # Pin provenance: ``created_at`` defaults to wall-clock time and is
        # unrelated to the N6 invariant under test.
        provenance = BundleProvenance(
            owner_reference="team-analytics",
            created_by_fingerprint=fp("11"),
            quality=BundleQualityStatus.VALIDATED,
            created_at="2026-01-01T00:00:00+00:00",
        )
        baseline = make_bundle(provenance=provenance)
        explicit = make_bundle(
            descriptor=make_descriptor(
                entities=tuple(
                    entity.model_copy(
                        update={
                            "fields": tuple(
                                f.model_copy(update={"value_semantics": None})
                                for f in entity.fields
                            )
                        }
                    )
                    for entity in baseline.descriptor.entities
                )
            ),
            provenance=provenance,
        )
        assert baseline.fingerprint == explicit.fingerprint

    def test_setting_semantics_changes_the_fingerprints(self) -> None:
        descriptor = make_descriptor()
        annotated = with_status_semantics(descriptor.catalog_fingerprint)
        assert descriptor != annotated
        assert sha256_fingerprint(descriptor.canonical_payload()) != (
            sha256_fingerprint(annotated.canonical_payload())
        )
        baseline_bundle = make_bundle(descriptor=descriptor)
        annotated_bundle = make_bundle(descriptor=annotated)
        assert baseline_bundle.fingerprint != annotated_bundle.fingerprint


class TestSnapshotBreakingChain:
    """Design D4: any ValueSemantics content edit is snapshot-breaking."""

    def _chain(self, **vs_overrides):
        old_snapshot = with_status_semantics(fp("ab"))
        old_fingerprint = sha256_fingerprint(old_snapshot.canonical_payload())
        new_snapshot = with_status_semantics(fp("ab"), **vs_overrides)
        new_fingerprint = sha256_fingerprint(new_snapshot.canonical_payload())
        return old_fingerprint, new_fingerprint

    def test_mapping_entry_edit_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain(
            value_mapping={"paid": "PAID", "charged": "CHARGED"}
        )
        assert old_fp != new_fp

    def test_sample_value_edit_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain(sample_values=("PAID",))
        assert old_fp != new_fp

    def test_policy_edit_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain(unknown_value_policy="warn")
        assert old_fp != new_fp

    def test_display_order_edit_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain(
            display_order=("refunded", "paid")
        )
        assert old_fp != new_fp

    def test_pii_flag_edit_is_snapshot_breaking(self) -> None:
        old_fp, new_fp = self._chain(pii=True)
        assert old_fp != new_fp

    def test_bundle_from_prior_snapshot_fails_catalog_compatibility(
        self,
    ) -> None:
        old_fp, new_fp = self._chain(unknown_value_policy="warn")
        stale_bundle = SemanticModelBundle(
            bundle_id="sales_model",
            model_version="1.0.0",
            descriptor=with_status_semantics(new_fp),
            measures=make_bundle().measures,
            grains=make_bundle().grains,
            sources=(make_source(catalog_fingerprint=new_fp),),
            compatibility=BundleCompatibility(
                compatible_catalog_fingerprints=frozenset({old_fp}),
                notes="Published before the mapping edit",
            ),
            provenance=BundleProvenance(
                owner_reference="team-analytics",
                quality=BundleQualityStatus.VALIDATED,
            ),
        )
        result = validate_bundle(stale_bundle)
        assert not result.valid
        assert "catalog_incompatible" in result.issue_codes()

    def test_republication_against_new_snapshot_restores_validity(
        self,
    ) -> None:
        old_fp, new_fp = self._chain(unknown_value_policy="warn")
        republished = SemanticModelBundle(
            bundle_id="sales_model",
            model_version="1.0.1",
            descriptor=with_status_semantics(new_fp),
            measures=make_bundle().measures,
            grains=make_bundle().grains,
            sources=(make_source(catalog_fingerprint=new_fp),),
            compatibility=BundleCompatibility(
                compatible_catalog_fingerprints=frozenset({new_fp}),
                notes="Republished after the mapping edit",
            ),
            provenance=BundleProvenance(
                owner_reference="team-analytics",
                quality=BundleQualityStatus.APPROVED,
            ),
        )
        assert validate_bundle(republished).valid

    def test_old_bundle_evidence_is_stale_after_republication(self) -> None:
        old_fp, new_fp = self._chain(unknown_value_policy="warn")
        old_bundle = make_bundle(descriptor=with_status_semantics(old_fp))
        new_bundle = make_bundle(
            descriptor=with_status_semantics(new_fp),
            model_version="1.0.1",
        )
        assert old_bundle.fingerprint != new_bundle.fingerprint
