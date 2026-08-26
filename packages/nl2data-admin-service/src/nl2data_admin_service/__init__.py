"""Optional transport-neutral admin control-plane service for nl2data-core.

The ``nl2data-admin-service`` package exposes a framework-neutral application
service for the metadata-to-Bundle lifecycle.  It intentionally has no HTTP,
authentication, web-framework, or database-driver dependencies so that hosts
can wrap it in their chosen transport (HTTP/CLI/UI) without coupling core to
any transport technology.
"""

from __future__ import annotations

from .config import AdminServiceConfig, AdminServiceProfile
from .service import AdminService

__all__ = ["AdminService", "AdminServiceConfig", "AdminServiceProfile"]
