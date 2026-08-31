"""Shared compiler governance boundary contracts (DDS-019).

Backend-neutral compilation context, evidence, guard results, and the
pre-execution guard boundary shared by every compiler and adapter.
"""

from .contract import (
    ArtifactGuardResult,
    CalculatedFieldHash,
    CompilationContext,
    CompilationEvidence,
    CompilationIssue,
    CompileResult,
    IRCompiler,
    ResultLineageEvidence,
    artifact_guard_evidence_fingerprint,
    compilation_evidence_fingerprint,
    result_lineage_fingerprint,
    verify_pre_execution_guard,
)
from .expansion import (
    EXPANSION_IDENTITY,
    ExpansionError,
    ZeroDivisionPolicyError,
    calculated_field_hashes,
    expand_mongo,
    expand_sql,
    resolve_calculated_fields,
)

__all__ = [
    "EXPANSION_IDENTITY",
    "ArtifactGuardResult",
    "CalculatedFieldHash",
    "CompilationContext",
    "CompilationEvidence",
    "CompilationIssue",
    "CompileResult",
    "ExpansionError",
    "IRCompiler",
    "ResultLineageEvidence",
    "ZeroDivisionPolicyError",
    "artifact_guard_evidence_fingerprint",
    "calculated_field_hashes",
    "compilation_evidence_fingerprint",
    "expand_mongo",
    "expand_sql",
    "result_lineage_fingerprint",
    "resolve_calculated_fields",
    "verify_pre_execution_guard",
]
