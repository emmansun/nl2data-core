"""Semantic Model Bundle loading: schema checks and safe payload validation.

A loader turns canonical serialized payloads back into validated bundles.
It SHALL reject unsupported schema versions with an explicit
``incompatible_schema`` result (never activating the bundle), recompute the
fingerprint from the canonical payload so an altered fingerprint in the
input can never be trusted, and surface every structural problem as a
bounded structured issue.

The loader protocol is replaceable so future DSL/YAML loaders can share
the same consumption contract.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import BUNDLE_SCHEMA_VERSION, SemanticModelBundle
from .validation import BundleValidationIssue, validate_bundle

#: Bounded number of issues reported by one load attempt.
_MAX_ISSUES = 64


class BundleLoadResult(BaseModel):
    """Immutable result of one bundle load attempt.

    ``loaded`` carries the validated bundle; ``incompatible_schema`` and
    ``invalid`` carry structured issues and never a bundle, so an
    unsupported or malformed payload can never enter a catalog or View.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["loaded", "incompatible_schema", "invalid"]
    bundle: SemanticModelBundle | None = None
    issues: tuple[BundleValidationIssue, ...] = Field(
        default_factory=tuple, max_length=_MAX_ISSUES
    )

    @model_validator(mode="after")
    def _consistent(self) -> BundleLoadResult:
        if self.kind == "loaded":
            if self.bundle is None:
                raise ValueError("loaded results must carry a bundle")
            if self.issues:
                raise ValueError("loaded results must not carry issues")
        else:
            if self.bundle is not None:
                raise ValueError("failed loads must not carry a bundle")
            if not self.issues:
                raise ValueError("failed loads must carry at least one issue")
        return self

    @property
    def loaded(self) -> bool:
        """Whether loading produced a validated bundle."""
        return self.kind == "loaded"

    def issue_codes(self) -> list[str]:
        """The bounded issue codes of this load result."""
        return [issue.code for issue in self.issues]


class SemanticBundleLoader(Protocol):
    """Replaceable loader protocol for canonical bundle payloads."""

    def load(self, payload: str) -> BundleLoadResult: ...


def _invalid(code: str, message: str) -> BundleLoadResult:
    return BundleLoadResult(
        kind="invalid",
        issues=(BundleValidationIssue(code=code, message=message),),
    )


def _issues_from_validation_error(error: ValidationError) -> tuple[BundleValidationIssue, ...]:
    issues: list[BundleValidationIssue] = []
    for item in error.errors()[:_MAX_ISSUES]:
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        issues.append(
            BundleValidationIssue(
                code="invalid_payload",
                message=f"{location}: {item['msg']}",
            )
        )
    return tuple(issues)


class CanonicalBundleLoader:
    """Loads bundles from their canonical JSON serialization.

    Schema version is checked on the raw payload before model
    construction, so an unsupported version fails with an explicit
    ``incompatible_schema`` result instead of a generic parse failure.
    """

    def __init__(
        self,
        *,
        supported_schema_versions: tuple[int, ...] = (BUNDLE_SCHEMA_VERSION,),
    ) -> None:
        self._supported_schema_versions = supported_schema_versions

    def load(self, payload: str) -> BundleLoadResult:
        """Load and validate one canonical bundle payload, failing closed."""
        try:
            data: Any = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            return _invalid("malformed_payload", f"payload is not valid JSON: {error}")
        if not isinstance(data, dict):
            return _invalid("malformed_payload", "payload must be a JSON object")

        schema_version = data.get("schema_version")
        if (
            not isinstance(schema_version, int)
            or schema_version not in self._supported_schema_versions
        ):
            return BundleLoadResult(
                kind="incompatible_schema",
                issues=(
                    BundleValidationIssue(
                        code="incompatible_schema",
                        message=(
                            f"bundle schema version {schema_version!r} is not "
                            "supported by this runtime"
                        ),
                    ),
                ),
            )

        try:
            bundle = SemanticModelBundle.model_validate(data)
        except ValidationError as error:
            return BundleLoadResult(
                kind="invalid", issues=_issues_from_validation_error(error)
            )

        supplied_fingerprint = data.get("fingerprint")
        if (
            isinstance(supplied_fingerprint, str)
            and supplied_fingerprint != bundle.fingerprint
        ):
            return _invalid(
                "fingerprint_mismatch",
                "payload fingerprint does not match the recomputed bundle fingerprint",
            )

        result = validate_bundle(
            bundle, supported_schema_versions=self._supported_schema_versions
        )
        if not result.valid:
            return BundleLoadResult(kind="invalid", issues=result.issues)
        return BundleLoadResult(kind="loaded", bundle=bundle)
