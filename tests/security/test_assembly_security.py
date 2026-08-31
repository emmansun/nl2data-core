"""Security tests for assembly deployment bindings and secret handling."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from nl2data_core.assembly import (
    ASSEMBLY_API_VERSION,
    AssemblyDraft,
    AssertionProvenance,
    AssertionType,
    DeploymentBinding,
    SemanticAssertion,
    verify_deployment_binding,
)
from nl2data_core.bundles import DeploymentBindingRedactionSummary
from nl2data_core.canonical import sha256_fingerprint


def binding(reference: str) -> DeploymentBinding:
    return DeploymentBinding(
        binding_id="production",
        environment="production",
        source_id="sales",
        connection_reference=reference,
    )


@pytest.mark.parametrize(
    "reference",
    [
        "postgres://user:pass@host/db",
        "env:password=cleartext",
        "vault:token=cleartext",
        "secret-value-without-scheme",
        "https://vault.example/secret",
    ],
)
def test_inline_credentials_and_unsupported_references_are_rejected(
    reference: str,
) -> None:
    with pytest.raises(ValidationError):
        binding(reference)


@pytest.mark.parametrize(
    "reference",
    ["env:SALES_DSN", "vault:secret/data/sales", "file:C:\\secrets\\sales"],
)
def test_safe_reference_forms_are_accepted(reference: str) -> None:
    assert binding(reference).reference_scheme in {"env", "vault", "file"}


def test_resolved_secret_is_not_returned_or_persisted() -> None:
    deployment = binding("env:SALES_DSN")
    resolver = type("Resolver", (), {"resolve": lambda self, item: "resolved-secret"})()
    verifier = type(
        "Verifier",
        (),
        {"verify": lambda self, item, secret: secret == "resolved-secret"},
    )()
    result = verify_deployment_binding(
        deployment,
        resolver=resolver,
        verifier=verifier,
    )
    assert result.valid
    assert "resolved-secret" not in result.model_dump_json()
    assert "resolved-secret" not in deployment.model_dump_json()


def test_audit_summary_contains_no_binding_name_reference_or_secret() -> None:
    summary = DeploymentBindingRedactionSummary(
        binding_count=2,
        reference_schemes=frozenset({"env", "vault"}),
    )
    serialized = json.dumps(summary.model_dump(mode="json"))
    assert serialized == '{"binding_count": 2, "reference_schemes": ["env", "vault"]}'
    for forbidden in ("SALES_DSN", "secret/data", "password", "token"):
        assert forbidden not in serialized


def test_deployment_binding_changes_do_not_change_semantic_fingerprint() -> None:
    assertion = SemanticAssertion.create(
        type=AssertionType.ENTITY,
        payload={"descriptor_id": "sales", "entity_id": "orders"},
        provenance=AssertionProvenance(kind="manual"),
    )
    common = {
        "apiVersion": ASSEMBLY_API_VERSION,
        "draft_id": "draft-1",
        "bundle_id": "sales",
        "source_id": "sales",
        "model_version": "1.0.0",
        "assertions": (assertion,),
        "author_reference": "author-1",
    }
    env_draft = AssemblyDraft(
        **common,
        deployment_bindings=(binding("env:SALES_DSN"),),
    )
    vault_draft = AssemblyDraft(
        **common,
        deployment_bindings=(binding("vault:secret/data/sales"),),
    )

    def semantic_fingerprint(draft: AssemblyDraft) -> str:
        return sha256_fingerprint(
            [item.canonical_payload() for item in draft.assertions]
        )

    assert semantic_fingerprint(env_draft) == semantic_fingerprint(vault_draft)