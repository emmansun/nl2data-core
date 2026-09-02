"""Shared Admin service request, dependency, and normalization helpers."""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from nl2data_core.assembly import (
    AssemblyDraft,
    AssemblyDraftStore,
    DraftRevisionConflict,
    LifecycleAuthorizationContext,
    LifecycleAuthorizationError,
    LifecycleAuthorizer,
)
from nl2data_core.verification.smoke import RunnableVerificationExecutor

from .auth import AuthContext, Permission
from .dtos import ErrorCategory, JobInfo, JobStatus, PaginationParams
from .errors import (
    AdminServiceError,
    AuthorizationDeniedError,
    ConflictError,
    DiscoveryError,
    NotFoundError,
    ValidationError,
)
from .protocols import (
    AdminServiceDependencies,
    JobRecord,
    LifecycleCatalogPort,
    ManifestBundleVerifier,
    MetadataCatalogPort,
    MetadataDiscoverer,
    SemanticBundleEmitter,
    VerificationExecutionContextFactory,
)

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class AdminRequestContext:
    """Trusted request context plus host audit reference."""

    auth_context: AuthContext
    host_audit_reference: str

    @property
    def audit_reference(self) -> str:
        return self.auth_context.audit_reference or self.host_audit_reference

    @property
    def tenant_scope_fingerprint(self) -> str:
        return self.auth_context.tenant_scope_fingerprint

    def lifecycle_context(self, source_id: str) -> LifecycleAuthorizationContext:
        reference = self.audit_reference
        if not reference:
            raise AuthorizationDeniedError("Bounded operator audit reference required")
        return LifecycleAuthorizationContext(
            operator_reference=reference,
            tenant_scope_fingerprint=self.auth_context.tenant_scope_fingerprint,
            source_id=source_id,
            roles=self.auth_context.lifecycle_roles,
        )


class AdminDependencyAccess:
    """Typed fail-closed accessors for optional Admin dependencies."""

    def __init__(self, dependencies: AdminServiceDependencies) -> None:
        self._deps = dependencies

    def catalog(self) -> MetadataCatalogPort:
        catalog = self._deps.catalog
        if catalog is None:
            raise NotFoundError("catalog")
        return catalog

    def discoverer(self) -> MetadataDiscoverer:
        discoverer = self._deps.discoverer
        if discoverer is None:
            raise DiscoveryError("No metadata discoverer configured")
        return discoverer

    def draft_store(self) -> AssemblyDraftStore:
        store = self._deps.draft_store
        if store is None:
            raise NotFoundError("assembly draft store")
        return store

    def lifecycle_authorizer(self) -> LifecycleAuthorizer:
        authorizer = self._deps.lifecycle_authorizer
        if authorizer is None:
            raise AuthorizationDeniedError("Lifecycle authorizer not configured")
        return authorizer

    def lifecycle_catalog(self) -> LifecycleCatalogPort:
        catalog = self._deps.lifecycle_catalog
        if catalog is None:
            raise NotFoundError("lifecycle catalog")
        return catalog

    def bundle_emitter(self) -> SemanticBundleEmitter:
        emitter = self._deps.bundle_emitter
        if emitter is None:
            raise NotFoundError("semantic bundle emitter")
        return emitter

    def manifest_verifier(self) -> ManifestBundleVerifier:
        verifier = self._deps.manifest_verifier
        if verifier is None:
            raise NotFoundError("manifest verifier")
        return verifier

    def verification_executor(self) -> RunnableVerificationExecutor:
        executor = self._deps.verification_executor
        if executor is None:
            raise ValidationError("Verification executor is not configured")
        return executor

    def verification_context_factory(self) -> VerificationExecutionContextFactory:
        factory = self._deps.verification_context_factory
        if factory is None:
            raise ValidationError("Verification context factory is not configured")
        return factory


def normalize_errors(method: _F) -> _F:
    """Convert unexpected sync failures into normalized, bounded service errors."""

    @functools.wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except AdminServiceError:
            raise
        except DraftRevisionConflict as err:
            raise ConflictError("Draft revision conflict") from err
        except LifecycleAuthorizationError as err:
            raise AuthorizationDeniedError("Lifecycle authorization denied") from err
        except ValueError as err:
            raise ValidationError((str(err) or "invalid request")[:256]) from err
        except Exception:
            raise AdminServiceError(
                category=ErrorCategory.INTERNAL,
                code="internal_service_error",
                message="Internal service error",
            ) from None

    return cast(_F, wrapper)


def normalize_async_errors(method: _F) -> _F:
    """Convert unexpected async failures into normalized, bounded service errors."""

    @functools.wraps(method)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await method(*args, **kwargs)
        except AdminServiceError:
            raise
        except DraftRevisionConflict as err:
            raise ConflictError("Draft revision conflict") from err
        except LifecycleAuthorizationError as err:
            raise AuthorizationDeniedError("Lifecycle authorization denied") from err
        except ValueError as err:
            raise ValidationError((str(err) or "invalid request")[:256]) from err
        except Exception:
            raise AdminServiceError(
                category=ErrorCategory.INTERNAL,
                code="internal_service_error",
                message="Internal service error",
            ) from None

    return cast(_F, wrapper)


def require_permission(auth_context: AuthContext, permission: Permission) -> None:
    if not auth_context.is_allowed(permission):
        raise AuthorizationDeniedError(f"Missing permission: {permission.value}")


def require_source(auth_context: AuthContext, source_id: str) -> None:
    if not auth_context.is_source_allowed(source_id):
        raise AuthorizationDeniedError("Source not authorized")


def request_context(
    auth_context: AuthContext,
    dependencies: AdminServiceDependencies,
) -> AdminRequestContext:
    return AdminRequestContext(
        auth_context=auth_context,
        host_audit_reference=dependencies.audit_reference,
    )


def lifecycle_context(
    dependencies: AdminServiceDependencies,
    auth_context: AuthContext,
    source_id: str,
) -> LifecycleAuthorizationContext:
    return request_context(auth_context, dependencies).lifecycle_context(source_id)


def load_draft(
    access: AdminDependencyAccess,
    draft_id: str,
    auth_context: AuthContext,
) -> AssemblyDraft:
    draft = access.draft_store().get(
        draft_id,
        tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
    )
    if draft is None:
        raise NotFoundError("assembly draft")
    require_source(auth_context, draft.source_id)
    return draft


def store_draft(
    access: AdminDependencyAccess,
    draft: AssemblyDraft,
    *,
    expected_revision: int,
    auth_context: AuthContext,
) -> None:
    access.draft_store().replace(
        draft,
        expected_revision=expected_revision,
        tenant_scope_fingerprint=auth_context.tenant_scope_fingerprint,
    )


def page_window(
    pagination: PaginationParams | None,
    *,
    max_page_size: int,
) -> tuple[PaginationParams, int, int]:
    resolved = pagination or PaginationParams()
    page_size = min(resolved.page_size, max_page_size)
    return resolved, page_size, resolved.offset


def make_job(command: str, status: JobStatus, result_fingerprint: str | None = None) -> JobInfo:
    return JobInfo(
        job_id="sync",
        status=status,
        command=command,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        result_fingerprint=result_fingerprint,
    )


def job_record_to_info(record: JobRecord) -> JobInfo:
    return JobInfo(
        job_id=record.job_id,
        status=JobStatus(record.status),
        command=record.command,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
