"""Strict configuration loading and activation.

Loading fails closed: unsupported schema versions, unknown strict fields,
malformed values and protected overrides are rejected before any snapshot
is activated.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from nl2data.errors import ErrorCode
from nl2data_core.ai.config import ModelConfig
from nl2data_core.canonical import strict_sha256_fingerprint

from .models import (
    SUPPORTED_SCHEMA_VERSION,
    ConfigurationError,
    EffectiveConfig,
    ExtensionSection,
    RuntimeSettings,
    SecretReference,
    ServiceIdentity,
)

#: Core section names that cannot be overridden through extensions.
_PROTECTED_SECTION_NAMES = frozenset(
    {"schema_version", "service", "runtime", "secrets", "extensions", "model", "fingerprint"}
)
_CONFIGURATION_FIELD_NAMES = frozenset(
    {"schema_version", "service", "runtime", "secrets", "extensions", "model"}
)


def _as_configuration_error(exc: Exception, message: str, *, code: ErrorCode) -> ConfigurationError:
    details = {"cause_type": type(exc).__name__}
    return ConfigurationError(code, message, details=details)


def _secret_reference(value: Any, path: str) -> SecretReference:
    """Validate a secrets-section entry, rejecting plaintext protected values."""
    if isinstance(value, SecretReference):
        return value
    if isinstance(value, Mapping):
        if "env" in value and set(value) <= {"env"}:
            name = value["env"]
            if isinstance(name, str):
                return SecretReference(kind="env", name=name)
        if "kind" in value and "name" in value and set(value) <= {"kind", "name"}:
            return SecretReference.model_validate(value)
        raise ConfigurationError(
            ErrorCode.PROTECTED_FIELD_OVERRIDE,
            f"malformed secret reference at '{path}'",
            details={"path": path},
        )
    raise ConfigurationError(
        ErrorCode.PROTECTED_FIELD_OVERRIDE,
        f"plaintext secret value at '{path}' is not allowed; use a secret reference",
        details={"path": path},
    )


def _validation_details(exc: Exception, prefix: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    raw = getattr(exc, "errors", None)
    if callable(raw):
        for item in raw() or []:
            loc = ".".join(str(part) for part in item.get("loc", ()))
            if prefix:
                loc = f"{prefix}.{loc}" if loc else prefix
            errors.append(f"{loc}: {item.get('msg', 'invalid value')}")
    return {"errors": errors[:32]}


def _validate_section(section: str, model: type[Any], data: Any) -> Any:
    """Validate one strict core section, reporting field paths with context."""
    try:
        return model.model_validate(data)
    except Exception as exc:
        raise ConfigurationError(
            ErrorCode.MALFORMED_CONFIGURATION,
            f"invalid '{section}' configuration section",
            details=_validation_details(exc, prefix=section),
        ) from exc


def load_config(source: Mapping[str, Any] | str) -> EffectiveConfig:
    """Compile a mapping or YAML document into an immutable effective snapshot.

    Raises :class:`ConfigurationError` (non-retryable) on any failure.
    """
    if isinstance(source, str):
        try:
            parsed = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise _as_configuration_error(
                exc,
                "configuration document is not valid YAML",
                code=ErrorCode.MALFORMED_CONFIGURATION,
            ) from exc
    else:
        parsed = source

    if not isinstance(parsed, Mapping):
        raise ConfigurationError(
            ErrorCode.MALFORMED_CONFIGURATION,
            "configuration document must be a mapping",
            details={"found": type(parsed).__name__},
        )

    unknown_fields = [key for key in parsed if key not in _CONFIGURATION_FIELD_NAMES]
    if unknown_fields:
        field = str(unknown_fields[0])
        raise ConfigurationError(
            ErrorCode.MALFORMED_CONFIGURATION,
            f"unknown configuration field '{field}'",
            details={"path": field},
        )

    raw_version = parsed.get("schema_version")
    if raw_version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigurationError(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"unsupported configuration schema version: {raw_version!r}",
            details={"supported": SUPPORTED_SCHEMA_VERSION, "found": str(raw_version)},
        )

    try:
        service = _validate_section("service", ServiceIdentity, parsed.get("service", {}))
        runtime = _validate_section("runtime", RuntimeSettings, parsed.get("runtime", {}))

        model: ModelConfig | None = None
        raw_model = parsed.get("model")
        if raw_model is not None:
            model = _validate_section("model", ModelConfig, raw_model)

        secrets: dict[str, SecretReference] = {}
        raw_secrets = parsed.get("secrets", {})
        if not isinstance(raw_secrets, Mapping):
            raise ConfigurationError(
                ErrorCode.MALFORMED_CONFIGURATION,
                "'secrets' must be a mapping of secret references",
            )
        for name, value in raw_secrets.items():
            secrets[str(name)] = _secret_reference(value, f"secrets.{name}")

        extensions: dict[str, ExtensionSection] = {}
        raw_extensions = parsed.get("extensions", {})
        if not isinstance(raw_extensions, Mapping):
            raise ConfigurationError(
                ErrorCode.MALFORMED_CONFIGURATION,
                "'extensions' must be a mapping of sections",
            )
        for name, value in raw_extensions.items():
            section_name = str(name)
            if section_name in _PROTECTED_SECTION_NAMES:
                raise ConfigurationError(
                    ErrorCode.PROTECTED_FIELD_OVERRIDE,
                    f"protected core section '{section_name}' cannot be overridden",
                    details={"path": f"extensions.{section_name}"},
                )
            if not isinstance(value, Mapping):
                raise ConfigurationError(
                    ErrorCode.MALFORMED_CONFIGURATION,
                    f"extension section '{section_name}' must be a mapping of scalars",
                    details={"path": f"extensions.{section_name}"},
                )
            extensions[section_name] = _validate_section(
                f"extensions.{section_name}", ExtensionSection, {"values": value}
            )
    except ConfigurationError:
        raise
    except Exception as exc:  # pydantic validation failures fail closed
        raise ConfigurationError(
            ErrorCode.MALFORMED_CONFIGURATION,
            "configuration contains invalid or unknown fields",
            details=_validation_details(exc),
        ) from exc

    payload = {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "service": service.model_dump(),
        "runtime": runtime.model_dump(),
        "secrets": {
            name: {"kind": ref.kind, "name": ref.name} for name, ref in sorted(secrets.items())
        },
        "extensions": {name: section.values for name, section in sorted(extensions.items())},
        "model": model.safe_payload() if model is not None else None,
    }
    fingerprint = strict_sha256_fingerprint(payload)
    return EffectiveConfig(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        service=service,
        runtime=runtime,
        secrets=secrets,
        extensions=extensions,
        model=model,
        fingerprint=fingerprint,
    )
