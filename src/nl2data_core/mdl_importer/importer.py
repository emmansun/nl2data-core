"""Offline Wren MDL to semantic-assembly-authoring import pipeline.

The pipeline is strictly sequential and fail-closed:

1. parse the bounded MDL JSON subset (``parser.parse_mdl``);
2. optionally load the bounded identifier-override manifest;
3. convert the supported subset into authoring models
   (``convert.convert_mdl``);
4. serialize through the existing deterministic authoring exporter and
   reparse the result with ``SemanticAssemblyAuthoringLoader`` — the
   existing loader and its semantic validation remain the final
   authority.

Only a fully validated document is returned; a failed import never
yields a partial document.  The pipeline performs no network access,
no catalog introspection, no draft persistence, and no lifecycle
state changes.
"""

from __future__ import annotations

from nl2data_core.assembly.authoring import (
    SemanticAssemblyAuthoringLoader,
    export_authoring,
)

from .convert import convert_mdl
from .diagnostics import MdlDiagnostic, MdlImportResult
from .manifest import MappingManifest, load_manifest
from .parser import parse_mdl


def import_wren_mdl(
    payload: bytes,
    *,
    source_id: str,
    bundle_id: str,
    model_version: str,
    manifest_payload: bytes | None = None,
) -> MdlImportResult:
    """Convert one MDL payload into validated authoring YAML."""
    parsed = parse_mdl(payload)
    if not parsed.parsed or parsed.document is None:
        return MdlImportResult(diagnostics=parsed.diagnostics, truncated=parsed.truncated)

    manifest: MappingManifest | None = None
    if manifest_payload is not None:
        manifest, diagnostics = load_manifest(manifest_payload)
        if manifest is None:
            return MdlImportResult(diagnostics=diagnostics)

    outcome = convert_mdl(
        parsed.document,
        source_id=source_id,
        bundle_id=bundle_id,
        model_version=model_version,
        manifest=manifest,
    )
    if not outcome.converted or outcome.model is None:
        return MdlImportResult(
            diagnostics=outcome.diagnostics,
            truncated=outcome.truncated,
        )

    exported = export_authoring(outcome.model)
    if not exported.exported or exported.document is None:
        return MdlImportResult(
            diagnostics=(
                MdlDiagnostic(
                    code="authoring_validation_failed",
                    path="$",
                    message="the converted document cannot be exported as "
                    "authoring YAML",
                ),
            )
        )

    reparsed = SemanticAssemblyAuthoringLoader().load(exported.document)
    if not reparsed.loaded or reparsed.model is None:
        diagnostics = tuple(
            MdlDiagnostic(
                code="authoring_validation_failed",
                path=diagnostic.path.render(),
                message=diagnostic.message,
            )
            for diagnostic in reparsed.diagnostics
        )
        return MdlImportResult(
            diagnostics=diagnostics,
            truncated=reparsed.truncated,
        )
    return MdlImportResult(document=exported.document)
