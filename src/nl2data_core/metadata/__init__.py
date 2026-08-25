"""Metadata discovery and inference: safe snapshots, proposals, and drift.

The metadata package defines the provider-neutral discovery contract
(``MetadataDiscoverer``), the immutable versioned ``MetadataSnapshot``
models, canonical serialization and SHA-256 fingerprinting, deterministic
semantic proposal generation with explicit trust/evidence/confidence,
immutable proposal review (approve/reject/revise), conversion of approved
proposals into Semantic Model Bundle inputs, and safe snapshot comparison
for schema drift.

Discovery and inference are optional capabilities: adapters without them
remain valid ``QueryAdapter`` implementations, and manual descriptor and
Bundle construction keeps working unchanged.  Nothing in this package
grants access - inferred or unreviewed facts never grant View visibility,
tenant access, mandatory filters, or execution authorization.
"""

from .compare import SnapshotComparison, compare_snapshots
from .conversion import ConvertedBundleInput, ProposalReference, convert_approved_proposals
from .inference import infer_proposals
from .models import (
    METADATA_SCHEMA_VERSION,
    MetadataConfidence,
    MetadataConstraint,
    MetadataConstraintKind,
    MetadataEvidence,
    MetadataField,
    MetadataFreshness,
    MetadataObject,
    MetadataObjectKind,
    MetadataProvenance,
    MetadataRelationship,
    MetadataRelationshipKind,
    MetadataSnapshot,
    MetadataSourceReference,
    MetadataStatistic,
    MetadataStatisticKind,
    MetadataTrustLevel,
)
from .proposals import (
    ProposalStatus,
    SemanticProposal,
    SemanticProposalKind,
    SemanticProposalSet,
)
from .protocol import (
    MetadataBoundsExceededError,
    MetadataDiscoverer,
    MetadataDiscoveryCapability,
    MetadataDiscoveryConfig,
    MetadataDiscoveryError,
    MetadataUnauthorizedError,
    MetadataUnavailableError,
)

__all__ = [
    "METADATA_SCHEMA_VERSION",
    "ConvertedBundleInput",
    "MetadataBoundsExceededError",
    "MetadataConfidence",
    "MetadataConstraint",
    "MetadataConstraintKind",
    "MetadataDiscoveryCapability",
    "MetadataDiscoveryConfig",
    "MetadataDiscoveryError",
    "MetadataDiscoverer",
    "MetadataEvidence",
    "MetadataField",
    "MetadataFreshness",
    "MetadataObject",
    "MetadataObjectKind",
    "MetadataProvenance",
    "MetadataRelationship",
    "MetadataRelationshipKind",
    "MetadataSnapshot",
    "MetadataSourceReference",
    "MetadataStatistic",
    "MetadataStatisticKind",
    "MetadataTrustLevel",
    "MetadataUnavailableError",
    "MetadataUnauthorizedError",
    "ProposalReference",
    "ProposalStatus",
    "SemanticProposal",
    "SemanticProposalKind",
    "SemanticProposalSet",
    "SnapshotComparison",
    "compare_snapshots",
    "convert_approved_proposals",
    "infer_proposals",
]
