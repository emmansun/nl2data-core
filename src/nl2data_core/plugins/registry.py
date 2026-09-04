"""Declarative plugin registry with immutable generations.

Registration validates manifests and stores immutable descriptors; it never
imports, installs, or executes plugin code.
"""

from __future__ import annotations

import re
from typing import Any

from nl2data_core.canonical import strict_sha256_fingerprint

from .models import (
    PluginActivationStatus,
    PluginDescriptor,
    PluginManifest,
    PluginManifestError,
)

_VERSION_RE = re.compile(r"^(==|>=|<=|>|<)?\s*(\d+)\.(\d+)\.(\d+)$")
_PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def _parse_version(version: str, where: str) -> tuple[int, int, int]:
    match = _VERSION_RE.fullmatch(version.strip())
    if not match:
        raise PluginManifestError(
            f"invalid version '{version}' in {where}",
            details={"where": where},
        )
    return (int(match.group(2)), int(match.group(3)), int(match.group(4)))


def _parse_range(range_spec: str, where: str) -> tuple[str, tuple[int, int, int]] | None:
    """Parse a simple range spec; ``*`` returns ``None``."""
    spec = range_spec.strip()
    if spec == "*":
        return None
    match = _VERSION_RE.fullmatch(spec)
    if not match:
        raise PluginManifestError(
            f"malformed version range '{range_spec}' in {where}",
            details={"range": range_spec, "where": where},
        )
    operator = match.group(1) or "=="
    target = (int(match.group(2)), int(match.group(3)), int(match.group(4)))
    return operator, target


def version_in_range(version: str, range_spec: str) -> bool:
    """Check whether a ``x.y.z`` version satisfies a simple range spec.

    Supported forms: ``x.y.z`` / ``==x.y.z`` / ``>=x.y.z`` / ``<=x.y.z`` /
    ``>x.y.z`` / ``<x.y.z`` / ``*``.  Malformed specs fail closed by raising.
    """
    parsed = _parse_range(range_spec, "version comparison")
    if parsed is None:
        return True
    operator, target = parsed
    actual = _parse_version(version, "version comparison")
    if operator == "==":
        return actual == target
    if operator == ">=":
        return actual >= target
    if operator == "<=":
        return actual <= target
    if operator == ">":
        return actual > target
    return actual < target


def _validate_permissions(permissions: Any) -> None:
    if not isinstance(permissions, (frozenset, set, tuple, list)):
        raise PluginManifestError("permissions must be a set of dotted names")
    for permission in permissions:
        if not isinstance(permission, str) or not _PERMISSION_PATTERN.fullmatch(permission):
            raise PluginManifestError(
                f"malformed permission '{permission}'",
                details={"permission": str(permission)},
            )


def validate_manifest(manifest: PluginManifest) -> None:
    """Semantic validation of a manifest before registration (fail closed)."""
    if manifest.schema_version != 1:
        raise PluginManifestError(
            f"unsupported manifest schema version {manifest.schema_version}",
            details={"schema_version": manifest.schema_version},
        )
    _validate_permissions(manifest.permissions)
    _parse_range(manifest.compatibility.core_version_range, "compatibility.core_version_range")
    for name, contract_range in manifest.compatibility.adapter_contracts.items():
        _parse_range(contract_range, f"adapter contract '{name}'")
    capability_names: set[str] = set()
    for capability in manifest.capabilities:
        if capability.name in capability_names:
            raise PluginManifestError(
                f"duplicate capability '{capability.name}'",
                details={"capability": capability.name},
            )
        capability_names.add(capability.name)
        _parse_version(capability.contract_version, f"capability {capability.name}")


class PluginRegistry:
    """Immutable registry generation over validated plugin descriptors.

    Every registration returns a new generation; existing generations are
    never mutated.
    """

    def __init__(
        self,
        generation: int = 0,
        descriptors: tuple[PluginDescriptor, ...] = (),
    ) -> None:
        if generation < 0:
            raise ValueError("generation must be non-negative")
        self._generation = generation
        self._descriptors = descriptors

    @property
    def generation(self) -> int:
        return self._generation

    def descriptors(self) -> tuple[PluginDescriptor, ...]:
        """Immutable view of all registered descriptors."""
        return self._descriptors

    def register(self, manifest: PluginManifest) -> PluginRegistry:
        """Return a new registry generation with the validated descriptor appended.

        Raises :class:`PluginManifestError` before activation on any
        invalid or duplicate declaration.
        """
        validate_manifest(manifest)
        for existing in self._descriptors:
            if existing.identity.name == manifest.identity.name:
                raise PluginManifestError(
                    f"plugin '{manifest.identity.name}' is already registered",
                    details={"name": manifest.identity.name},
                )
        # Frozenset members carry as sorted arrays so strict
        # canonicalization stays fail-closed and deterministic.
        fingerprint = strict_sha256_fingerprint(
            {
                **manifest.model_dump(mode="json"),
                "categories": sorted(manifest.categories),
                "permissions": sorted(manifest.permissions),
            }
        )
        descriptor = PluginDescriptor(
            manifest_fingerprint=fingerprint,
            identity=manifest.identity,
            capabilities=manifest.capabilities,
            granted_permissions=manifest.permissions,
            activation_status=PluginActivationStatus.INACTIVE,
            entry_point=manifest.entry_point,
        )
        return PluginRegistry(
            generation=self._generation + 1,
            descriptors=self._descriptors + (descriptor,),
        )

    def resolve(
        self,
        capability: str,
        contract_range: str,
        *,
        required_permission: str | None = None,
    ) -> PluginDescriptor | None:
        """Resolve a compatible plugin by capability and version range.

        Fails closed: returns ``None`` when no declared capability satisfies
        the contract range or when the required permission is absent.
        """
        for descriptor in self._descriptors:
            if descriptor.activation_status == PluginActivationStatus.BLOCKED:
                continue
            if (
                required_permission is not None
                and required_permission not in descriptor.granted_permissions
            ):
                continue
            for declared in descriptor.capabilities:
                if declared.name != capability:
                    continue
                try:
                    if version_in_range(declared.contract_version, contract_range):
                        return descriptor
                except PluginManifestError:
                    continue
        return None

    def descriptor_ids(self) -> tuple[str, ...]:
        """Deterministic public identifiers of registered plugins."""
        return tuple(descriptor.public_id() for descriptor in self._descriptors)
