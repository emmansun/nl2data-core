"""Lifecycle contract shared by every controlled fixture profile."""

from __future__ import annotations

from abc import ABC, abstractmethod

from nl2data_core.fixtures.models import FixtureSpec


class FixtureProfile(ABC):
    """A controlled fixture profile with a uniform lifecycle.

    Provisioning, reset, disposal, and verification are deterministic:
    equal specs produce equal schema, seed, and protected query results.
    Profiles never expose native clients, credentials, or raw state to
    evaluation callers.
    """

    @property
    @abstractmethod
    def spec(self) -> FixtureSpec:
        """The versioned fixture spec this profile provisions."""

    @abstractmethod
    def provision(self) -> None:
        """Create or refresh the fixture to its declared seed state."""

    @abstractmethod
    def reset(self) -> None:
        """Restore the fixture to its seed state using the reset strategy."""

    @abstractmethod
    def dispose(self) -> None:
        """Release fixture resources; safe to call more than once."""

    @abstractmethod
    def verify(self) -> None:
        """Verify expected object counts; raise on any mismatch."""
