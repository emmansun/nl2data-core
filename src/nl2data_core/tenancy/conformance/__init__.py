"""Tenant isolation conformance: deterministic cases and protected reports."""

from __future__ import annotations

from .cases import default_tenant_conformance_dataset
from .models import (
    TenantAssertionResult,
    TenantCaseResult,
    TenantConformanceAssertion,
    TenantConformanceCase,
    TenantConformanceDataset,
    TenantConformanceDecision,
    TenantConformanceOutcome,
    TenantConformanceReport,
    TenantProtectedEvidence,
    TenantRunContext,
)
from .runner import (
    TenantConformanceRunner,
    evaluate_assertions,
    evidence_is_redacted,
)

__all__ = [
    "TenantAssertionResult",
    "TenantCaseResult",
    "TenantConformanceAssertion",
    "TenantConformanceCase",
    "TenantConformanceDataset",
    "TenantConformanceDecision",
    "TenantConformanceOutcome",
    "TenantConformanceReport",
    "TenantConformanceRunner",
    "TenantProtectedEvidence",
    "TenantRunContext",
    "default_tenant_conformance_dataset",
    "evaluate_assertions",
    "evidence_is_redacted",
]
