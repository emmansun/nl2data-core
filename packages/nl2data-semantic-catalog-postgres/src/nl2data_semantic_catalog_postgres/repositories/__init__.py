"""Focused repositories over the shared catalog unit of work.

Repositories persist one domain each (metadata snapshots/proposal sets,
assembly drafts, Bundle publications, verification/audit evidence, and
activation/history).  They operate on connections handed to them by a
transaction owner and never commit independently during atomic
cross-domain operations such as publication, activation, or rollback.
"""

from __future__ import annotations

from .activation import ActivationRepository
from .audit_evidence import AuditEvidenceRepository
from .drafts import DraftRepository
from .evidence import EvidenceRepository
from .publications import PublicationRepository
from .snapshots import SnapshotRepository

__all__ = [
    "ActivationRepository",
    "AuditEvidenceRepository",
    "DraftRepository",
    "EvidenceRepository",
    "PublicationRepository",
    "SnapshotRepository",
]
