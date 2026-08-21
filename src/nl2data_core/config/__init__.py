"""Configuration foundation: versioned strict configuration models and loading."""

from .loader import load_config
from .models import (
    SUPPORTED_SCHEMA_VERSION,
    ConfigurationError,
    EffectiveConfig,
    ExtensionSection,
    RuntimeSettings,
    SecretReference,
    ServiceIdentity,
)

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "ConfigurationError",
    "EffectiveConfig",
    "ExtensionSection",
    "RuntimeSettings",
    "SecretReference",
    "ServiceIdentity",
    "load_config",
]
