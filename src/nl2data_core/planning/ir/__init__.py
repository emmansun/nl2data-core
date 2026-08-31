"""Canonical Semantic Query IR: models and validation."""

from .models import (
    IR_VERSION,
    NAMED_QUERY_PLACEHOLDER_CAPABILITY,
    NAMED_QUERY_PLACEHOLDER_KIND,
    SCALAR_TYPES,
    IRExtension,
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRResultShape,
    IRSelection,
    IRTimeContext,
    NamedQueryPlaceholderExtension,
    NamedQueryPlaceholderParameter,
    SemanticQueryIR,
)
from .validation import (
    IRValidationIssue,
    IRValidationResult,
    validate_ir,
    verify_ir_fingerprint,
)

__all__ = [
    "IRFilter",
    "IRGrouping",
    "IROrdering",
    "IRProvenance",
    "IRResultShape",
    "IRSelection",
    "IRTimeContext",
    "IRValidationIssue",
    "IRValidationResult",
    "IR_VERSION",
    "IRExtension",
    "NAMED_QUERY_PLACEHOLDER_CAPABILITY",
    "NAMED_QUERY_PLACEHOLDER_KIND",
    "NamedQueryPlaceholderExtension",
    "NamedQueryPlaceholderParameter",
    "SCALAR_TYPES",
    "SemanticQueryIR",
    "validate_ir",
    "verify_ir_fingerprint",
]
