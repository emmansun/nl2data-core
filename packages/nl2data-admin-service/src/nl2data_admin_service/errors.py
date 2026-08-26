"""Normalized admin service errors."""

from __future__ import annotations

from .dtos import ErrorCategory, ErrorDetail


class AdminServiceError(Exception):
    """Base error for the admin service."""

    def __init__(
        self,
        *,
        category: ErrorCategory,
        code: str,
        message: str,
        details: tuple[ErrorDetail, ...] = (),
    ) -> None:
        super().__init__(message)
        self.category = category
        self.code = code
        self.message = message
        self.details = details

    def to_detail(self) -> ErrorDetail:
        return ErrorDetail(code=self.code, message=self.message)


class AuthenticationRequiredError(AdminServiceError):
    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(
            category=ErrorCategory.AUTHENTICATION,
            code="authentication_required",
            message=message,
        )


class AuthorizationDeniedError(AdminServiceError):
    def __init__(self, message: str = "Authorization denied") -> None:
        super().__init__(
            category=ErrorCategory.AUTHORIZATION,
            code="authorization_denied",
            message=message,
        )


class NotFoundError(AdminServiceError):
    def __init__(self, resource: str) -> None:
        super().__init__(
            category=ErrorCategory.NOT_FOUND,
            code="not_found",
            message=f"{resource} not found",
        )


class ValidationError(AdminServiceError):
    def __init__(self, message: str, details: tuple[ErrorDetail, ...] = ()) -> None:
        super().__init__(
            category=ErrorCategory.VALIDATION,
            code="validation_error",
            message=message,
            details=details,
        )


class ConflictError(AdminServiceError):
    def __init__(self, message: str = "Conflict") -> None:
        super().__init__(
            category=ErrorCategory.CONFLICT,
            code="conflict",
            message=message,
        )


class DiscoveryError(AdminServiceError):
    def __init__(self, message: str) -> None:
        super().__init__(
            category=ErrorCategory.DISCOVERY,
            code="discovery_failed",
            message=message,
        )


class BundleError(AdminServiceError):
    def __init__(self, message: str, details: tuple[ErrorDetail, ...] = ()) -> None:
        super().__init__(
            category=ErrorCategory.BUNDLE,
            code="bundle_operation_failed",
            message=message,
            details=details,
        )
