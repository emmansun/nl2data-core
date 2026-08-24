"""Bounded memory subsystem for multi-turn context.

Memory stores only immutable logical facts and protected fingerprints -
never raw prompts, SQL/MQL, rows/documents, secrets, or native objects.
Recalled memory is context for the current turn, never authority: every
turn revalidates tenant scope, policy/catalog fingerprints, semantic view,
adapter/artifact references, and expiry before any reference is used.

The shared Redis provider and its configuration are exported lazily so
importing this package (or ``nl2data``) never imports the optional
``redis`` driver; the in-memory provider stays fully usable without it.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    """PEP 562 lazy exports for the optional shared memory provider."""
    if name == "RedisMemoryConfig":
        from .redis_config import RedisMemoryConfig

        return RedisMemoryConfig
    if name == "RedisMemoryProvider":
        from .redis_provider import RedisMemoryProvider

        return RedisMemoryProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
