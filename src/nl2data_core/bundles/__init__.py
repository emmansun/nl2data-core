"""Semantic Model Bundles: versioned immutable semantic artifacts.

The bundles package defines the authoritative safe semantic model artifact
(``SemanticModelBundle``), its structural validation, canonical loading,
and a replaceable catalog with an atomic publish/activate/rollback
lifecycle.  Bundles wrap the existing bounded descriptor primitives and
add measures, grains, source references, dependencies, trust metadata,
compatibility, and safe provenance - never credentials, physical bindings,
or authorization claims.
"""

from .catalog import (
    BundleCatalogIssue,
    BundleCatalogOutcome,
    BundlePublication,
    InMemorySemanticBundleCatalog,
    SemanticBundleCatalog,
)
from .loader import (
    BundleLoadResult,
    CanonicalBundleLoader,
    SemanticBundleLoader,
)
from .models import (
    BUNDLE_SCHEMA_VERSION,
    BundleCompatibility,
    BundleDependency,
    BundleProvenance,
    BundleQualityStatus,
    SemanticGrain,
    SemanticMeasure,
    SemanticModelBundle,
    SemanticSourceReference,
    SemanticTrustKind,
    SemanticTrustMarker,
)
from .validation import (
    BundleValidationIssue,
    BundleValidationResult,
    validate_bundle,
)

__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "BundleCatalogIssue",
    "BundleCatalogOutcome",
    "BundleCompatibility",
    "BundleDependency",
    "BundleLoadResult",
    "BundleProvenance",
    "BundlePublication",
    "BundleQualityStatus",
    "BundleValidationIssue",
    "BundleValidationResult",
    "CanonicalBundleLoader",
    "InMemorySemanticBundleCatalog",
    "SemanticBundleCatalog",
    "SemanticBundleLoader",
    "SemanticGrain",
    "SemanticMeasure",
    "SemanticModelBundle",
    "SemanticSourceReference",
    "SemanticTrustKind",
    "SemanticTrustMarker",
    "validate_bundle",
]
