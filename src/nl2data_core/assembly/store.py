"""Replaceable tenant-scoped persistence boundary for assembly drafts."""

from __future__ import annotations

from typing import Protocol

from .models import AssemblyDraft, DraftRevisionConflict


class AssemblyDraftStore(Protocol):
    """Persistence contract with compare-and-swap draft replacement."""

    def create(
        self,
        draft: AssemblyDraft,
        *,
        tenant_scope_fingerprint: str,
    ) -> None: ...

    def get(
        self,
        draft_id: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> AssemblyDraft | None: ...

    def replace(
        self,
        draft: AssemblyDraft,
        *,
        expected_revision: int,
        tenant_scope_fingerprint: str,
    ) -> None: ...


class InMemoryAssemblyDraftStore:
    """Bounded process-local reference store with revision CAS semantics."""

    def __init__(self, *, max_drafts: int = 10_000) -> None:
        if max_drafts < 1:
            raise ValueError("max_drafts must be positive")
        self._max_drafts = max_drafts
        self._drafts: dict[tuple[str, str], AssemblyDraft] = {}

    def create(
        self,
        draft: AssemblyDraft,
        *,
        tenant_scope_fingerprint: str,
    ) -> None:
        key = (tenant_scope_fingerprint, draft.draft_id)
        if key in self._drafts:
            raise ValueError(f"assembly draft '{draft.draft_id}' already exists")
        if len(self._drafts) >= self._max_drafts:
            raise ValueError("assembly draft store capacity exceeded")
        self._drafts[key] = draft

    def get(
        self,
        draft_id: str,
        *,
        tenant_scope_fingerprint: str,
    ) -> AssemblyDraft | None:
        return self._drafts.get((tenant_scope_fingerprint, draft_id))

    def replace(
        self,
        draft: AssemblyDraft,
        *,
        expected_revision: int,
        tenant_scope_fingerprint: str,
    ) -> None:
        key = (tenant_scope_fingerprint, draft.draft_id)
        current = self._drafts.get(key)
        if current is None:
            raise ValueError(f"assembly draft '{draft.draft_id}' does not exist")
        current.require_revision(expected_revision)
        if draft.draft_revision != expected_revision + 1:
            raise DraftRevisionConflict(
                expected=expected_revision + 1,
                actual=draft.draft_revision,
            )
        self._drafts[key] = draft