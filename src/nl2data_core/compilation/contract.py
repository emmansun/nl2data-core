"""Backend-neutral compiler governance boundary contracts (DDS-019).

The compilation boundary separates deterministic IR-to-artifact compilation
from governance.  A compiler receives a validated canonical IR plus an
immutable context of fingerprints, capabilities, bounds, and compiler-specific
physical bindings, and emits a backend artifact plus safe evidence.

Evidence links identities and fingerprints only - never raw IR payloads,
raw SQL/MQL, credentials, tenant identity, or native objects - so the
logical IR fingerprint stays the canonical identity of the query while
each backend artifact keeps its own artifact fingerprint.

Compilers cannot grant authority: they never touch governance or
authorization, and adapter execution is only reachable through the shared
pre-execution guard boundary (:func:`verify_pre_execution_guard`), which
re-verifies compiler evidence, artifact guard results, obligations, limits,
and execution authorization immediately before the adapter call.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nl2data_core.adapters.models import AdapterCapabilities
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.governance.models import EffectiveLimits, ExecutionAuthorization
from nl2data_core.planning.ir.models import IRViewReference, LogicalJoinPlan, SemanticQueryIR
from nl2data_core.planning.models import PhysicalBinding
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.views.models import CalculatedField

_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"

#: Activation switch for planner-identity versioning strictness (ADR-033).
#: When versioning becomes active, evidence missing ``planner_identity``
#: is rejected outright - strictness lands before version divergence so
#: no evidence can exist in an "unversioned" state.
PLANNER_IDENTITY_VERSIONING = False

#: Activation switch for expansion-identity versioning strictness (v4.2 D12).
#: When versioning becomes active, evidence missing ``expansion_identity``
#: is rejected outright - the same strictness ordering as the planner
#: identity guard, so legacy evidence stays valid until the switch flips.
EXPANSION_IDENTITY_VERSIONING = False


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CompilationContext(BaseModel):
    """Immutable, backend-neutral context for one compiler invocation.

    Carries the validated canonical IR, view/model bundle fingerprints,
    tenant scope, purpose, policy fingerprint, adapter capabilities,
    effective bounds, mandatory filter obligations, and the explicit
    compiler-specific context (physical binding).  The physical binding is
    compiler context only: it never enters the IR payload and never appears
    in compilation evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ir: SemanticQueryIR
    view: AuthorizedView | None = None
    view_reference: IRViewReference | None = None
    view_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    bundle_id: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    bundle_version: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    bundle_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    tenant_scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    purpose: str | None = Field(default=None, min_length=1, max_length=64)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    adapter_capabilities: AdapterCapabilities
    effective_limits: EffectiveLimits | None = None
    mandatory_filter_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    compiler_context: PhysicalBinding | None = None
    join_plan: LogicalJoinPlan | None = None
    planner_identity: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    #: Bundle-anchored calculated-field definitions available for
    #: compile-time expansion (v4.2 D1); unset when none are declared.
    calculated_fields: tuple[CalculatedField, ...] | None = None
    #: Expansion-implementation identity anchored on the producer side
    #: (v4.2 D12); compilers copy it into the evidence so the symmetric
    #: pre-execution guard can reject drift and one-sided identities.
    expansion_identity: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def _consistent(self) -> CompilationContext:
        bundle_parts = (self.bundle_id, self.bundle_version, self.bundle_fingerprint)
        if any(part is not None for part in bundle_parts) and not all(
            part is not None for part in bundle_parts
        ):
            raise ValueError(
                "bundle id, version, and fingerprint must be supplied together"
            )
        if self.view is not None and self.view.view_bound:
            if self.view_fingerprint is None:
                raise ValueError(
                    "a bound view requires the resolved view fingerprint in context"
                )
            if self.view_reference is None:
                raise ValueError("a bound view requires its reference in context")
        return self


class CompilationIssue(BaseModel):
    """One bounded, safe issue observed during compilation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=_IDENTIFIER_PATTERN)
    message: str = Field(min_length=1, max_length=512)


class CompilationEvidence(BaseModel):
    """Safe evidence linking one compiled artifact to its decision chain.

    The evidence carries identity and fingerprint references only - never
    the raw IR payload, raw SQL/MQL, credentials, tenant identity, or
    native values.  ``artifact_fingerprint`` is the canonical executable
    identity of the produced artifact (for example the SQL artifact
    fingerprint or the structured MQL spec fingerprint).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ir_version: int = Field(ge=1, le=1_000)
    ir_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    operation: str = Field(min_length=1, max_length=64)
    field_ids: frozenset[str] = Field(default_factory=frozenset)
    view_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    bundle_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    tenant_scope_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    purpose: str | None = Field(default=None, min_length=1, max_length=64)
    adapter_type: str = Field(pattern=_IDENTIFIER_PATTERN)
    capability_ids: frozenset[str] = Field(default_factory=frozenset)
    required_capabilities: frozenset[str] = Field(default_factory=frozenset)
    mandatory_filter_fingerprints: frozenset[str] = Field(default_factory=frozenset)
    max_rows: int | None = Field(default=None, ge=1, le=1_000_000_000)
    max_columns: int | None = Field(default=None, ge=1, le=100_000)
    max_execution_seconds: float | None = Field(default=None, gt=0.0, le=3600.0)
    max_result_bytes: int | None = Field(default=None, ge=1, le=1_073_741_824)
    compiler_identity: str = Field(pattern=_IDENTIFIER_PATTERN)
    compiler_version: str = Field(min_length=1, max_length=64)
    artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    join_plan_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    planner_identity: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    calculated_field_hashes: tuple[CalculatedFieldHash, ...] | None = Field(
        default=None, max_length=32
    )
    expansion_identity: str | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def _validate_references(self) -> CompilationEvidence:
        pattern = re.compile(_FINGERPRINT_PATTERN)
        for fingerprint in self.mandatory_filter_fingerprints:
            if not pattern.fullmatch(fingerprint):
                raise ValueError("obligation references must be sha256 fingerprints")
        if self.calculated_field_hashes is not None:
            names = [record.name for record in self.calculated_field_hashes]
            if names != sorted(names) or len(names) != len(set(names)):
                raise ValueError(
                    "calculated field hash records must be unique and sorted by name"
                )
        return self


class CalculatedFieldHash(BaseModel):
    """Frozen name+hash record of one referenced calculated field (v4.2 D6).

    The hash covers the canonical expression tree, the zero-division
    policy, and the output type - never the tree itself, physical names,
    or values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_IDENTIFIER_PATTERN)
    hash: str = Field(pattern=_FINGERPRINT_PATTERN)


class CompileResult(BaseModel):
    """The deterministic output of one compiler invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact: str = Field(min_length=1, max_length=1_000_000)
    evidence: CompilationEvidence
    issues: tuple[CompilationIssue, ...] = Field(default_factory=tuple, max_length=16)


class ArtifactGuardResult(BaseModel):
    """The artifact guard outcome consumed by the pre-execution boundary.

    ``fingerprint`` is the guard fingerprint of the validated artifact (the
    identity bound into the execution authorization); ``artifact_fingerprint``
    is the canonical executable identity of the artifact itself, linking the
    guard result back to the compilation evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    reasons: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    guard_identity: str = Field(pattern=_IDENTIFIER_PATTERN)
    artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    obligations_verified: frozenset[str] = Field(default_factory=frozenset)
    bounded_rows: int | None = Field(default=None, ge=1, le=1_000_000_000)

    @property
    def rejected(self) -> bool:
        return not self.accepted


class ResultLineageEvidence(BaseModel):
    """Decision lineage of one protected result (audit-safe).

    Links the protected result fingerprint back through the artifact guard,
    compilation evidence, IR/view/bundle/policy identities, and the
    execution authorization.  Fingerprints only - never result rows, raw
    payloads, or credentials.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    artifact_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    guard_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    ir_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    view_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    bundle_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    authorization_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    adapter_type: str = Field(pattern=_IDENTIFIER_PATTERN)
    compiler_identity: str = Field(pattern=_IDENTIFIER_PATTERN)
    compiler_version: str = Field(min_length=1, max_length=64)


@runtime_checkable
class IRCompiler(Protocol):
    """One backend compiler: validated IR plus immutable context in, artifact out.

    Compilers are deterministic and side-effect free.  They never grant
    authority: no governance evaluation, no authorization issuance, and no
    capability broadening happens inside a compiler.  A compiler that
    cannot produce an artifact for the given context raises a structured
    error (fail closed) instead of emitting partial or unbounded output.
    """

    def compile(
        self, ir: SemanticQueryIR, *, context: CompilationContext
    ) -> CompileResult: ...


def compilation_evidence_fingerprint(evidence: CompilationEvidence) -> str:
    """Stable evidence fingerprint of the safe compilation facts."""
    payload = {
        "ir_version": evidence.ir_version,
        "ir_fingerprint": evidence.ir_fingerprint,
        "source_id": evidence.source_id,
        "operation": evidence.operation,
        "field_ids": sorted(evidence.field_ids),
        "view_fingerprint": evidence.view_fingerprint,
        "bundle_fingerprint": evidence.bundle_fingerprint,
        "policy_fingerprint": evidence.policy_fingerprint,
        "tenant_scope_fingerprint": evidence.tenant_scope_fingerprint,
        "purpose": evidence.purpose,
        "adapter_type": evidence.adapter_type,
        "capability_ids": sorted(evidence.capability_ids),
        "required_capabilities": sorted(evidence.required_capabilities),
        "mandatory_filter_fingerprints": sorted(
            evidence.mandatory_filter_fingerprints
        ),
        "max_rows": evidence.max_rows,
        "max_columns": evidence.max_columns,
        "max_execution_seconds": evidence.max_execution_seconds,
        "max_result_bytes": evidence.max_result_bytes,
        "compiler_identity": evidence.compiler_identity,
        "compiler_version": evidence.compiler_version,
        "artifact_fingerprint": evidence.artifact_fingerprint,
        "join_plan_fingerprint": evidence.join_plan_fingerprint,
        "planner_identity": evidence.planner_identity,
    }
    # N6 (v4.2): unset optional members are omitted entirely so evidence
    # for queries without calculated fields keeps its previous fingerprint
    # byte-identically.
    if evidence.calculated_field_hashes:
        payload["calculated_field_hashes"] = [
            {"name": record.name, "hash": record.hash}
            for record in evidence.calculated_field_hashes
        ]
    if evidence.expansion_identity is not None:
        payload["expansion_identity"] = evidence.expansion_identity
    return strict_sha256_fingerprint(payload)


def artifact_guard_evidence_fingerprint(guard: ArtifactGuardResult) -> str:
    """Stable evidence fingerprint of one artifact guard result."""
    return strict_sha256_fingerprint(
        {
            "accepted": guard.accepted,
            "guard_identity": guard.guard_identity,
            "guard_fingerprint": guard.fingerprint,
            "artifact_fingerprint": guard.artifact_fingerprint,
            "obligations_verified": sorted(guard.obligations_verified),
            "bounded_rows": guard.bounded_rows,
        }
    )


def result_lineage_fingerprint(lineage: ResultLineageEvidence) -> str:
    """Stable fingerprint of one protected result's decision lineage."""
    return strict_sha256_fingerprint(
        {
            "result_fingerprint": lineage.result_fingerprint,
            "artifact_fingerprint": lineage.artifact_fingerprint,
            "guard_fingerprint": lineage.guard_fingerprint,
            "ir_fingerprint": lineage.ir_fingerprint,
            "view_fingerprint": lineage.view_fingerprint,
            "bundle_fingerprint": lineage.bundle_fingerprint,
            "policy_fingerprint": lineage.policy_fingerprint,
            "authorization_id": lineage.authorization_id,
            "adapter_type": lineage.adapter_type,
            "compiler_identity": lineage.compiler_identity,
            "compiler_version": lineage.compiler_version,
        }
    )


def verify_pre_execution_guard(
    *,
    context: CompilationContext,
    evidence: CompilationEvidence,
    guard: ArtifactGuardResult,
    authorization: ExecutionAuthorization | None,
    now: datetime | None = None,
    identity_versioning: bool | None = None,
    expansion_identity_versioning: bool | None = None,
) -> tuple[str, ...]:
    """Re-verify the full compiler-governance chain immediately before execution.

    The boundary rejects stale or missing evidence, unguarded or
    obligation-incomplete artifacts, unsupported capabilities, unbounded
    results, expired or mismatched authorizations, and planner-identity
    drift (design D5): any identity mismatch between evidence and context,
    and any one-sided identity - context without evidence or evidence
    without context - fails the guard because a one-sided identity cannot
    be drift-checked.  When ``identity_versioning`` is active (default:
    :data:`PLANNER_IDENTITY_VERSIONING`), evidence missing a planner
    identity is rejected outright.  The expansion-identity guard (v4.2 D12)
    mirrors the planner-identity guard: drift and one-sided identities are
    rejected, and when ``expansion_identity_versioning`` is active
    (default: :data:`EXPANSION_IDENTITY_VERSIONING`) evidence missing an
    expansion identity is rejected outright.  Returns human-safe reasons;
    an empty tuple means the chain verified and execution may proceed.  The
    boundary never raises and never broadens.
    """
    if identity_versioning is None:
        identity_versioning = PLANNER_IDENTITY_VERSIONING
    if expansion_identity_versioning is None:
        expansion_identity_versioning = EXPANSION_IDENTITY_VERSIONING
    reasons: list[str] = []

    if evidence.ir_version != context.ir.ir_version:
        reasons.append("compilation evidence is stale: IR version mismatch")
    if evidence.ir_fingerprint != context.ir.fingerprint:
        reasons.append("compilation evidence does not match the current IR")
    if evidence.view_fingerprint != context.view_fingerprint:
        reasons.append("compilation evidence does not match the current view")
    if evidence.bundle_fingerprint != context.bundle_fingerprint:
        reasons.append("compilation evidence does not match the current model bundle")
    if evidence.policy_fingerprint != context.policy_fingerprint:
        reasons.append("compilation evidence does not match the current policy")
    if evidence.tenant_scope_fingerprint != context.tenant_scope_fingerprint:
        reasons.append("compilation evidence does not match the current tenant scope")
    # Planner-identity drift guard (design D5): symmetric, fail-closed.
    if context.planner_identity is not None and evidence.planner_identity is None:
        reasons.append(
            "compilation evidence lacks the planner identity declared by the "
            "compilation context"
        )
    elif context.planner_identity is None and evidence.planner_identity is not None:
        reasons.append(
            "compilation evidence carries a planner identity the compilation "
            "context does not declare"
        )
    elif (
        context.planner_identity is not None
        and evidence.planner_identity is not None
        and evidence.planner_identity != context.planner_identity
    ):
        reasons.append(
            "compilation evidence planner identity does not match the current "
            "planner identity"
        )
    if identity_versioning and evidence.planner_identity is None:
        reasons.append(
            "compilation evidence is missing a planner identity and planner "
            "identity versioning is active"
        )
    # Expansion-identity drift guard (v4.2 D12): symmetric, fail-closed.
    if context.expansion_identity is not None and evidence.expansion_identity is None:
        reasons.append(
            "compilation evidence lacks the expansion identity declared by the "
            "compilation context"
        )
    elif context.expansion_identity is None and evidence.expansion_identity is not None:
        reasons.append(
            "compilation evidence carries an expansion identity the compilation "
            "context does not declare"
        )
    elif (
        context.expansion_identity is not None
        and evidence.expansion_identity is not None
        and evidence.expansion_identity != context.expansion_identity
    ):
        reasons.append(
            "compilation evidence expansion identity does not match the current "
            "expansion identity"
        )
    if expansion_identity_versioning and evidence.expansion_identity is None:
        reasons.append(
            "compilation evidence is missing an expansion identity and "
            "expansion identity versioning is active"
        )
    if evidence.purpose != context.purpose:
        reasons.append("compilation evidence does not match the current purpose")
    if evidence.adapter_type != context.adapter_capabilities.adapter_type:
        reasons.append("compilation evidence does not match the adapter profile")
    if evidence.capability_ids != context.adapter_capabilities.features:
        reasons.append("compilation evidence does not match the adapter capabilities")
    if not guard.accepted:
        reasons.append("artifact guard rejected the artifact")
    if guard.artifact_fingerprint != evidence.artifact_fingerprint:
        reasons.append("artifact guard evidence does not match the compiled artifact")
    for capability in sorted(context.ir.required_capabilities):
        if capability not in context.adapter_capabilities.features:
            reasons.append(f"adapter lacks required capability '{capability}'")
    for obligation in sorted(context.mandatory_filter_fingerprints):
        if obligation not in guard.obligations_verified:
            reasons.append(
                f"mandatory filter obligation '{obligation[:16]}...' is not "
                "enforced by the guarded artifact"
            )
    if (
        guard.bounded_rows is not None
        and context.effective_limits is not None
        and guard.bounded_rows > context.effective_limits.max_rows
    ):
        reasons.append("guarded artifact result bound exceeds the effective limits")
    if context.effective_limits is not None:
        limits = context.effective_limits
        if evidence.max_rows != limits.max_rows:
            reasons.append("compilation evidence does not match the effective row limit")
        if evidence.max_columns != limits.max_columns:
            reasons.append(
                "compilation evidence does not match the effective column limit"
            )
        if evidence.max_execution_seconds != limits.max_execution_seconds:
            reasons.append(
                "compilation evidence does not match the effective execution limit"
            )
        if evidence.max_result_bytes != limits.max_result_bytes:
            reasons.append(
                "compilation evidence does not match the effective result limit"
            )
    if authorization is None:
        reasons.append("execution authorization is missing")
    else:
        if authorization.is_expired(now=now or _utc_now()):
            reasons.append("execution authorization has expired")
        if authorization.artifact_fingerprint != guard.fingerprint:
            reasons.append("execution authorization is not bound to the guarded artifact")
        if (
            authorization.ir_fingerprint is not None
            and authorization.ir_fingerprint != evidence.ir_fingerprint
        ):
            reasons.append("execution authorization is not bound to the compiled IR")
        if authorization.adapter_type != evidence.adapter_type:
            reasons.append("execution authorization does not match the adapter")
        if (
            context.policy_fingerprint is not None
            and authorization.policy_fingerprint != context.policy_fingerprint
        ):
            reasons.append("execution authorization does not match the current policy")
        if (
            context.view_fingerprint is not None
            and authorization.view_fingerprint != context.view_fingerprint
        ):
            reasons.append("execution authorization does not match the current view")
        if (
            context.bundle_fingerprint is not None
            and authorization.bundle_fingerprint != context.bundle_fingerprint
        ):
            reasons.append(
                "execution authorization does not match the current model bundle"
            )
        if (
            context.tenant_scope_fingerprint is not None
            and authorization.tenant_scope_fingerprint
            != context.tenant_scope_fingerprint
        ):
            reasons.append(
                "execution authorization does not match the current tenant scope"
            )
        if authorization.capability_ids and (
            authorization.capability_ids != context.adapter_capabilities.features
        ):
            reasons.append(
                "execution authorization does not match the adapter capabilities"
            )
        if (
            context.effective_limits is not None
            and authorization.effective_limits != context.effective_limits
        ):
            reasons.append(
                "execution authorization does not match the effective limits"
            )

    return tuple(reasons)
