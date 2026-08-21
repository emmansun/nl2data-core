"""SQL adapter specialization of the canonical QueryAdapter contract.

SQL-specific behavior lives only in this package; the core protocol in
:mod:`nl2data_core.adapters.protocol` stays backend-neutral.
"""

from __future__ import annotations

from .adapter import SqlQueryAdapter

__all__ = ["SqlQueryAdapter"]
