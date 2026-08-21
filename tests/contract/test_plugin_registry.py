"""Contract tests for declarative plugin registration and resolution."""

from __future__ import annotations

import sys

import pytest
from pydantic import ValidationError

from nl2data_core.plugins.models import (
    Compatibility,
    PluginCapability,
    PluginDescriptor,
    PluginIdentity,
    PluginManifest,
    PluginManifestError,
)
from nl2data_core.plugins.registry import PluginRegistry, version_in_range

DIGEST = "sha256:" + "ef" * 32


def make_manifest(**overrides) -> PluginManifest:
    defaults: dict = {
        "identity": PluginIdentity(name="demo", version="1.2.3", package="demo_plugin"),
        "entry_point": "demo_plugin.adapter",
        "categories": frozenset({"adapter"}),
        "capabilities": (PluginCapability(name="query", contract_version="1.0.0"),),
        "permissions": frozenset({"query.execute"}),
        "compatibility": Compatibility(core_version_range=">=0.1.0"),
        "content_digest": DIGEST,
    }
    defaults.update(overrides)
    return PluginManifest(**defaults)


class TestManifestValidation:
    def test_invalid_manifest_is_rejected_before_activation(self) -> None:
        registry = PluginRegistry()
        # Shape-level failures (digest format) fail construction immediately.
        with pytest.raises(ValidationError):
            make_manifest(content_digest="md5:deadbeef")
        # Semantic failures are rejected by registration before activation.
        with pytest.raises(PluginManifestError):
            registry.register(make_manifest(permissions=frozenset({"UPPER::bad"})))
        with pytest.raises(PluginManifestError):
            registry.register(
                make_manifest(compatibility=Compatibility(core_version_range="not-a-range"))
            )
        # Unsupported schema versions fail at construction (shape-level).
        with pytest.raises(ValidationError):
            make_manifest(schema_version=2)

    def test_duplicate_plugin_is_rejected(self) -> None:
        registry = PluginRegistry().register(make_manifest())
        duplicate = make_manifest(
            identity=PluginIdentity(name="demo", version="2.0.0", package="demo_plugin")
        )
        with pytest.raises(PluginManifestError):
            registry.register(duplicate)

    def test_missing_identity_fails_construction(self) -> None:
        with pytest.raises(ValidationError):
            PluginManifest(entry_point="x.y", content_digest=DIGEST)  # type: ignore[call-arg]

    def test_version_range_comparisons(self) -> None:
        assert version_in_range("1.2.3", ">=1.0.0")
        assert not version_in_range("0.9.0", ">=1.0.0")
        assert version_in_range("1.2.3", "*")
        assert not version_in_range("1.2.3", "<1.0.0")
        with pytest.raises(PluginManifestError):
            version_in_range("1.2.3", "banana")


class TestImmutableDescriptors:
    def test_descriptor_cannot_be_mutated(self) -> None:
        registry = PluginRegistry().register(make_manifest())
        descriptor = registry.descriptors()[0]
        with pytest.raises(ValidationError):
            descriptor.granted_permissions = frozenset()  # type: ignore[misc]

    def test_registration_returns_new_generation(self) -> None:
        base = PluginRegistry()
        assert base.generation == 0
        next_gen = base.register(make_manifest())
        assert next_gen.generation == 1
        assert base.descriptors() == ()
        assert len(next_gen.descriptors()) == 1

    def test_descriptor_contains_resolved_declarative_data(self) -> None:
        registry = PluginRegistry().register(make_manifest())
        descriptor: PluginDescriptor = registry.descriptors()[0]
        assert descriptor.manifest_fingerprint.startswith("sha256:")
        assert descriptor.identity.name == "demo"
        assert descriptor.granted_permissions == frozenset({"query.execute"})
        assert descriptor.public_id() == "demo@1.2.3"


class TestCapabilityResolution:
    def test_resolves_compatible_capability(self) -> None:
        registry = PluginRegistry().register(make_manifest())
        resolved = registry.resolve("query", ">=1.0.0", required_permission="query.execute")
        assert resolved is not None
        assert resolved.identity.name == "demo"

    def test_incompatible_contract_range_is_not_resolved(self) -> None:
        registry = PluginRegistry().register(make_manifest())
        assert registry.resolve("query", ">=2.0.0") is None

    def test_missing_permission_fails_closed(self) -> None:
        registry = PluginRegistry().register(make_manifest())
        assert registry.resolve("query", "*", required_permission="schema.read") is None

    def test_unknown_capability_is_not_resolved(self) -> None:
        registry = PluginRegistry().register(make_manifest())
        assert registry.resolve("memory", "*") is None


class TestNoCodeExecution:
    def test_registration_never_invokes_entry_point(self) -> None:
        assert "demo_plugin" not in sys.modules
        PluginRegistry().register(make_manifest())
        assert "demo_plugin" not in sys.modules

    def test_registered_descriptor_stores_entry_point_as_data(self) -> None:
        registry = PluginRegistry().register(make_manifest())
        descriptor = registry.descriptors()[0]
        assert descriptor.entry_point == "demo_plugin.adapter"
