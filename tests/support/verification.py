"""Shared Verification Suite test builders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from nl2data_core.assembly import ASSEMBLY_API_VERSION, AssemblyDraft, AssemblyState
from nl2data_core.assembly.manifest import AcceptedAssertionManifest
from nl2data_core.bundles import (
    BundleProvenance,
    BundleQualityStatus,
    SemanticModelBundle,
    SemanticSourceReference,
)
from nl2data_core.governance.models import PolicyScope
from nl2data_core.planning.ir.models import (
    IRFilter,
    IROrdering,
    IRProvenance,
    IRSelection,
    SemanticQueryIR,
)
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.verification import COMPATIBILITY_POLICY, SemanticContractCase, SmokeQueryCase
from nl2data_core.verification.execution import (
    VerificationExecutionContext,
    VerificationObservation,
)
from nl2data_core.views import (
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
)

SCOPE_FINGERPRINT = "sha256:" + "a" * 64


def candidate_bundle() -> SemanticModelBundle:
    descriptor = SemanticDescriptor(
        descriptor_id="sales",
        version=1,
        source_id="sales",
        entities=(
            SemanticEntityDescriptor(
                entity_id="order",
                label="Order",
                fields=tuple(
                    SemanticFieldDescriptor(
                        field_id=field_id,
                        label=field_id,
                        data_type=data_type,
                    )
                    for field_id, data_type in (
                        ("order_id", "int"),
                        ("amount", "float"),
                        ("region", "str"),
                    )
                ),
            ),
        ),
    )
    return SemanticModelBundle(
        bundle_id="sales",
        model_version="1.0.0",
        descriptor=descriptor,
        sources=(SemanticSourceReference(reference_id="sales", source_id="sales"),),
        provenance=BundleProvenance(
            owner_reference="team-analytics",
            quality=BundleQualityStatus.APPROVED,
        ),
    )


def accepted_manifest(candidate: SemanticModelBundle) -> AcceptedAssertionManifest:
    draft = AssemblyDraft(
        apiVersion=ASSEMBLY_API_VERSION,
        draft_id="draft-execution",
        bundle_id="sales",
        source_id="sales",
        model_version="1.0.0",
        state=AssemblyState.APPROVED,
        author_reference="author-1",
    )
    return AcceptedAssertionManifest.from_draft(
        draft,
        bundle_fingerprint=candidate.fingerprint,
    )


def authorized_view() -> AuthorizedView:
    return AuthorizedView(
        source_id="sales",
        root_entity_ids=frozenset({"order"}),
        field_ids=frozenset({"order_id", "amount", "region"}),
    )


def policy_scope() -> PolicyScope:
    return PolicyScope(
        policy_id="verification-policy",
        source_ids=frozenset({"sales"}),
        resource_ids=frozenset({"orders"}),
        operation_ids=frozenset({"select"}),
        field_ids=authorized_view().field_ids,
    )


def verification_context(**overrides: object) -> VerificationExecutionContext:
    candidate = candidate_bundle()
    values: dict[str, object] = {
        "candidate": candidate,
        "manifest": accepted_manifest(candidate),
        "view": authorized_view(),
        "policy": COMPATIBILITY_POLICY,
        "policy_scope": policy_scope(),
        "tenant_scope_fingerprint": SCOPE_FINGERPRINT,
        "source_scope_fingerprint": SCOPE_FINGERPRINT,
        "deadline_at": datetime.now(UTC) + timedelta(seconds=10),
    }
    values.update(overrides)
    return VerificationExecutionContext(**values)  # type: ignore[arg-type]


def semantic_ir(**overrides: object) -> SemanticQueryIR:
    values: dict[str, object] = {
        "ir_id": "verification-ir",
        "source_id": "sales",
        "root_entity_id": "order",
        "selections": (
            IRSelection(selection_id="order", field_id="order_id", alias="oid"),
            IRSelection(selection_id="amount", field_id="amount", alias="amt"),
        ),
        "filters": (
            IRFilter(
                filter_id="region",
                field_id="region",
                operator="eq",
                value="emea",
            ),
        ),
        "orderings": (
            IROrdering(
                ordering_id="amount",
                field_id="amount",
                direction="desc",
            ),
        ),
        "limit": 3,
        "provenance": IRProvenance(source_id="sales", root_entity_id="order"),
    }
    values.update(overrides)
    return SemanticQueryIR(**values)  # type: ignore[arg-type]


def smoke_case(*assertions, query: SemanticQueryIR | None = None) -> SmokeQueryCase:
    return SmokeQueryCase(
        case_id="smoke-case",
        query=query or semantic_ir(),
        fixture_profile_id="sqlite-v1",
        assertions=assertions,
    )


def semantic_case(*contracts) -> SemanticContractCase:
    return SemanticContractCase(
        case_id="semantic-case",
        query=semantic_ir(),
        fixture_profile_id="sqlite-v1",
        contracts=contracts,
    )


class StubVerificationExecutor:
    executor_id = "stub-verification"
    capability_ids = frozenset({"aggregation", "filtering"})
    capability_fingerprint = SCOPE_FINGERPRINT

    def __init__(self, observation: VerificationObservation) -> None:
        self.observation = observation
        self.calls = 0

    async def open_session(self, fixture_profile_id, context):
        raise NotImplementedError

    async def execute(self, ir, session, context):
        raise NotImplementedError

    async def run_case(self, ir, *, fixture_profile_id, context):
        self.calls += 1
        return self.observation
