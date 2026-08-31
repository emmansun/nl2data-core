"""Security tests for the PostgreSQL semantic catalog adapter.

Proves the catalog's adversarial guarantees over the deterministic fake
pool: cross-tenant isolation with no existence oracle, safe envelope
rejection of tampered/foreign rows, secret and DSN exclusion from every
serialized surface, fingerprint mismatch rejection, and fail-closed
behavior against newer database schema versions.  Every assertion checks
that the failure surfaces as a normalized, redacted catalog error - never
backend text, payload content, or connection material.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    DeploymentBinding,
)
from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.canonical import sha256_fingerprint
from nl2data_core.metadata import (
    MetadataEvidence,
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataTrustLevel,
)
from nl2data_core.metadata.inference import infer_proposals
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)
from nl2data_core.workflow.durable import tenant_scope_namespace
from nl2data_semantic_catalog_postgres.config import SemanticCatalogConfig
from nl2data_semantic_catalog_postgres.envelope import (
    ENVELOPE_SCHEMA_VERSION,
    ArtifactKind,
    EnvelopeRejectedError,
    encode_envelope,
)
from nl2data_semantic_catalog_postgres.errors import (
    SemanticCatalogError,
    SemanticCatalogErrorCode,
)
from nl2data_semantic_catalog_postgres.fake_postgres import (
    FakePostgresPool,
    OperationalError,
    TimeoutError,
)
from nl2data_semantic_catalog_postgres.schema import SUPPORTED_SCHEMA_VERSION
from nl2data_semantic_catalog_postgres.store import PostgreSQLSemanticCatalog

TENANT_A = "sha256:" + "a" * 64
TENANT_B = "sha256:" + "b" * 64

#: Serialization surfaces that must never carry backend or secret material.
_FORBIDDEN_TEXT = (
    "localhost",
    "5432",
    "postgresql://",
    "hunter2",
    "password",
    "backend down",
)


def fp(byte: str) -> str:
    """A valid ``sha256:<hex>`` fingerprint filled with one repeated byte."""
    return "sha256:" + byte * 32


def make_snapshot(**overrides: object) -> MetadataSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "snap-1",
        "source": MetadataSourceReference(
            source_id="sales",
            catalog_fingerprint=fp("ab"),
            description="Logical sales source",
        ),
        "objects": (
            MetadataObject(
                object_id="orders",
                kind=MetadataObjectKind.TABLE,
                name="orders",
                fields=(
                    MetadataField(
                        field_id="order_id",
                        object_id="orders",
                        path="order_id",
                        data_type="INTEGER",
                        nullable=False,
                        trust_level=MetadataTrustLevel.DECLARED,
                    ),
                ),
                trust_level=MetadataTrustLevel.DECLARED,
            ),
        ),
        "freshness": MetadataFreshness(
            bounded_objects=False, bounded_fields=False, sample_limit=10
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
    return MetadataSnapshot(**values)  # type: ignore[arg-type]


def make_field(field_id: str = "amount", **overrides: object) -> SemanticFieldDescriptor:
    values: dict[str, object] = {
        "field_id": field_id,
        "label": field_id.replace("_", " ").title(),
        "description": f"Semantic field {field_id}",
        "data_type": "decimal" if field_id == "amount" else "string",
    }
    values.update(overrides)
    return SemanticFieldDescriptor(**values)  # type: ignore[arg-type]


def make_descriptor(**overrides: object) -> SemanticDescriptor:
    values: dict[str, object] = {
        "descriptor_id": "sales_catalog",
        "version": 1,
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "entities": (
            SemanticEntityDescriptor(
                entity_id="order",
                label="Order",
                fields=(
                    make_field("order_id", data_type="string"),
                    make_field("amount"),
                ),
            ),
        ),
    }
    values.update(overrides)
    return SemanticDescriptor(**values)  # type: ignore[arg-type]


def make_source(**overrides: object) -> SemanticSourceReference:
    values: dict[str, object] = {
        "reference_id": "src-sales",
        "source_id": "sales",
        "catalog_fingerprint": fp("ab"),
        "description": "Logical sales source reference",
    }
    values.update(overrides)
    return SemanticSourceReference(**values)  # type: ignore[arg-type]


def make_bundle(**overrides: object) -> SemanticModelBundle:
    values: dict[str, object] = {
        "bundle_id": "sales_model",
        "model_version": "1.0.0",
        "descriptor": make_descriptor(),
        "sources": (make_source(),),
        "provenance": BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.VALIDATED,
        ),
    }
    values.update(overrides)
    return SemanticModelBundle(**values)  # type: ignore[arg-type]


def make_postgres_catalog() -> tuple[PostgreSQLSemanticCatalog, FakePostgresPool]:
    pool = FakePostgresPool()
    catalog = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
    return catalog, pool


def _tamper_snapshot_envelope(
    pool: FakePostgresPool, snapshot: MetadataSnapshot, envelope: str
) -> None:
    """Replace the persisted envelope of one snapshot row in place."""
    pool.snapshots[(tenant_scope_namespace(TENANT_A), snapshot.fingerprint)][
        "envelope"
    ] = envelope


def _assert_no_forbidden_text(*surfaces: str) -> None:
    for surface in surfaces:
        for fragment in _FORBIDDEN_TEXT:
            assert fragment not in surface


class TestTenantIsolation:
    def test_cross_scope_reads_and_activation_fail_closed(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        assert catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_B) is None
        assert catalog.active_snapshot("sales", TENANT_B) is None
        foreign = catalog.activate_snapshot(
            snapshot.fingerprint, tenant_scope_fingerprint=TENANT_B
        )
        assert not foreign.activated
        assert foreign.reason == "snapshot_unknown"
        #: The tenant-A record is untouched by the failed cross-scope attempt.
        assert catalog.active_snapshot("sales", TENANT_A) is None
        assert (
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
            is not None
        )

    def test_proposal_sets_are_bound_to_the_registered_scope(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        proposals = infer_proposals(snapshot)
        catalog.save_proposal_set(proposals, tenant_scope_fingerprint=TENANT_A)
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.save_proposal_set(proposals, tenant_scope_fingerprint=TENANT_B)
        assert excinfo.value.code is SemanticCatalogErrorCode.UNAUTHORIZED
        #: The tenant-A proposal set is untouched by the failed cross-scope save.
        assert (
            catalog.proposal_set(
                snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A
            )
            is not None
        )

    def test_bundles_are_scoped_per_tenant(self) -> None:
        catalog, _ = make_postgres_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle, tenant_scope_fingerprint=TENANT_A).success
        assert (
            catalog.get(
                "sales_model", "1.0.0", tenant_scope_fingerprint=TENANT_B
            )
            is None
        )
        assert catalog.versions("sales_model", tenant_scope_fingerprint=TENANT_B) == ()
        outcome = catalog.activate(
            "sales_model", "1.0.0", tenant_scope_fingerprint=TENANT_B
        )
        assert outcome.kind == "not_found"
        assert catalog.active("sales_model", tenant_scope_fingerprint=TENANT_B) is None
        #: The tenant-A publication and pointer remain intact.
        assert (
            catalog.get(
                "sales_model", "1.0.0", tenant_scope_fingerprint=TENANT_A
            )
            == bundle
        )

    def test_unknown_scope_activation_never_acts_as_an_existence_oracle(self) -> None:
        catalog, _ = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        #: The same fail-closed reason is returned whether or not the
        #: fingerprint exists in any scope.
        for scope, fingerprint in (
            (TENANT_B, snapshot.fingerprint),
            (TENANT_A, fp("ff")),
            (TENANT_B, fp("ff")),
        ):
            activation = catalog.activate_snapshot(
                fingerprint, tenant_scope_fingerprint=scope
            )
            assert not activation.activated
            assert activation.reason == "snapshot_unknown"


class TestSafeEnvelopeRejection:
    def test_snapshot_envelope_tampering_fails_closed_after_reconstruction(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        key = (tenant_scope_namespace(TENANT_A), snapshot.fingerprint)
        raw = json.loads(pool.snapshots[key]["envelope"])
        raw["payload"]["freshness"]["sample_limit"] = 99
        pool.snapshots[key]["envelope"] = json.dumps(raw)
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        assert excinfo.value.code is SemanticCatalogErrorCode.FINGERPRINT_MISMATCH

    def test_structurally_invalid_but_self_consistent_bundle_fails_on_read(self) -> None:
        catalog, pool = make_postgres_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle).success
        row = pool.publications["", "sales_model", "1.0.0"]
        payload = dict(bundle.canonical_payload())
        payload["sources"] = []
        row["envelope"] = encode_envelope(
            ArtifactKind.BUNDLE,
            payload,
            sha256_fingerprint(payload),
            max_envelope_bytes=1_048_576,
            max_payload_bytes=524_288,
        )
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.get("sales_model", "1.0.0")
        assert excinfo.value.code is SemanticCatalogErrorCode.ENVELOPE_REJECTED

    def test_tampered_payload_fails_closed_on_read(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        key = (tenant_scope_namespace(TENANT_A), snapshot.fingerprint)
        raw = json.loads(pool.snapshots[key]["envelope"])
        raw["payload"]["source"]["description"] = "tampered by an attacker"
        _tamper_snapshot_envelope(pool, snapshot, json.dumps(raw))
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        error = excinfo.value
        #: The read path maps a fingerprint mismatch to its dedicated code.
        assert error.code is SemanticCatalogErrorCode.FINGERPRINT_MISMATCH
        assert error.details.get("reason") == "fingerprint_mismatch"
        assert "tampered" not in str(error)

    def test_wrong_kind_envelope_fails_closed(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        payload = {"bundle_id": "sales_model"}
        foreign = encode_envelope(
            ArtifactKind.BUNDLE,
            payload,
            sha256_fingerprint(payload),
            max_envelope_bytes=1_048_576,
            max_payload_bytes=524_288,
        )
        _tamper_snapshot_envelope(pool, snapshot, foreign)
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        assert excinfo.value.code is SemanticCatalogErrorCode.ENVELOPE_REJECTED
        assert excinfo.value.details.get("reason") == "kind_mismatch"

    def test_malformed_envelope_fails_closed(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        _tamper_snapshot_envelope(pool, snapshot, "not-json-at-all")
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        assert excinfo.value.code is SemanticCatalogErrorCode.ENVELOPE_REJECTED
        assert excinfo.value.details.get("reason") == "malformed"

    def test_newer_envelope_schema_fails_closed(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        payload = snapshot.canonical_payload()
        newer = encode_envelope(
            ArtifactKind.SNAPSHOT,
            payload,
            snapshot.fingerprint,
            max_envelope_bytes=1_048_576,
            max_payload_bytes=524_288,
        )
        raw = json.loads(newer)
        raw["schema_version"] = ENVELOPE_SCHEMA_VERSION + 1
        _tamper_snapshot_envelope(pool, snapshot, json.dumps(raw))
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        #: A newer envelope schema is a schema-level mismatch, not a generic
        #: envelope rejection, and fails closed before any payload is read.
        assert excinfo.value.code is SemanticCatalogErrorCode.SCHEMA_MISMATCH
        assert excinfo.value.details.get("reason") == "newer_schema"

    def test_unknown_kind_envelope_fails_closed(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        raw = json.loads(
            pool.snapshots[(tenant_scope_namespace(TENANT_A), snapshot.fingerprint)][
                "envelope"
            ]
        )
        raw["kind"] = "gadget"
        _tamper_snapshot_envelope(pool, snapshot, json.dumps(raw))
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        assert excinfo.value.code is SemanticCatalogErrorCode.ENVELOPE_REJECTED
        assert excinfo.value.details.get("reason") == "unknown_kind"

    def test_tampered_bundle_envelope_fails_closed_on_read(self) -> None:
        catalog, pool = make_postgres_catalog()
        bundle = make_bundle()
        catalog.publish(bundle)
        #: The default non-tenant scope uses the empty-string namespace.
        row = pool.publications[("", "sales_model", "1.0.0")]
        raw = json.loads(row["envelope"])
        raw["payload"]["descriptor"]["catalog_fingerprint"] = fp("00")
        row["envelope"] = json.dumps(raw)
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.get("sales_model", "1.0.0")
        assert excinfo.value.code is SemanticCatalogErrorCode.FINGERPRINT_MISMATCH
        assert excinfo.value.details.get("reason") == "fingerprint_mismatch"


class TestSecretAndDsnExclusion:
    def test_draft_persistence_keeps_reference_but_not_resolved_credential(self) -> None:
        catalog, pool = make_postgres_catalog()
        draft = AssemblyDraft(
            apiVersion=ASSEMBLY_API_VERSION,
            draft_id="draft-sales",
            bundle_id="sales-model",
            source_id="sales",
            model_version="1.0.0",
            deployment_bindings=(
                DeploymentBinding(
                    binding_id="production",
                    environment="production",
                    source_id="sales",
                    connection_reference="vault:secret/data/sales",
                ),
            ),
            author_reference="author-1",
        )
        catalog.create(draft, tenant_scope_fingerprint=TENANT_A)
        persisted = str(pool.assembly_drafts)
        assert "vault:secret/data/sales" in persisted
        assert "resolved-password-hunter2" not in persisted

    def test_config_never_carries_the_dsn(self) -> None:
        config = SemanticCatalogConfig(
            namespace="deploy_a", dsn_secret_ref="NL2DATA_CATALOG_DSN"
        )
        payload = config.safe_payload()
        assert payload["dsn_secret_ref"] == "NL2DATA_CATALOG_DSN"
        assert "postgresql://" not in str(payload)
        assert "localhost" not in str(payload)

    def test_backend_failures_never_leak_driver_text(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        pool.fail_next(
            OperationalError(
                "connection to localhost:5432 failed: password authentication "
                "failed for user 'hunter2'"
            )
        )
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        error = excinfo.value
        assert error.code is SemanticCatalogErrorCode.CATALOG_UNAVAILABLE
        assert error.retryable
        record = error.to_record().safe_dump()
        _assert_no_forbidden_text(str(error), str(record), str(error.safe_details()))

    def test_timeout_errors_never_leak_statement_text(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        pool.fail_next(TimeoutError("statement_timeout on SELECT * FROM metadata_snapshots"))
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        error = excinfo.value
        assert error.code is SemanticCatalogErrorCode.CATALOG_TIMEOUT
        assert "metadata_snapshots" not in str(error)
        assert "statement_timeout" not in str(error)

    def test_config_dump_and_error_record_are_serializable_and_redacted(self) -> None:
        config = SemanticCatalogConfig(
            namespace="deploy_a", dsn_secret_ref="NL2DATA_CATALOG_DSN"
        )
        dumped = config.model_dump()
        assert dumped["dsn_secret_ref"] == "NL2DATA_CATALOG_DSN"
        assert "dsn" not in {key.lower() for key in dumped if key != "dsn_secret_ref"}
        record = SemanticCatalogError(
            SemanticCatalogErrorCode.CATALOG_UNAVAILABLE, "backend is unreachable"
        ).to_record()
        payload = record.safe_dump()
        assert payload["code"] == "CATALOG_UNAVAILABLE"
        assert "backend" in payload["message"]


class TestFingerprintMismatch:
    def test_encode_rejects_a_fingerprint_that_mismatches_the_payload(self) -> None:
        snapshot = make_snapshot()
        payload = snapshot.canonical_payload()
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            encode_envelope(
                ArtifactKind.SNAPSHOT,
                payload,
                fp("00"),
                max_envelope_bytes=1_048_576,
                max_payload_bytes=524_288,
            )
        assert excinfo.value.code == "fingerprint_mismatch"

    def test_encode_rejects_a_malformed_fingerprint(self) -> None:
        snapshot = make_snapshot()
        with pytest.raises(EnvelopeRejectedError) as excinfo:
            encode_envelope(
                ArtifactKind.SNAPSHOT,
                snapshot.canonical_payload(),
                "not-a-fingerprint",
                max_envelope_bytes=1_048_576,
                max_payload_bytes=524_288,
            )
        assert excinfo.value.code == "fingerprint_mismatch"

    def test_tampered_fingerprint_field_fails_closed_on_read(self) -> None:
        catalog, pool = make_postgres_catalog()
        snapshot = make_snapshot()
        catalog.register_snapshot(snapshot, tenant_scope_fingerprint=TENANT_A)
        raw = json.loads(
            pool.snapshots[(tenant_scope_namespace(TENANT_A), snapshot.fingerprint)][
                "envelope"
            ]
        )
        raw["fingerprint"] = fp("00")
        _tamper_snapshot_envelope(pool, snapshot, json.dumps(raw))
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.snapshot(snapshot.fingerprint, tenant_scope_fingerprint=TENANT_A)
        assert excinfo.value.code is SemanticCatalogErrorCode.FINGERPRINT_MISMATCH
        assert excinfo.value.details.get("reason") == "fingerprint_mismatch"


class TestSchemaVersionFailClosed:
    def test_newer_publication_row_schema_fails_closed_when_listing(self) -> None:
        catalog, pool = make_postgres_catalog()
        bundle = make_bundle()
        assert catalog.publish(bundle).success
        pool.publications["", "sales_model", "1.0.0"]["schema_version"] = (
            ENVELOPE_SCHEMA_VERSION + 1
        )
        with pytest.raises(SemanticCatalogError) as excinfo:
            catalog.versions("sales_model")
        assert excinfo.value.code is SemanticCatalogErrorCode.SCHEMA_MISMATCH

    def test_newer_database_schema_is_rejected_at_construction(self) -> None:
        pool = FakePostgresPool()
        catalog = PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        assert catalog.schema_version() == SUPPORTED_SCHEMA_VERSION
        pool.set_schema_version(SUPPORTED_SCHEMA_VERSION + 1)
        with pytest.raises(SemanticCatalogError) as excinfo:
            PostgreSQLSemanticCatalog(pool=pool, now=pool.clock.now)
        error = excinfo.value
        assert error.code is SemanticCatalogErrorCode.SCHEMA_MISMATCH
        assert not error.retryable
        assert str(SUPPORTED_SCHEMA_VERSION + 1) in str(error)

    def test_config_cannot_request_an_unsupported_schema_version(self) -> None:
        with pytest.raises(ValidationError):
            SemanticCatalogConfig(
                namespace="deploy_a",
                schema_version=SUPPORTED_SCHEMA_VERSION + 1,
            )
