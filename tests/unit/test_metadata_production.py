"""Unit tests for the production metadata discovery profile.

Covers the production discovery policy (authorization, allowlists, bounds,
sampling, timeout, concurrency, statistics), host-owned snapshot lifecycle
(partial/complete/freshness/retention/activation), drift severity
classification, activation blocking with bounded overrides, tenant scoping,
and safe error normalization - all without real services.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from nl2data_core.metadata import (
    DiscoveryAuthorization,
    DiscoveryHealthEvidence,
    DiscoveryOutcome,
    DiscoveryOutcomeCategory,
    DriftDecision,
    DriftOverride,
    DriftReason,
    DriftSeverity,
    MetadataBoundsExceededError,
    MetadataDiscoveryConfig,
    MetadataDiscoveryError,
    MetadataEvidence,
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataRelationship,
    MetadataRelationshipKind,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataTrustLevel,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
    ProductionActivationContext,
    ProductionDiscoveryConfig,
    SnapshotActivationPolicy,
    SnapshotLedger,
    SnapshotLifecycleState,
    check_snapshot_activation,
    classify_drift,
    discovery_health,
    run_production_discovery,
)

_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def make_object(object_id: str, *, field_ids: tuple[str, ...]) -> MetadataObject:
    """A table object whose fields carry only the given ids."""
    return MetadataObject(
        object_id=object_id,
        kind=MetadataObjectKind.TABLE,
        name=object_id,
        fields=tuple(
            MetadataField(
                field_id=field_id,
                object_id=object_id,
                path=field_id,
                data_type=(
                    "INTEGER" if field_id.endswith("_id") or field_id == "amount" else "TEXT"
                ),
                nullable=field_id != "order_id",
                trust_level=MetadataTrustLevel.DECLARED,
            )
            for field_id in field_ids
        ),
    )


def make_snapshot(
    *, source_id: str = "sales", catalog: str = "ab", discovered_at: datetime | None = None,
    **overrides,
) -> MetadataSnapshot:
    """A complete, fresh, structurally valid snapshot for policy tests."""
    values = {
        "snapshot_id": "snap-1",
        "source": MetadataSourceReference(
            source_id=source_id,
            catalog_fingerprint=fp(catalog),
            description="Logical sales source",
        ),
        "objects": (
            make_object("orders", field_ids=("order_id", "amount", "region")),
        ),
        "relationships": (
            MetadataRelationship(
                relationship_id="orders_customers_via_customer_id",
                kind=MetadataRelationshipKind.FOREIGN_KEY,
                source_object_id="orders",
                target_object_id="customers",
                source_fields=frozenset({"customer_id"}),
                target_fields=frozenset({"id"}),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "freshness": MetadataFreshness(
            bounded_objects=False,
            bounded_fields=False,
            bounded_samples=False,
            sample_limit=10,
            discovered_at=(
                discovered_at if discovered_at is not None else datetime(2026, 1, 1, tzinfo=UTC)
            ),
        ),
        "provenance": MetadataProvenance(
            discovered_by_fingerprint=fp("11"),
            method="test",
            evidence=(
                MetadataEvidence(
                    evidence_id="ev-1",
                    kind="object",
                    reference=fp("22"),
                    description="observation",
                ),
            ),
        ),
    }
    values.update(overrides)
    return MetadataSnapshot(**values)


def make_authorization(*, source_id: str = "sales", tenant: str = "11") -> DiscoveryAuthorization:
    return DiscoveryAuthorization(
        source_id=source_id,
        tenant_scope_fingerprint=fp(tenant),
        discovery_identity_fingerprint=fp("33"),
    )


def make_config(**overrides) -> ProductionDiscoveryConfig:
    values = {
        "authorization": make_authorization(),
        "bounds": MetadataDiscoveryConfig(
            allowed_objects=frozenset({"orders"}),
            allowed_fields=frozenset({"order_id", "amount", "region"}),
            include_statistics=True,
        ),
        "sensitive_name_markers": frozenset({"amount"}),
    }
    values.update(overrides)
    return ProductionDiscoveryConfig(**values)


def make_policy(snapshot: MetadataSnapshot, **overrides) -> SnapshotActivationPolicy:
    values = {
        "max_age_seconds": None,
        "allow_partial": False,
        "compatible_catalog_fingerprints": frozenset({snapshot.source.catalog_fingerprint}),
        "tenant_scope_fingerprint": fp("11"),
        "source_id": "sales",
    }
    values.update(overrides)
    return SnapshotActivationPolicy(**values)


def make_drift_reason(code: str, member_id: str | None = None) -> DriftReason:
    return DriftReason(code=code, member_id=member_id)


def make_decision(
    *, severity: DriftSeverity, reasons: tuple[DriftReason, ...] = (),
    comparison: str = "ab",
) -> DriftDecision:
    return DriftDecision(
        severity=severity,
        blocking_reasons=reasons,
        comparison_fingerprint=fp(comparison),
    )


def make_override(decision: DriftDecision, *, tenant: str = "11", source_id: str = "sales",
                  expires_at: datetime | None = None) -> DriftOverride:
    return DriftOverride(
        override_id="override-1",
        tenant_scope_fingerprint=fp(tenant),
        source_id=source_id,
        decision_fingerprint=decision.decision_fingerprint,
        reason="reviewed and accepted after schema review",
        approved_by_fingerprint=fp("44"),
        expires_at=expires_at,
    )


class StubDiscoverer:
    """A scriptable protocol-compatible discoverer for normalization tests."""

    def __init__(self, result: MetadataSnapshot | Exception) -> None:
        self._result = result

    def capability(self):
        return None

    async def discover(self, config: MetadataDiscoveryConfig) -> MetadataSnapshot:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class TestProductionDiscoveryPolicy:
    def test_authorization_is_required(self) -> None:
        with pytest.raises(ValidationError):
            ProductionDiscoveryConfig(bounds=MetadataDiscoveryConfig())

    def test_authorization_identifiers_and_fingerprints_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_authorization(source_id="bad source!")
        with pytest.raises(ValidationError):
            make_authorization(tenant="not-a-fingerprint")
        with pytest.raises(ValidationError):
            DiscoveryAuthorization(
                source_id="sales",
                tenant_scope_fingerprint=fp("11"),
                discovery_identity_fingerprint="x",
            )

    def test_config_is_frozen_and_rejects_extra_fields(self) -> None:
        config = make_config()
        with pytest.raises(ValidationError):
            config.bounds = MetadataDiscoveryConfig()  # type: ignore[misc]
        with pytest.raises(ValidationError):
            make_config(unknown="nope")

    def test_sensitive_markers_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            make_config(sensitive_name_markers=frozenset({""}))
        with pytest.raises(ValidationError):
            make_config(sensitive_name_markers=frozenset({"x" * 65}))

    def test_bounds_compose_timeout_concurrency_statistics(self) -> None:
        config = make_config(
            bounds=MetadataDiscoveryConfig(
                allowed_objects=frozenset({"orders"}),
                max_objects=16,
                max_fields_per_object=64,
                max_samples=5,
                max_statistics=32,
                timeout_seconds=12.5,
                max_concurrency=4,
                include_statistics=False,
            )
        )
        assert config.bounds.max_objects == 16
        assert config.bounds.max_samples == 5
        assert config.bounds.timeout_seconds == 12.5
        assert config.bounds.max_concurrency == 4
        assert config.bounds.include_statistics is False
        assert _FINGERPRINT.fullmatch(config.fingerprint()) is not None
        with pytest.raises(ValidationError):
            make_config(bounds=MetadataDiscoveryConfig(max_concurrency=99))

    def test_policy_fingerprint_is_canonical(self) -> None:
        snapshot = make_snapshot()
        policy = make_policy(snapshot)
        assert policy.fingerprint() == policy.fingerprint()
        assert _FINGERPRINT.fullmatch(policy.fingerprint()) is not None
        with pytest.raises(ValidationError):
            SnapshotActivationPolicy(
                compatible_catalog_fingerprints=frozenset({"not-a-fingerprint"})
            )
        with pytest.raises(ValidationError):
            SnapshotActivationPolicy(max_age_seconds=0.0)


class TestDiscoveryOutcomeEvidence:
    def test_success_means_complete_or_bounded_partial(self) -> None:
        succeeded = DiscoveryOutcome(
            outcome=DiscoveryOutcomeCategory.SUCCEEDED,
            object_count=1,
            field_count=3,
            statistic_count=0,
            duration_seconds=0.5,
        )
        partial = DiscoveryOutcome(
            outcome=DiscoveryOutcomeCategory.PARTIAL,
            object_count=1,
            field_count=2,
            statistic_count=0,
            duration_seconds=0.5,
            bounded_fields=True,
        )
        assert succeeded.success is True
        assert partial.success is True
        denied = DiscoveryOutcome(
            outcome=DiscoveryOutcomeCategory.UNAUTHORIZED,
            object_count=0,
            field_count=0,
            statistic_count=0,
            duration_seconds=0.1,
            error_category="unauthorized",
        )
        assert denied.success is False

    def test_counts_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            DiscoveryOutcome(
                outcome=DiscoveryOutcomeCategory.SUCCEEDED,
                object_count=2_000,
                field_count=0,
                statistic_count=0,
                duration_seconds=0.1,
            )
        with pytest.raises(ValidationError):
            DiscoveryOutcome(
                outcome=DiscoveryOutcomeCategory.SUCCEEDED,
                object_count=0,
                field_count=0,
                statistic_count=0,
                duration_seconds=9_999.0,
            )

    def test_error_categories_are_bounded_codes(self) -> None:
        with pytest.raises(ValidationError):
            DiscoveryOutcome(
                outcome=DiscoveryOutcomeCategory.FAILED,
                object_count=0,
                field_count=0,
                statistic_count=0,
                duration_seconds=0.1,
                error_category="psycopg2.OperationalError: connection refused",
            )

    def test_failure_outcome_carries_zero_counts(self) -> None:
        outcome = DiscoveryOutcome(
            outcome=DiscoveryOutcomeCategory.UNAVAILABLE,
            object_count=0,
            field_count=0,
            statistic_count=0,
            duration_seconds=0.1,
            error_category="unavailable",
        )
        assert outcome.object_count == 0
        assert outcome.field_count == 0
        assert outcome.statistic_count == 0
        assert outcome.error_category == "unavailable"

    def test_safe_payload_never_carries_free_text_or_names(self) -> None:
        outcome = DiscoveryOutcome(
            outcome=DiscoveryOutcomeCategory.SUCCEEDED,
            object_count=1,
            field_count=3,
            statistic_count=1,
            duration_seconds=0.5,
            snapshot_fingerprint=fp("55"),
            redacted_sensitive_objects=1,
            redacted_sensitive_fields=2,
        )
        payload = json.dumps(outcome.safe_payload())
        for material in ("orders", "order_id", "amount", "sales.db", "Traceback"):
            assert material not in payload
        assert outcome.snapshot_fingerprint in payload

    def test_health_evidence_fields_are_bounded(self) -> None:
        health = DiscoveryHealthEvidence(
            source_id="sales",
            tenant_scope_fingerprint=fp("11"),
            healthy=True,
            last_outcome=DiscoveryOutcomeCategory.SUCCEEDED,
            last_object_count=1,
            last_field_count=3,
            last_duration_seconds=0.4,
            snapshot_fingerprint=fp("55"),
        )
        assert health.healthy is True
        assert health.snapshot_fingerprint == fp("55")
        with pytest.raises(ValidationError):
            DiscoveryHealthEvidence(
                source_id="sales",
                tenant_scope_fingerprint=fp("11"),
                healthy=True,
                last_outcome=DiscoveryOutcomeCategory.SUCCEEDED,
                last_object_count=9_999,
                last_field_count=0,
                last_duration_seconds=0.1,
            )


class TestActivationPolicy:
    def test_no_snapshot_fails_closed(self) -> None:
        check = check_snapshot_activation(None, SnapshotActivationPolicy())
        assert check.allowed is False
        assert check.issue_codes() == ["snapshot_unavailable"]

    def test_tenant_scope_mismatch_is_unauthorized(self) -> None:
        snapshot = make_snapshot()
        policy = make_policy(snapshot)
        check = check_snapshot_activation(
            snapshot, policy, tenant_scope_fingerprint=fp("99")
        )
        assert check.allowed is False
        assert "snapshot_unauthorized" in check.issue_codes()

    def test_missing_tenant_scope_is_unauthorized(self) -> None:
        snapshot = make_snapshot()
        check = check_snapshot_activation(
            snapshot, make_policy(snapshot), tenant_scope_fingerprint=None
        )
        assert "snapshot_unauthorized" in check.issue_codes()

    def test_source_identity_mismatch(self) -> None:
        snapshot = make_snapshot(source_id="warehouse")
        check = check_snapshot_activation(snapshot, make_policy(snapshot))
        assert "source_changed" in check.issue_codes()

    def test_catalog_incompatible(self) -> None:
        snapshot = make_snapshot(catalog="cd")
        policy = make_policy(snapshot, compatible_catalog_fingerprints=frozenset({fp("ab")}))
        check = check_snapshot_activation(snapshot, policy)
        assert "catalog_incompatible" in check.issue_codes()

    def test_partial_snapshot_blocks_by_default_and_allow_partial_permits(self) -> None:
        snapshot = make_snapshot(
            freshness=MetadataFreshness(
                bounded_objects=True,
                bounded_fields=False,
                bounded_samples=False,
                sample_limit=10,
                discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        check = check_snapshot_activation(
            snapshot, make_policy(snapshot), tenant_scope_fingerprint=fp("11")
        )
        assert check.allowed is False
        assert "snapshot_partial" in check.issue_codes()

        permitted = check_snapshot_activation(
            snapshot,
            make_policy(snapshot, allow_partial=True),
            tenant_scope_fingerprint=fp("11"),
        )
        assert permitted.allowed is True

    def test_stale_freshness_blocks(self) -> None:
        snapshot = make_snapshot(
            discovered_at=datetime(2020, 1, 1, tzinfo=UTC)
        )
        check = check_snapshot_activation(
            snapshot,
            make_policy(snapshot, max_age_seconds=3600.0),
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert "snapshot_stale" in check.issue_codes()

    def test_observed_incomplete_object_counts_as_partial(self) -> None:
        snapshot = make_snapshot(
            objects=(
                make_object("orders", field_ids=("order_id",)).model_copy(
                    update={"observed_incomplete": True}
                ),
            )
        )
        check = check_snapshot_activation(snapshot, make_policy(snapshot))
        assert "snapshot_partial" in check.issue_codes()

    def test_clean_snapshot_activates(self) -> None:
        snapshot = make_snapshot()
        check = check_snapshot_activation(
            snapshot, make_policy(snapshot), tenant_scope_fingerprint=fp("11")
        )
        assert check.allowed is True
        assert check.issues == ()

    def test_activation_context_check_wires_policy_and_scope(self) -> None:
        snapshot = make_snapshot()
        context = ProductionActivationContext(
            snapshot_policy=make_policy(snapshot),
            active_snapshot=snapshot,
            tenant_scope_fingerprint=fp("99"),
        )
        check = context.check()
        assert check.allowed is False
        assert "snapshot_unauthorized" in check.issue_codes()
        assert context.safe_payload()["active_snapshot"] == snapshot.fingerprint


class TestDriftSeverity:
    def test_no_changes_is_informational(self) -> None:
        before = make_snapshot()
        after = make_snapshot(snapshot_id="snap-2")
        decision = classify_drift(
            before,
            after,
            referenced_objects=frozenset({"orders"}),
            referenced_fields=frozenset({"order_id", "amount"}),
        )
        assert decision.severity is DriftSeverity.INFORMATIONAL
        assert decision.blocking is False
        assert decision.blocking_reasons == ()

    def test_additions_are_informational(self) -> None:
        before = make_snapshot(
            objects=(make_object("orders", field_ids=("order_id", "amount")),)
        )
        after = make_snapshot(
            snapshot_id="snap-2",
            objects=(make_object("orders", field_ids=("order_id", "amount", "region")),),
        )
        decision = classify_drift(before, after)
        assert decision.severity is DriftSeverity.INFORMATIONAL
        assert any(
            change.kind == "field_added" and change.field_id == "region"
            for change in decision.informational_changes
        )

    def test_unreferenced_removal_is_warning(self) -> None:
        before = make_snapshot(
            objects=(
                make_object("orders", field_ids=("order_id", "amount")),
                make_object("archive", field_ids=("id",)),
            )
        )
        after = make_snapshot(snapshot_id="snap-2")
        decision = classify_drift(
            before, after, referenced_objects=frozenset({"orders"})
        )
        assert decision.severity is DriftSeverity.WARNING
        assert decision.blocking is False
        assert any(
            change.kind == "object_removed" and change.object_id == "archive"
            for change in decision.warning_changes
        )

    def test_referenced_object_removal_is_blocking(self) -> None:
        before = make_snapshot(
            objects=(
                make_object("orders", field_ids=("order_id", "amount")),
                make_object("customers", field_ids=("id",)),
            )
        )
        after = make_snapshot(snapshot_id="snap-2")
        decision = classify_drift(
            before,
            after,
            referenced_objects=frozenset({"orders", "customers"}),
            referenced_fields=frozenset({"order_id"}),
        )
        assert decision.severity is DriftSeverity.BLOCKING
        assert any(
            reason.code == "referenced_object_removed" and reason.member_id == "customers"
            for reason in decision.blocking_reasons
        )

    def test_referenced_field_removal_is_blocking(self) -> None:
        before = make_snapshot()
        after = make_snapshot(
            snapshot_id="snap-2",
            objects=(make_object("orders", field_ids=("order_id", "region")),),
        )
        decision = classify_drift(
            before, after, referenced_fields=frozenset({"amount"})
        )
        assert decision.severity is DriftSeverity.BLOCKING
        assert any(
            reason.code == "referenced_field_removed"
            for reason in decision.blocking_reasons
        )

    def test_referenced_type_change_is_blocking_unreferenced_is_warning(self) -> None:
        before = make_snapshot()
        after = make_snapshot(
            snapshot_id="snap-2",
            objects=(
                before.objects[0].model_copy(
                    update={
                        "fields": tuple(
                            field.model_copy(update={"data_type": "TEXT"})
                            if field.field_id == "amount"
                            else field
                            for field in before.objects[0].fields
                        )
                    }
                ),
            ),
        )
        blocking = classify_drift(
            before, after, referenced_fields=frozenset({"amount"})
        )
        assert blocking.severity is DriftSeverity.BLOCKING
        assert any(
            reason.code == "referenced_type_changed" for reason in blocking.blocking_reasons
        )
        warning = classify_drift(before, after)
        assert warning.severity is DriftSeverity.WARNING
        assert any(
            change.kind == "field_type_changed" for change in warning.warning_changes
        )

    def test_source_identity_and_catalog_changes_are_blocking(self) -> None:
        before = make_snapshot()
        after = make_snapshot(snapshot_id="snap-2", source_id="warehouse")
        decision = classify_drift(before, after)
        assert decision.severity is DriftSeverity.BLOCKING
        assert any(
            reason.code == "source_identity_changed" for reason in decision.blocking_reasons
        )

        after_catalog = make_snapshot(snapshot_id="snap-2", catalog="cd")
        decision = classify_drift(before, after_catalog)
        assert decision.severity is DriftSeverity.BLOCKING
        assert any(
            reason.code == "catalog_changed" for reason in decision.blocking_reasons
        )

    def test_freshness_expiry_is_blocking(self) -> None:
        before = make_snapshot()
        after = make_snapshot(
            snapshot_id="snap-2",
            discovered_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
        decision = classify_drift(
            before,
            after,
            max_age_seconds=3600.0,
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert decision.severity is DriftSeverity.BLOCKING
        assert any(
            reason.code == "freshness_expired" for reason in decision.blocking_reasons
        )

    def test_decision_fingerprint_is_canonical_and_blocking_requires_reasons(self) -> None:
        decision = make_decision(severity=DriftSeverity.BLOCKING, reasons=(make_drift_reason("x"),))
        assert decision.decision_fingerprint == decision.decision_fingerprint
        assert _FINGERPRINT.fullmatch(decision.decision_fingerprint) is not None
        with pytest.raises(ValidationError):
            make_decision(severity=DriftSeverity.BLOCKING)
        with pytest.raises(ValidationError):
            DriftDecision(
                severity=DriftSeverity.BLOCKING,
                blocking_reasons=(DriftReason(code="x" * 65),),
                comparison_fingerprint=fp("ab"),
            )

    def test_override_permits_exactly_one_decision_and_expires(self) -> None:
        decision = make_decision(severity=DriftSeverity.BLOCKING, reasons=(make_drift_reason("x"),))
        override = make_override(decision)
        assert override.permits(decision) is True
        assert override.permits(
            make_decision(severity=DriftSeverity.BLOCKING, reasons=(make_drift_reason("y"),))
        ) is False
        expired = make_override(
            decision, expires_at=datetime(2020, 1, 1, tzinfo=UTC)
        )
        assert expired.permits(decision, now=datetime(2026, 1, 1, tzinfo=UTC)) is False

    def test_blocking_drift_requires_matching_override(self) -> None:
        snapshot = make_snapshot()
        before = make_snapshot(
            objects=(
                make_object("orders", field_ids=("order_id", "amount")),
                make_object("customers", field_ids=("id",)),
            )
        )
        decision = classify_drift(
            before,
            snapshot,
            referenced_objects=frozenset({"orders", "customers"}),
            referenced_fields=frozenset({"order_id"}),
        )
        assert decision.severity is DriftSeverity.BLOCKING

        policy = make_policy(snapshot)
        blocked = check_snapshot_activation(
            snapshot,
            policy,
            drift_decision=decision,
            tenant_scope_fingerprint=fp("11"),
        )
        assert "blocking_drift" in blocked.issue_codes()

        wrong_tenant = check_snapshot_activation(
            snapshot,
            policy,
            drift_decision=decision,
            overrides=(make_override(decision, tenant="99"),),
            tenant_scope_fingerprint=fp("11"),
        )
        assert "blocking_drift" in wrong_tenant.issue_codes()

        wrong_source = check_snapshot_activation(
            snapshot,
            policy,
            drift_decision=decision,
            overrides=(make_override(decision, source_id="warehouse"),),
            tenant_scope_fingerprint=fp("11"),
        )
        assert "blocking_drift" in wrong_source.issue_codes()

        permitted = check_snapshot_activation(
            snapshot,
            policy,
            drift_decision=decision,
            overrides=(make_override(decision),),
            tenant_scope_fingerprint=fp("11"),
        )
        assert permitted.allowed is True
        assert permitted.decision_fingerprint == decision.decision_fingerprint


class TestSnapshotLedger:
    def test_register_retains_evidence_but_never_activates(self) -> None:
        snapshot = make_snapshot()
        ledger = SnapshotLedger()
        record = ledger.register(snapshot, tenant_scope_fingerprint=fp("11"))
        assert record.state is SnapshotLifecycleState.INACTIVE
        assert ledger.active("sales", fp("11")) is None
        assert record.observed_incomplete is False

    def test_activate_unknown_snapshot(self) -> None:
        ledger = SnapshotLedger()
        activation = ledger.activate(fp("99"), tenant_scope_fingerprint=fp("11"))
        assert activation.activated is False
        assert activation.reason == "snapshot_unknown"

    def test_activate_requires_same_tenant_scope(self) -> None:
        snapshot = make_snapshot()
        ledger = SnapshotLedger()
        ledger.register(snapshot, tenant_scope_fingerprint=fp("11"))
        activation = ledger.activate(
            snapshot.fingerprint, tenant_scope_fingerprint=fp("99")
        )
        assert activation.activated is False
        assert activation.reason == "snapshot_unauthorized"

    def test_expired_retention_blocks_activation_and_cleanup_drops(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        snapshot = make_snapshot()
        ledger = SnapshotLedger(now_fn=lambda: base)
        ledger.register(
            snapshot, tenant_scope_fingerprint=fp("11"), retained_for_seconds=10.0
        )
        later = base + timedelta(seconds=60)
        activation = ledger.activate(
            snapshot.fingerprint, tenant_scope_fingerprint=fp("11"), now=later
        )
        assert activation.activated is False
        assert activation.reason == "snapshot_expired"
        assert ledger.cleanup_expired(now=later) == 1
        assert ledger.records() == ()

    def test_partial_snapshot_activates_only_with_explicit_policy(self) -> None:
        snapshot = make_snapshot(
            freshness=MetadataFreshness(
                bounded_objects=True,
                bounded_fields=False,
                bounded_samples=False,
                sample_limit=10,
                discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        ledger = SnapshotLedger()
        ledger.register(snapshot, tenant_scope_fingerprint=fp("11"))
        denied = ledger.activate(snapshot.fingerprint, tenant_scope_fingerprint=fp("11"))
        assert denied.activated is False
        assert denied.reason == "snapshot_partial"

        permitted = ledger.activate(
            snapshot.fingerprint,
            tenant_scope_fingerprint=fp("11"),
            policy=make_policy(snapshot, allow_partial=True),
        )
        assert permitted.activated is True
        assert permitted.record is not None
        assert permitted.record.state is SnapshotLifecycleState.ACTIVE

    def test_activation_records_drift_decision_evidence(self) -> None:
        snapshot = make_snapshot()
        ledger = SnapshotLedger()
        ledger.register(snapshot, tenant_scope_fingerprint=fp("11"))
        decision = make_decision(severity=DriftSeverity.INFORMATIONAL)
        activation = ledger.activate(
            snapshot.fingerprint,
            tenant_scope_fingerprint=fp("11"),
            policy=make_policy(snapshot),
            drift_decision=decision,
        )
        assert activation.activated is True
        assert activation.record is not None
        assert activation.record.activation_evidence == decision.decision_fingerprint
        assert ledger.active("sales", fp("11")) == snapshot

    def test_active_snapshot_is_tenant_scoped(self) -> None:
        snapshot = make_snapshot()
        ledger = SnapshotLedger()
        ledger.register(snapshot, tenant_scope_fingerprint=fp("11"))
        ledger.activate(
            snapshot.fingerprint, tenant_scope_fingerprint=fp("11"), policy=make_policy(snapshot)
        )
        assert ledger.active("sales", fp("11")) is snapshot
        assert ledger.active("sales", fp("22")) is None

    def test_activation_replaces_prior_active_snapshot_for_same_scope(self) -> None:
        first = make_snapshot()
        second = make_snapshot(snapshot_id="snap-2")
        ledger = SnapshotLedger()
        ledger.register(first, tenant_scope_fingerprint=fp("11"))
        ledger.register(second, tenant_scope_fingerprint=fp("11"))
        assert ledger.activate(
            first.fingerprint,
            tenant_scope_fingerprint=fp("11"),
            policy=make_policy(first),
        ).activated
        assert ledger.activate(
            second.fingerprint,
            tenant_scope_fingerprint=fp("11"),
            policy=make_policy(second),
        ).activated
        assert ledger.active("sales", fp("11")) == second
        active = [
            record
            for record in ledger.records()
            if record.state is SnapshotLifecycleState.ACTIVE
        ]
        assert active == [
            next(
                record
                for record in ledger.records()
                if record.snapshot_fingerprint == second.fingerprint
            )
        ]

    def test_failed_outcome_never_replaces_active_snapshot(self) -> None:
        snapshot = make_snapshot()
        ledger = SnapshotLedger()
        ledger.register(snapshot, tenant_scope_fingerprint=fp("11"))
        ledger.activate(
            snapshot.fingerprint, tenant_scope_fingerprint=fp("11"), policy=make_policy(snapshot)
        )
        ledger.record_outcome(
            DiscoveryOutcome(
                outcome=DiscoveryOutcomeCategory.UNAVAILABLE,
                object_count=0,
                field_count=0,
                statistic_count=0,
                duration_seconds=0.1,
                error_category="unavailable",
            ),
            source_id="sales",
            tenant_scope_fingerprint=fp("11"),
        )
        assert ledger.active("sales", fp("11")) is snapshot

    def test_health_requires_success_and_active_snapshot(self) -> None:
        snapshot = make_snapshot()
        ledger = SnapshotLedger()
        health = discovery_health(ledger, source_id="sales", tenant_scope_fingerprint=fp("11"))
        assert health.healthy is False

        ledger.register(snapshot, tenant_scope_fingerprint=fp("11"))
        ledger.activate(
            snapshot.fingerprint, tenant_scope_fingerprint=fp("11"), policy=make_policy(snapshot)
        )
        ledger.record_outcome(
            DiscoveryOutcome(
                outcome=DiscoveryOutcomeCategory.SUCCEEDED,
                object_count=1,
                field_count=3,
                statistic_count=0,
                duration_seconds=0.5,
                snapshot_fingerprint=snapshot.fingerprint,
            ),
            source_id="sales",
            tenant_scope_fingerprint=fp("11"),
        )
        healthy = discovery_health(
            ledger, source_id="sales", tenant_scope_fingerprint=fp("11")
        )
        assert healthy.healthy is True
        assert healthy.snapshot_fingerprint == snapshot.fingerprint
        assert healthy.last_object_count == 1

        ledger.record_outcome(
            DiscoveryOutcome(
                outcome=DiscoveryOutcomeCategory.UNAUTHORIZED,
                object_count=0,
                field_count=0,
                statistic_count=0,
                duration_seconds=0.1,
                error_category="unauthorized",
            ),
            source_id="sales",
            tenant_scope_fingerprint=fp("11"),
        )
        unhealthy = discovery_health(
            ledger, source_id="sales", tenant_scope_fingerprint=fp("11")
        )
        assert unhealthy.healthy is False
        assert unhealthy.last_error_category == "unauthorized"

    def test_cleanup_drops_expired_active_snapshot(self) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        snapshot = make_snapshot()
        ledger = SnapshotLedger(now_fn=lambda: base)
        ledger.register(
            snapshot, tenant_scope_fingerprint=fp("11"), retained_for_seconds=10.0
        )
        ledger.activate(snapshot.fingerprint, tenant_scope_fingerprint=fp("11"))
        later = base + timedelta(seconds=60)
        assert ledger.cleanup_expired(now=later) == 1
        assert ledger.active("sales", fp("11")) is None


class TestErrorNormalization:
    @pytest.mark.asyncio
    async def test_discovery_rejects_a_snapshot_from_another_authorized_source(self) -> None:
        snapshot = make_snapshot(source_id="warehouse")
        result = await run_production_discovery(
            StubDiscoverer(snapshot), make_config()
        )
        assert result.outcome.outcome is DiscoveryOutcomeCategory.UNAUTHORIZED
        assert result.snapshot is None

    async def test_unauthorized_is_normalized_without_driver_text(self) -> None:
        discoverer = StubDiscoverer(
            MetadataUnauthorizedError("no objects are authorized for discovery")
        )
        result = await run_production_discovery(discoverer, make_config())
        assert result.outcome.outcome is DiscoveryOutcomeCategory.UNAUTHORIZED
        assert result.outcome.error_category == "unauthorized"
        assert result.outcome.success is False
        assert result.snapshot is None
        payload = json.dumps(result.outcome.safe_payload())
        for material in ("no objects are authorized", "sqlite", "sales.db", "Traceback"):
            assert material not in payload

    async def test_unavailable_and_bounds_and_failed_categories(self) -> None:
        cases = (
            (
                MetadataUnavailableError("backend unreachable"),
                DiscoveryOutcomeCategory.UNAVAILABLE,
                "unavailable",
            ),
            (
                MetadataBoundsExceededError("allowlist exceeded"),
                DiscoveryOutcomeCategory.BOUNDS_EXCEEDED,
                "bounds_exceeded",
            ),
            (
                MetadataDiscoveryError("malformed payload"),
                DiscoveryOutcomeCategory.FAILED,
                "discovery_failed",
            ),
        )
        for error, category, code in cases:
            result = await run_production_discovery(StubDiscoverer(error), make_config())
            assert result.outcome.outcome is category
            assert result.outcome.error_category == code
            assert result.outcome.object_count == 0
            assert result.outcome.field_count == 0
            assert result.snapshot is None

    async def test_partial_snapshot_is_reported_and_never_activated(self) -> None:
        snapshot = make_snapshot(
            freshness=MetadataFreshness(
                bounded_objects=True,
                bounded_fields=True,
                bounded_samples=False,
                sample_limit=5,
                discovered_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        result = await run_production_discovery(StubDiscoverer(snapshot), make_config())
        assert result.outcome.outcome is DiscoveryOutcomeCategory.PARTIAL
        assert result.outcome.bounded_objects is True
        assert result.outcome.bounded_fields is True
        assert result.snapshot is snapshot

    async def test_sensitive_members_are_counted_but_never_named(self) -> None:
        snapshot = make_snapshot(
            objects=(
                make_object("orders", field_ids=("order_id", "amount", "salary")),
            )
        )
        result = await run_production_discovery(
            StubDiscoverer(snapshot),
            make_config(sensitive_name_markers=frozenset({"amount", "salary"})),
        )
        assert result.outcome.outcome is DiscoveryOutcomeCategory.SUCCEEDED
        assert result.outcome.redacted_sensitive_fields == 2
        payload = json.dumps(result.outcome.safe_payload())
        for name in ("orders", "order_id", "amount", "salary"):
            assert name not in payload
