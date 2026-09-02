"""Shared constrained scalar patterns for publication contracts."""

FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
ISSUE_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"