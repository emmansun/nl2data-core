"""Fail-closed YAML and JSON loading for semantic assembly drafts."""

from __future__ import annotations

from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import ASSEMBLY_API_VERSION, AssemblyDraft

_MAX_ISSUES = 64
_MAX_ISSUE_CHARS = 1_024


class AssemblyLoadIssue(BaseModel):
    """One bounded and safe assembly load issue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    message: str = Field(min_length=1, max_length=_MAX_ISSUE_CHARS)


class AssemblyLoadResult(BaseModel):
    """Immutable result of one assembly draft load attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["loaded", "incompatible_schema", "invalid"]
    draft: AssemblyDraft | None = None
    issues: tuple[AssemblyLoadIssue, ...] = Field(
        default_factory=tuple, max_length=_MAX_ISSUES
    )

    @model_validator(mode="after")
    def _consistent(self) -> AssemblyLoadResult:
        if self.kind == "loaded":
            if self.draft is None or self.issues:
                raise ValueError("loaded results require one draft and no issues")
        elif self.draft is not None or not self.issues:
            raise ValueError("failed loads require issues and must not carry a draft")
        return self

    @property
    def loaded(self) -> bool:
        return self.kind == "loaded"

    def issue_codes(self) -> list[str]:
        return [issue.code for issue in self.issues]


class SemanticAssemblyLoader(Protocol):
    """Replaceable loader protocol for assembly YAML or JSON files."""

    def load(self, payload: str) -> AssemblyLoadResult: ...


def _invalid(code: str, message: str) -> AssemblyLoadResult:
    return AssemblyLoadResult(
        kind="invalid",
        issues=(AssemblyLoadIssue(code=code, message=message[:_MAX_ISSUE_CHARS]),),
    )


def _validation_issues(error: ValidationError) -> tuple[AssemblyLoadIssue, ...]:
    issues: list[AssemblyLoadIssue] = []
    for item in error.errors()[:_MAX_ISSUES]:
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        message = f"{location}: {item['msg']}"[:_MAX_ISSUE_CHARS]
        issues.append(AssemblyLoadIssue(code="invalid_payload", message=message))
    return tuple(issues)


class YamlAssemblyLoader:
    """Load an assembly draft after checking its raw file API version."""

    def __init__(
        self,
        *,
        supported_api_versions: tuple[str, ...] = (ASSEMBLY_API_VERSION,),
    ) -> None:
        self._supported_api_versions = supported_api_versions

    def load(self, payload: str) -> AssemblyLoadResult:
        try:
            data: Any = yaml.safe_load(payload)
        except (yaml.YAMLError, UnicodeError) as error:
            return _invalid("malformed_payload", f"payload is not valid YAML or JSON: {error}")
        if not isinstance(data, dict):
            return _invalid("malformed_payload", "payload must be an object")

        api_version = data.get("apiVersion")
        if (
            not isinstance(api_version, str)
            or api_version not in self._supported_api_versions
        ):
            return AssemblyLoadResult(
                kind="incompatible_schema",
                issues=(
                    AssemblyLoadIssue(
                        code="incompatible_schema",
                        message=(
                            f"assembly apiVersion {api_version!r} is not supported "
                            "by this runtime"
                        ),
                    ),
                ),
            )

        try:
            draft = AssemblyDraft.model_validate(data)
        except ValidationError as error:
            return AssemblyLoadResult(kind="invalid", issues=_validation_issues(error))
        return AssemblyLoadResult(kind="loaded", draft=draft)