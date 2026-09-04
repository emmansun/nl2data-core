"""Authorized model-context assembly for provider invocation.

The context contains only bounded request data and policy-pruned semantic
references.  Credentials, native clients, raw result sets, unrestricted
schema metadata, and hidden policy state are never part of the context;
the provider payload is assembled from the safe projection only.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nl2data.models import QueryRequest
from nl2data_core.canonical import strict_sha256_fingerprint
from nl2data_core.planning.validation import AuthorizedView
from nl2data_core.views.projection import ResolvedViewProjection

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,127}$"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"

_MAX_PROMPT_CHARS = 100_000
_MAX_REFERENCES = 1_000
_MAX_OUTPUT_TOKENS = 131_072


class SemanticReference(BaseModel):
    """One policy-pruned semantic object authorized for the model context.

    Carries only bounded catalog labels; never physical DDL, driver
    objects, or credentials.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    label: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    data_type: str = Field(default="string", min_length=1, max_length=32)
    allowed_aggregations: frozenset[str] = Field(default_factory=frozenset)


class AuthorizedModelContext(BaseModel):
    """Immutable bounded context handed to a provider for one request.

    The fingerprint covers the semantic scope only (never the raw prompt,
    which travels separately in the invocation request).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    root_entity_ids: frozenset[str] = Field(default_factory=frozenset)
    semantic_references: tuple[SemanticReference, ...] = Field(
        default_factory=tuple, max_length=_MAX_REFERENCES
    )
    catalog_fingerprint: str | None = Field(default=None, pattern=_FINGERPRINT_PATTERN)
    max_output_tokens: int = Field(default=4096, ge=1, le=_MAX_OUTPUT_TOKENS)
    fingerprint: str = Field(default="", pattern=_FINGERPRINT_PATTERN)

    @field_validator("root_entity_ids")
    @classmethod
    def _valid_root_entities(cls, value: frozenset[str]) -> frozenset[str]:
        for entity_id in value:
            if len(entity_id) > 128 or not entity_id:
                raise ValueError(
                    "root entity ids must be non-empty and at most 128 characters"
                )
        return value

    @field_validator("semantic_references")
    @classmethod
    def _unique_references(
        cls, value: tuple[SemanticReference, ...]
    ) -> tuple[SemanticReference, ...]:
        ids = [reference.field_id for reference in value]
        if len(ids) != len(set(ids)):
            raise ValueError("semantic reference field ids must be unique")
        return value

    @model_validator(mode="after")
    def _compute_fingerprint(self) -> AuthorizedModelContext:
        fingerprint = strict_sha256_fingerprint(self.safe_payload())
        object.__setattr__(self, "fingerprint", fingerprint)
        return self

    def safe_payload(self) -> dict[str, Any]:
        """Provider-safe projection: no prompt, credentials, clients, or policy state.

        References are serialized in JSON mode so frozenset aggregations
        cross the wire boundary as plain lists.
        """
        return {
            "request_id": self.request_id,
            "source_id": self.source_id,
            "root_entity_ids": sorted(self.root_entity_ids),
            "semantic_references": [
                reference.model_dump(mode="json") for reference in self.semantic_references
            ],
            "catalog_fingerprint": self.catalog_fingerprint,
            "max_output_tokens": self.max_output_tokens,
        }


def assemble_model_context(
    *,
    request: QueryRequest,
    view: AuthorizedView,
    semantic_references: dict[str, SemanticReference] | None = None,
    max_output_tokens: int = 4096,
    projection: ResolvedViewProjection | None = None,
) -> AuthorizedModelContext:
    """Assemble the authorized model context for one query request.

    When a resolved projection is available the context is assembled from
    the projection only: references are derived from its permitted members
    (alias, label, description, data type, and allowed aggregations), so
    physical metadata, credentials, restricted members, and hidden policy
    details never enter the provider context.  Without a projection the
    context falls back to policy-pruned references, dropping any field
    outside ``view.field_ids``.
    """
    if projection is not None:
        references = tuple(
            SemanticReference(
                field_id=field.field_id,
                label=field.alias or field.label,
                description=field.description,
                data_type=field.data_type,
                allowed_aggregations=field.allowed_aggregations,
            )
            for entity in projection.entities
            for field in entity.fields
        )
        return AuthorizedModelContext(
            request_id=request.request_id,
            source_id=projection.source_id,
            root_entity_ids=projection.root_entity_ids,
            semantic_references=references,
            catalog_fingerprint=projection.catalog_fingerprint,
            max_output_tokens=max_output_tokens,
        )
    pruned: list[SemanticReference] = []
    for field_id in sorted(view.field_ids):
        reference = (semantic_references or {}).get(field_id)
        if reference is not None:
            pruned.append(reference)
    return AuthorizedModelContext(
        request_id=request.request_id,
        source_id=view.source_id,
        root_entity_ids=view.root_entity_ids,
        semantic_references=tuple(pruned),
        catalog_fingerprint=view.catalog_fingerprint,
        max_output_tokens=max_output_tokens,
    )
