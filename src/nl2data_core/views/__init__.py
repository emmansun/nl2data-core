"""Semantic View resolution: bounded definitions, trusted context, projections.

The views package resolves immutable, versioned Semantic View definitions
against trusted resolution context and produces authorized projections that
planning, IR validation, provider-context assembly, workflow evidence, and
Memory revalidation can bind to.  Every model is bounded, frozen, and
serializes only safe references - never credentials, physical bindings, or
hidden policy rules.
"""

from .context import ResolutionContext
from .models import (
    CalculatedField,
    CalculatedOutputType,
    ExprNode,
    SemanticDescriptor,
    SemanticEntityDescriptor,
    SemanticFieldDescriptor,
    SemanticRelationshipDescriptor,
    SemanticViewDefinition,
    ValueSemantics,
    ViewMemberRestrictions,
    ViewProvenance,
)
from .outcomes import ResolutionIssue, ResolutionOutcome, denied, unavailable
from .projection import ResolvedViewEntity, ResolvedViewField, ResolvedViewProjection
from .registry import ViewRegistry

__all__ = [
    "CalculatedField",
    "CalculatedOutputType",
    "ExprNode",
    "ResolutionContext",
    "ResolutionIssue",
    "ResolutionOutcome",
    "ResolvedViewEntity",
    "ResolvedViewField",
    "ResolvedViewProjection",
    "SemanticDescriptor",
    "SemanticEntityDescriptor",
    "SemanticFieldDescriptor",
    "SemanticRelationshipDescriptor",
    "SemanticViewDefinition",
    "ValueSemantics",
    "ViewMemberRestrictions",
    "ViewProvenance",
    "ViewRegistry",
    "denied",
    "unavailable",
]
