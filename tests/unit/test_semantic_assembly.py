"""Unit tests for semantic assembly lifecycle models and identity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssemblyState,
    AssertionProvenance,
    AssertionProvenanceKind,
    AssertionType,
    DeploymentBinding,
    DraftRevisionConflict,
    ReviewState,
    SemanticAssertion,
    YamlAssemblyLoader,
)
from nl2data_core.canonical import sha256_fingerprint


def provenance() -> AssertionProvenance:
    return AssertionProvenance(kind=AssertionProvenanceKind.MANUAL)


def entity_assertion(**overrides: object) -> SemanticAssertion:
    payload: dict[str, object] = {
        "descriptor_id": "sales",
        "entity_id": "orders",
        "label": "Orders",
    }
    payload.update(overrides)
    return SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload=payload,
        provenance=provenance(),
    )


def draft(**overrides: object) -> AssemblyDraft:
    values: dict[str, object] = {
        "api_version": ASSEMBLY_API_VERSION,
        "draft_id": "draft-1",
        "bundle_id": "sales",
        "source_id": "sales",
        "model_version": "1.0.0",
        "assertions": (entity_assertion(),),
        "author_reference": "team-analytics",
    }
    values.update(overrides)
    return AssemblyDraft(**values)  # type: ignore[arg-type]


class TestLifecycle:
    def test_state_transitions_are_explicit_and_revisioned(self) -> None:
        review = draft().transition(expected_revision=0, state=AssemblyState.REVIEW)
        reviewed = review.mutate(
            expected_revision=1,
            assertions=(
                review.assertions[0].bind_review(
                    state=ReviewState.APPROVED,
                    reviewer_reference="reviewer-1",
                ),
            ),
        )
        approved = reviewed.transition(
            expected_revision=2,
            state=AssemblyState.APPROVED,
        )
        assert (review.state, review.draft_revision) == (AssemblyState.REVIEW, 1)
        assert (approved.state, approved.draft_revision) == (
            AssemblyState.APPROVED,
            3,
        )

    def test_invalid_transition_and_published_draft_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="invalid assembly transition"):
            draft().transition(expected_revision=0, state=AssemblyState.APPROVED)
        with pytest.raises(ValidationError, match="SemanticModelBundle"):
            draft(state=AssemblyState.PUBLISHED)


class TestFileVersion:
    def test_canonical_round_trip_uses_required_api_version(self) -> None:
        original = draft()
        result = YamlAssemblyLoader().load(original.serialize_canonical())
        assert result.loaded
        assert result.draft == original
        assert '"apiVersion"' in original.serialize_canonical()

    @pytest.mark.parametrize(
        "payload",
        ["draft_id: draft-1", "apiVersion: nl2data.io/semantic-assembly/v9"],
    )
    def test_missing_or_unknown_api_version_fails_closed(self, payload: str) -> None:
        result = YamlAssemblyLoader().load(payload)
        assert result.kind == "incompatible_schema"
        assert result.issue_codes() == ["incompatible_schema"]


class TestAssertionIdentity:
    def test_mapping_payload_change_keeps_identity_but_changes_hash(self) -> None:
        first = SemanticAssertion.create(
            type=AssertionType.MAPPING,
            payload={
                "descriptor_id": "sales",
                "entity_id": "orders",
                "field_id": "status",
                "value_mapping": {"new": 1},
            },
            provenance=provenance(),
        )
        changed = first.replace_payload(
            {**dict(first.payload), "value_mapping": {"new": 2}}
        )
        assert changed.id == first.id
        assert changed.payload_hash() != first.payload_hash()

    def test_relationship_join_key_change_is_delete_add_identity(self) -> None:
        payload = {
            "descriptor_id": "sales",
            "relationship_id": "orders_customers",
            "source_entity_id": "orders",
            "target_entity_id": "customers",
            "source_fields": ["customer_id"],
            "target_fields": ["id"],
            "label": "Customer",
        }
        first = SemanticAssertion.create(
            type=AssertionType.RELATIONSHIP,
            payload=payload,
            provenance=provenance(),
        )
        changed = first.replace_payload({**payload, "source_fields": ["account_id"]})
        assert changed.id != first.id

    def test_calculated_field_definition_change_invalidates_review_not_id(self) -> None:
        payload = {
            "descriptor_id": "sales",
            "entity_id": "orders",
            "name": "net_amount",
            "label": "Net amount",
            "description": "",
            "expression": {"op": "field", "field_id": "amount"},
            "output_type": "float",
            "requires": ["amount"],
            "zero_division_policy": "null",
        }
        approved = SemanticAssertion.create(
            type=AssertionType.CALCULATED_FIELD,
            payload=payload,
            provenance=provenance(),
        ).bind_review(
            state=ReviewState.APPROVED,
            reviewer_reference="reviewer-1",
        )
        changed = approved.replace_payload(
            {**payload, "zero_division_policy": "error"}
        )
        assert changed.id == approved.id
        assert changed.review_state is ReviewState.PENDING
        assert changed.review_binding is None

    def test_reviewer_provenance_and_deployment_metadata_are_non_semantic(
        self,
    ) -> None:
        assertion = entity_assertion()
        first = assertion.bind_review(
            state=ReviewState.APPROVED,
            reviewer_reference="reviewer-1",
        )
        second = SemanticAssertion.create(
            type=assertion.type,
            payload=assertion.payload,
            provenance=AssertionProvenance(
                kind=AssertionProvenanceKind.DISCOVERED,
                source_reference="catalog-observation",
            ),
        ).bind_review(
            state=ReviewState.APPROVED,
            reviewer_reference="reviewer-2",
        )
        first_draft = draft(
            assertions=(first,),
            deployment_bindings=(
                DeploymentBinding(
                    binding_id="dev",
                    environment="dev",
                    source_id="sales",
                    connection_reference="env:SALES_DSN",
                ),
            ),
        )
        second_draft = draft(
            assertions=(second,),
            deployment_bindings=(
                DeploymentBinding(
                    binding_id="prod",
                    environment="prod",
                    source_id="sales",
                    connection_reference="vault:secret/data/sales",
                ),
            ),
        )

        def semantic_fingerprint(value: AssemblyDraft) -> str:
            approved = [
                item.canonical_payload()
                for item in value.assertions
                if item.review_state is ReviewState.APPROVED
            ]
            return sha256_fingerprint({"assertions": approved})

        assert semantic_fingerprint(first_draft) == semantic_fingerprint(second_draft)


class TestDraftRevision:
    def test_mutation_advances_revision_and_stale_write_conflicts(self) -> None:
        original = draft()
        changed = original.mutate(
            expected_revision=0,
            author_reference="team-semantic",
        )
        assert original.draft_revision == 0
        assert changed.draft_revision == 1
        with pytest.raises(DraftRevisionConflict) as captured:
            changed.mutate(expected_revision=0, author_reference="stale-team")
        assert captured.value.expected == 0
        assert captured.value.actual == 1

    def test_generic_mutation_cannot_skip_lifecycle_states(self) -> None:
        with pytest.raises(ValueError, match="protected fields: state"):
            draft().mutate(expected_revision=0, state=AssemblyState.APPROVED)