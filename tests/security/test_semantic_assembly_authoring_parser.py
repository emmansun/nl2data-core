"""Adversarial tests for bounded semantic authoring YAML parsing."""

from __future__ import annotations

import json

import pytest

import nl2data_core.assembly.authoring.loader as authoring_loader
from nl2data_core.assembly.authoring import SemanticAssemblyAuthoringLoader
from nl2data_core.assembly.authoring.loader import (
    MAX_AUTHORING_ALIASES,
    MAX_AUTHORING_DEPTH,
    MAX_AUTHORING_SCALAR_CHARS,
)


def valid_yaml(*, source_id: str = "warehouse") -> str:
    return f"""apiVersion: nl2data.io/semantic-assembly-authoring/v1alpha1
kind: SemanticAssembly
metadata:
  bundleId: sales
  modelVersion: 1.0.0
spec:
  source:
    sourceId: {source_id}
  entities:
    - entityId: orders
      label: Orders
"""


@pytest.mark.parametrize(
    "payload",
    [
        valid_yaml(),
        json.dumps(
            json.loads(
                json.dumps(
                    {
                        "apiVersion": "nl2data.io/semantic-assembly-authoring/v1alpha1",
                        "kind": "SemanticAssembly",
                        "metadata": {"bundleId": "sales", "modelVersion": "1.0.0"},
                        "spec": {
                            "source": {"sourceId": "warehouse"},
                            "entities": [{"entityId": "orders", "label": "Orders"}],
                        },
                    }
                )
            )
        ),
    ],
)
def test_valid_yaml_and_json_load(payload: str) -> None:
    assert SemanticAssemblyAuthoringLoader().load(payload).loaded


def test_comments_and_bounded_aliases_are_semantically_resolved() -> None:
    payload = (
        valid_yaml().replace(
            "sourceId: warehouse",
            "sourceId: &source warehouse # reusable logical source",
        )
        + "  sourceReferences:\n    - referenceId: primary\n      sourceId: *source\n"
    )
    result = SemanticAssemblyAuthoringLoader().load(payload)
    assert result.loaded
    assert result.model is not None
    assert result.model.spec.source_references[0].source_id == "warehouse"


@pytest.mark.parametrize(
    ("fragment", "code"),
    [
        ("metadata:\n  bundleId: sales\n  bundleId: hidden", "unsupported_yaml"),
        ("metadata: {<<: {bundleId: sales}, modelVersion: 1.0.0}", "unsupported_yaml"),
        ("metadata: !!python/object:builtins.dict {}", "unsupported_yaml"),
        ("metadata: {1: value}", "unsupported_yaml"),
        ("metadata: !!timestamp 2026-01-01", "unsupported_yaml"),
        ("metadata: .nan", "unsupported_yaml"),
    ],
)
def test_dangerous_yaml_features_are_rejected(fragment: str, code: str) -> None:
    lines = valid_yaml().splitlines()
    start = lines.index("metadata:")
    payload = "\n".join(lines[:start] + fragment.splitlines() + lines[start + 3 :])
    result = SemanticAssemblyAuthoringLoader().load(payload)
    assert not result.loaded
    assert result.diagnostics[0].code == code


@pytest.mark.parametrize(
    "value",
    ["!!bool yes", "!!int 0x10", "!!float '.nan'", "!!null Null"],
)
def test_explicit_typed_scalars_require_json_compatible_spelling(value: str) -> None:
    result = SemanticAssemblyAuthoringLoader().load(valid_yaml(source_id=value))
    assert result.diagnostics[0].code == "unsupported_yaml"


def test_cyclic_alias_is_rejected_before_construction() -> None:
    payload = valid_yaml() + "cycle: &cycle [*cycle]\n"
    result = SemanticAssemblyAuthoringLoader().load(payload)
    assert result.diagnostics[0].code == "unsupported_yaml"


def test_alias_count_is_bounded() -> None:
    payload = (
        valid_yaml() + "aliases: [" + ", ".join(["*source"] * (MAX_AUTHORING_ALIASES + 1)) + "]\n"
    )
    payload = payload.replace("sourceId: warehouse", "sourceId: &source warehouse")
    result = SemanticAssemblyAuthoringLoader().load(payload)
    assert result.diagnostics[0].code == "structure_limit"


def test_depth_scalar_and_byte_bounds_fail_closed() -> None:
    loader = SemanticAssemblyAuthoringLoader()
    deep = (
        valid_yaml()
        + "extra: "
        + "[" * (MAX_AUTHORING_DEPTH + 1)
        + "null"
        + "]" * (MAX_AUTHORING_DEPTH + 1)
    )
    assert loader.load(deep).diagnostics[0].code == "structure_limit"
    scalar = valid_yaml(source_id="x" * (MAX_AUTHORING_SCALAR_CHARS + 1))
    assert loader.load(scalar).diagnostics[0].code == "structure_limit"
    oversized = valid_yaml() + "#" + "x" * 1_048_576
    assert loader.load(oversized).diagnostics[0].code == "input_too_large"


def test_event_node_collection_and_alias_expansion_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    loader = SemanticAssemblyAuthoringLoader()
    monkeypatch.setattr(authoring_loader, "MAX_AUTHORING_EVENTS", 10)
    assert loader.load(valid_yaml()).diagnostics[0].code == "structure_limit"
    monkeypatch.setattr(authoring_loader, "MAX_AUTHORING_EVENTS", 65_536)

    monkeypatch.setattr(authoring_loader, "MAX_AUTHORING_NODES", 10)
    assert loader.load(valid_yaml()).diagnostics[0].code == "structure_limit"
    monkeypatch.setattr(authoring_loader, "MAX_AUTHORING_NODES", 32_768)

    monkeypatch.setattr(authoring_loader, "MAX_AUTHORING_COLLECTION_ITEMS", 1)
    assert loader.load(valid_yaml()).diagnostics[0].code == "structure_limit"
    monkeypatch.setattr(authoring_loader, "MAX_AUTHORING_COLLECTION_ITEMS", 16_384)

    monkeypatch.setattr(authoring_loader, "MAX_AUTHORING_EXPANDED_NODES", 10)
    aliased = valid_yaml().replace("sourceId: warehouse", "sourceId: &source warehouse")
    aliased += "aliases: [*source, *source, *source]\n"
    assert loader.load(aliased).diagnostics[0].code == "structure_limit"


def test_malformed_unicode_is_rejected() -> None:
    result = SemanticAssemblyAuthoringLoader().load(valid_yaml() + "\ud800")
    assert result.diagnostics[0].code == "invalid_encoding"


@pytest.mark.parametrize("value", ["yes", "on", "2026-08-31"])
def test_yaml_11_and_timestamp_like_scalars_remain_strings(value: str) -> None:
    result = SemanticAssemblyAuthoringLoader().load(valid_yaml(source_id=value))
    assert result.loaded
    assert result.model is not None
    assert result.model.spec.source.source_id == value


def test_malformed_yaml_has_controlled_diagnostic() -> None:
    result = SemanticAssemblyAuthoringLoader().load("apiVersion: [")
    assert result.diagnostics[0].code == "invalid_yaml"
    assert "expected" not in result.diagnostics[0].message.lower()


def test_model_diagnostics_are_located_truncated_and_secret_redacted() -> None:
    payload = valid_yaml() + "secret-token-value: rejected\n"
    result = SemanticAssemblyAuthoringLoader().load(payload)
    diagnostic = result.diagnostics[0]
    serialized = result.model_dump_json()
    assert diagnostic.path.render() == "$.member"
    assert diagnostic.mark is not None
    assert diagnostic.mark.line == 12
    assert result.issue_count == 1
    assert not result.truncated
    assert "secret-token-value" not in diagnostic.message
    assert "secret-token-value" not in serialized
    assert "rejected" not in serialized


def test_oversized_unknown_key_is_redacted_to_a_bounded_path() -> None:
    unsafe_key = "x" * 2_000
    result = SemanticAssemblyAuthoringLoader().load(
        valid_yaml() + '"' + unsafe_key + '": value\n'
    )
    rendered = result.diagnostics[0].path.render()
    assert len(rendered) <= 1_024
    assert unsafe_key not in rendered


def test_diagnostics_are_bounded_and_report_truncation() -> None:
    extras = "".join(f"unknown{index}: value\n" for index in range(101))
    result = SemanticAssemblyAuthoringLoader().load(valid_yaml() + extras)
    assert len(result.diagnostics) == 100
    assert result.issue_count == 101
    assert result.truncated
