"""Plugin manifest and registry foundation (declarative, non-executing)."""

from .models import (
    Compatibility,
    PluginActivationStatus,
    PluginCapability,
    PluginDescriptor,
    PluginIdentity,
    PluginManifest,
    PluginManifestError,
)
from .registry import PluginRegistry, validate_manifest, version_in_range

__all__ = [
    "Compatibility",
    "PluginActivationStatus",
    "PluginCapability",
    "PluginDescriptor",
    "PluginIdentity",
    "PluginManifest",
    "PluginManifestError",
    "PluginRegistry",
    "validate_manifest",
    "version_in_range",
]
