"""Query adapter contract foundation (DDS-002)."""

from .fingerprint import artifact_fingerprint, safe_artifact_payload
from .models import (
    AdapterCapabilities,
    AdapterLimits,
    AsyncMode,
    CostEstimate,
    ExecutionResult,
    GeneratedArtifact,
    ParsedArtifact,
    ValidatedArtifact,
    ValidationContext,
)
from .protocol import QueryAdapter

__all__ = [
    "AdapterCapabilities",
    "AdapterLimits",
    "AsyncMode",
    "CostEstimate",
    "ExecutionResult",
    "GeneratedArtifact",
    "ParsedArtifact",
    "QueryAdapter",
    "ValidatedArtifact",
    "ValidationContext",
    "artifact_fingerprint",
    "safe_artifact_payload",
]
