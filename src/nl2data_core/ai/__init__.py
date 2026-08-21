"""Internal AI runtime boundary for NL2Data.

Provider-neutral model invocation, structured intent resolution, and
deterministic AI evaluation.  Nothing here is part of the public API;
applications import from :mod:`nl2data` instead.  The core never imports
vendor SDKs, credentials, or network frameworks.
"""

from __future__ import annotations
