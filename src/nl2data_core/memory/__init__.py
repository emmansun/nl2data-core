"""Bounded memory subsystem for multi-turn context.

Memory stores only immutable logical facts and protected fingerprints -
never raw prompts, SQL/MQL, rows/documents, secrets, or native objects.
Recalled memory is context for the current turn, never authority: every
turn revalidates tenant scope, policy/catalog fingerprints, semantic view,
adapter/artifact references, and expiry before any reference is used.
"""
