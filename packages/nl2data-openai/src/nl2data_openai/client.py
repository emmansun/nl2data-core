"""Lazy optional OpenAI SDK boundary for the provider package.

The ``openai`` package is loaded only inside this module through
:func:`importlib.import_module`, so importing ``nl2data_openai``, the core,
or the provider never imports the SDK.  Client construction happens lazily
on first generation; no import-time or capability-time network access
exists.  Error predicates duck-type by class name so injected fake clients
raise structurally identical errors without the SDK installed.
"""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec
from typing import Any

from nl2data_core.ai.errors import ModelErrorCode, ModelInvocationError

from .config import OpenAIProviderConfig


def driver_available() -> bool:
    """Whether the optional ``openai`` SDK is installed."""
    return find_spec("openai") is not None


def build_openai_client(config: OpenAIProviderConfig, *, api_key: str) -> Any:
    """Lazily import the SDK and build a bounded ``AsyncOpenAI`` client.

    Raises a normalized ``PROVIDER_UNAVAILABLE`` error when the SDK is
    missing or the client cannot be constructed; the key and any driver
    exception text never enter the error.
    """
    if not driver_available():
        raise ModelInvocationError(
            ModelErrorCode.PROVIDER_UNAVAILABLE,
            "the openai SDK is not installed; install the 'nl2data-openai' package",
            details={"cause_type": "ImportError"},
        )
    try:
        openai = import_module("openai")
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": config.timeout_seconds}
        if config.base_url is not None:
            kwargs["base_url"] = config.base_url
        if config.organization is not None:
            kwargs["organization"] = config.organization
        return openai.AsyncOpenAI(**kwargs)
    except ModelInvocationError:
        raise
    except Exception as error:
        raise ModelInvocationError(
            ModelErrorCode.PROVIDER_UNAVAILABLE,
            "the openai client could not be constructed",
            details={"cause_type": type(error).__name__},
        ) from error


def _class_name(error: BaseException) -> str:
    return error.__class__.__name__


def is_timeout_error(error: BaseException) -> bool:
    """SDK or builtin timeout signals (duck-typed by class name)."""
    if isinstance(error, TimeoutError):
        return True
    return _class_name(error) == "APITimeoutError"


def is_connection_error(error: BaseException) -> bool:
    """Connection failure signals (duck-typed by class name)."""
    if isinstance(error, ConnectionError):
        return True
    return _class_name(error) in {"APIConnectionError", "APIConnectionPoolTimeoutError"}


def is_rate_limit_error(error: BaseException) -> bool:
    """Rate-limit signals (duck-typed by class name)."""
    return _class_name(error) == "RateLimitError"


def is_authentication_error(error: BaseException) -> bool:
    """Credential-rejection signals (duck-typed by class name)."""
    return _class_name(error) in {"AuthenticationError", "PermissionDeniedError"}


def is_status_error(error: BaseException) -> bool:
    """Any SDK status error exposing an HTTP status code (duck-typed)."""
    return _class_name(error) == "APIStatusError" or hasattr(error, "status_code")
