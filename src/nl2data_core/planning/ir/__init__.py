"""Canonical Semantic Query IR: models, validation, and plan compatibility."""

from .compat import ir_to_plan, plan_to_ir
from .models import (
    IR_VERSION,
    SCALAR_TYPES,
    IRExtension,
    IRFilter,
    IRGrouping,
    IROrdering,
    IRProvenance,
    IRResultShape,
    IRSelection,
    IRTimeContext,
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
    "SCALAR_TYPES",
    "SemanticQueryIR",
    "ir_to_plan",
    "plan_to_ir",
    "validate_ir",
    "verify_ir_fingerprint",
]
