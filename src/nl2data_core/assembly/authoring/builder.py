"""Fluent programmatic construction of semantic assembly authoring documents.

The builder is a pure constructor over the existing authoring models: every
fluent call constructs the corresponding pydantic model immediately, so all
model-level validators (bounds, safe descriptions, scalar profiles, forbidden
keys, descriptor-global uniqueness) run at call time — the same code the YAML
loader runs.  The builder adds no validation logic of its own beyond
structural misuse checks and performs no sorting of its own; documents built
here traverse the unchanged ``validate_authoring`` / ``lower_authoring`` /
``export_authoring`` pipeline exactly like loaded YAML documents.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import ValidationError

from nl2data_core.bundles.models import BundleCompatibility
from nl2data_core.planning.models import AggregationKind
from nl2data_core.views.models import ExprNode

from .diagnostics import AuthoringPath
from .models import (
    AUTHORING_API_VERSION,
    AUTHORING_KIND,
    AuthoringCalculatedField,
    AuthoringDeploymentBinding,
    AuthoringEntity,
    AuthoringField,
    AuthoringGrain,
    AuthoringMeasure,
    AuthoringMetadata,
    AuthoringPolicyTemplate,
    AuthoringRelationship,
    AuthoringSource,
    AuthoringSourceReference,
    AuthoringSpec,
    AuthoringVerificationPlan,
    SemanticAssemblyAuthoring,
)

_MAX_ERROR_MESSAGE_CHARS = 256


class AuthoringBuilderError(Exception):
    """Bounded, non-echoing failure of one builder construction step.

    The message names the failure class without repeating rejected input
    values, and carries an authoring path but never a source mark:
    programmatic construction has no line or column.
    """

    def __init__(self, message: str, *, path: AuthoringPath | None = None) -> None:
        bounded = message[:_MAX_ERROR_MESSAGE_CHARS]
        super().__init__(bounded)
        self.message = bounded
        self.path = path if path is not None else AuthoringPath()

    def __str__(self) -> str:
        return f"{self.message} (authoring path: {self.path.render()})"


def _path(parts: tuple[str | int, ...]) -> AuthoringPath:
    return AuthoringPath(parts=parts)


def _builder_error(
    path_parts: tuple[str | int, ...],
    error: ValidationError,
) -> AuthoringBuilderError:
    # Pydantic messages name the failure class (type, pattern, validator
    # text); they do not repeat the rejected input value.  The underlying
    # error is intentionally not chained: its display form echoes input.
    message = str(error.errors()[0].get("msg", "The authoring content is not valid."))
    return AuthoringBuilderError(message, path=_path(path_parts))


def _construct(model: type, path_parts: tuple[str | int, ...], **kwargs: Any) -> Any:
    try:
        return model(**kwargs)
    except ValidationError as error:
        raise _builder_error(path_parts, error) from None


class _AuthoringEntityBuilder:
    """Entity-scoped fluent surface; commits one entity at :meth:`done`."""

    def __init__(
        self,
        parent: SemanticAssemblyBuilder,
        entity_index: int,
        entity_id: str,
        label: str,
        description: str,
    ) -> None:
        self._parent = parent
        self._entity_index = entity_index
        self._entity_id = entity_id
        self._label = label
        self._description = description
        self._fields: list[AuthoringField] = []
        self._relationships: list[AuthoringRelationship] = []
        self._calculated_fields: list[AuthoringCalculatedField] = []
        self._closed = False

    def _entity_path(self) -> tuple[str | int, ...]:
        return ("spec", "entities", self._entity_index)

    def _require_open(self, operation: str) -> None:
        if self._closed or self._parent._built or self._parent._open_entity is not self:
            raise AuthoringBuilderError(
                f"{operation} is not allowed: this entity scope is no longer open."
            )

    def field(
        self,
        field_id: str,
        label: str,
        data_type: str,
        *,
        description: str = "",
        allowed_aggregations: tuple[AggregationKind, ...] | frozenset[AggregationKind] = (),
        value_semantics: Any = None,
    ) -> _AuthoringEntityBuilder:
        self._require_open("field")
        path = (*self._entity_path(), "fields", len(self._fields))
        self._fields.append(
            _construct(
                AuthoringField,
                path,
                field_id=field_id,
                label=label,
                description=description,
                data_type=data_type,
                allowed_aggregations=allowed_aggregations,
                value_semantics=value_semantics,
            )
        )
        return self

    def relationship(
        self,
        relationship_id: str,
        target_entity_id: str,
        source_fields: tuple[str, ...] | list[str],
        target_fields: tuple[str, ...] | list[str],
        label: str,
    ) -> _AuthoringEntityBuilder:
        self._require_open("relationship")
        path = (*self._entity_path(), "relationships", len(self._relationships))
        self._relationships.append(
            _construct(
                AuthoringRelationship,
                path,
                relationship_id=relationship_id,
                target_entity_id=target_entity_id,
                source_fields=source_fields,
                target_fields=target_fields,
                label=label,
            )
        )
        return self

    def calculated_field(
        self,
        name: str,
        label: str,
        expression: ExprNode,
        output_type: str,
        *,
        description: str = "",
        requires: tuple[str, ...] | list[str] = (),
        zero_division_policy: str = "null",
    ) -> _AuthoringEntityBuilder:
        self._require_open("calculated_field")
        path = (*self._entity_path(), "calculatedFields", len(self._calculated_fields))
        self._calculated_fields.append(
            _construct(
                AuthoringCalculatedField,
                path,
                name=name,
                label=label,
                description=description,
                expression=expression,
                output_type=output_type,
                requires=requires,
                zero_division_policy=zero_division_policy,
            )
        )
        return self

    def done(self) -> SemanticAssemblyBuilder:
        self._require_open("done")
        entity = _construct(
            AuthoringEntity,
            self._entity_path(),
            entity_id=self._entity_id,
            label=self._label,
            description=self._description,
            fields=tuple(self._fields),
            relationships=tuple(self._relationships),
            calculated_fields=tuple(self._calculated_fields),
        )
        self._closed = True
        self._parent._commit_entity(self, entity)
        return self._parent


class SemanticAssemblyBuilder:
    """Fluent, fail-closed constructor for semantic assembly authoring documents.

    Parameters mirror the authoring schema fields exactly.  There are no
    parameters for expression strings, computed fingerprints, lifecycle
    state, review or approval bindings, resolved secrets, or physical names:
    the authoring schema rejects that content class, and so does this surface.
    """

    def __init__(self, bundle_id: str, model_version: str, description: str = "") -> None:
        self._metadata = _construct(
            AuthoringMetadata,
            ("metadata",),
            bundle_id=bundle_id,
            model_version=model_version,
            description=description,
        )
        self._source: AuthoringSource | None = None
        self._entities: list[AuthoringEntity] = []
        self._open_entity: _AuthoringEntityBuilder | None = None
        self._measures: list[AuthoringMeasure] = []
        self._grains: list[AuthoringGrain] = []
        self._policies: list[AuthoringPolicyTemplate] = []
        self._source_references: list[AuthoringSourceReference] = []
        self._deployment_bindings: list[AuthoringDeploymentBinding] = []
        self._compatibility = BundleCompatibility()
        self._verification_plan: AuthoringVerificationPlan | None = None
        self._built = False

    def _require_open(self, operation: str) -> None:
        if self._built:
            raise AuthoringBuilderError(
                f"{operation} is not allowed: the document has already been built."
            )

    def _commit_entity(self, sub_builder: _AuthoringEntityBuilder, entity: AuthoringEntity) -> None:
        self._entities.append(entity)
        self._open_entity = None

    def source(
        self,
        source_id: str,
        *,
        catalog_fingerprint: str | None = None,
    ) -> SemanticAssemblyBuilder:
        self._require_open("source")
        self._source = _construct(
            AuthoringSource,
            ("spec", "source"),
            source_id=source_id,
            catalog_fingerprint=catalog_fingerprint,
        )
        return self

    def entity(self, entity_id: str, label: str, description: str = "") -> _AuthoringEntityBuilder:
        self._require_open("entity")
        if self._open_entity is not None:
            raise AuthoringBuilderError(
                "entity is not allowed: the previous entity scope is still open; "
                "call done() on it first."
            )
        entity_index = len(self._entities)
        # Construct the model once now so identity and safe-content validators
        # run at the entity() call, then again at done() with the children.
        _construct(
            AuthoringEntity,
            ("spec", "entities", entity_index),
            entity_id=entity_id,
            label=label,
            description=description,
        )
        sub_builder = _AuthoringEntityBuilder(self, entity_index, entity_id, label, description)
        self._open_entity = sub_builder
        return sub_builder

    def measure(
        self,
        measure_id: str,
        field_id: str,
        label: str,
        *,
        aggregation: AggregationKind = "none",
        description: str = "",
    ) -> SemanticAssemblyBuilder:
        self._require_open("measure")
        path = ("spec", "measures", len(self._measures))
        self._measures.append(
            _construct(
                AuthoringMeasure,
                path,
                measure_id=measure_id,
                field_id=field_id,
                aggregation=aggregation,
                label=label,
                description=description,
            )
        )
        return self

    def grain(
        self,
        grain_id: str,
        entity_id: str,
        *,
        attributes: tuple[str, ...] | list[str] | set[str] = (),
        description: str = "",
    ) -> SemanticAssemblyBuilder:
        self._require_open("grain")
        path = ("spec", "grains", len(self._grains))
        self._grains.append(
            _construct(
                AuthoringGrain,
                path,
                grain_id=grain_id,
                entity_id=entity_id,
                attributes=attributes,
                description=description,
            )
        )
        return self

    def policy(self, template: str, **parameters: Any) -> SemanticAssemblyBuilder:
        self._require_open("policy")
        path = ("spec", "policies", len(self._policies))
        self._policies.append(
            _construct(
                AuthoringPolicyTemplate,
                path,
                template=template,
                parameters=parameters,
            )
        )
        return self

    def source_reference(
        self,
        reference_id: str,
        source_id: str,
        *,
        catalog_fingerprint: str | None = None,
        description: str = "",
    ) -> SemanticAssemblyBuilder:
        self._require_open("source_reference")
        path = ("spec", "sourceReferences", len(self._source_references))
        self._source_references.append(
            _construct(
                AuthoringSourceReference,
                path,
                reference_id=reference_id,
                source_id=source_id,
                catalog_fingerprint=catalog_fingerprint,
                description=description,
            )
        )
        return self

    def compatibility(
        self,
        compatibility: BundleCompatibility | None = None,
        **field_arguments: Any,
    ) -> SemanticAssemblyBuilder:
        self._require_open("compatibility")
        if compatibility is not None:
            if field_arguments:
                raise AuthoringBuilderError(
                    "compatibility is not allowed: provide an instance or field "
                    "arguments, not both.",
                    path=_path(("spec", "compatibility")),
                )
            if not isinstance(compatibility, BundleCompatibility):
                raise AuthoringBuilderError(
                    "compatibility is not allowed: the value is not a "
                    "BundleCompatibility instance.",
                    path=_path(("spec", "compatibility")),
                )
            self._compatibility = compatibility
            return self
        self._compatibility = _construct(
            BundleCompatibility, ("spec", "compatibility"), **field_arguments
        )
        return self

    def deployment_binding(
        self,
        binding_id: str,
        environment: str,
        source_id: str,
        connection_reference: str,
    ) -> SemanticAssemblyBuilder:
        self._require_open("deployment_binding")
        path = ("spec", "deploymentBindings", len(self._deployment_bindings))
        self._deployment_bindings.append(
            _construct(
                AuthoringDeploymentBinding,
                path,
                binding_id=binding_id,
                environment=environment,
                source_id=source_id,
                connection_reference=connection_reference,
            )
        )
        return self

    def verification_plan(
        self,
        plan: AuthoringVerificationPlan | dict[str, Any],
    ) -> SemanticAssemblyBuilder:
        self._require_open("verification_plan")
        if isinstance(plan, AuthoringVerificationPlan):
            self._verification_plan = plan
            return self
        # Routed through the same normalized model construction as YAML:
        # camelCase aliases accepted, forbidden lifecycle keys rejected.
        try:
            self._verification_plan = AuthoringVerificationPlan.model_validate(plan)
        except ValidationError as error:
            raise _builder_error(("spec", "verificationPlan"), error) from None
        return self

    def build(self) -> SemanticAssemblyAuthoring:
        self._require_open("build")
        if self._open_entity is not None:
            raise AuthoringBuilderError(
                "build is not allowed: an entity scope is still open; call done() on it first.",
                path=_path(("spec", "entities", len(self._entities))),
            )
        spec = _construct(
            AuthoringSpec,
            ("spec",),
            source=self._source,
            entities=tuple(self._entities),
            measures=tuple(self._measures),
            grains=tuple(self._grains),
            policies=tuple(self._policies),
            source_references=tuple(self._source_references),
            compatibility=self._compatibility,
            deployment_bindings=tuple(self._deployment_bindings),
            verification_plan=self._verification_plan,
        )
        document = cast(
            SemanticAssemblyAuthoring,
            _construct(
                SemanticAssemblyAuthoring,
                (),
                api_version=AUTHORING_API_VERSION,
                kind=AUTHORING_KIND,
                metadata=self._metadata,
                spec=spec,
            ),
        )
        self._built = True
        return document
