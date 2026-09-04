## Why

Programmatic hosts today must emit YAML text to create semantic assembly documents; the only supported front-end is the bounded YAML loader (DDS-020 Path A friction). DDS-020 §9.2 requires a fluent programmatic Builder API so that hand-written, discovery-pipeline, and LLM-generated documents can be assembled in code — with the same fail-closed validation as YAML, so there is no fork where a document passes one entry point and fails the other.

## What Changes

- Add `assembly/authoring/builder.py` with a fluent `SemanticAssemblyBuilder` that constructs the exact same `SemanticAssemblyAuthoring` model the YAML loader produces (metadata, source, entities with fields/relationships/calculated fields, measures, grains, policy template declarations, source references, compatibility, deployment bindings, verification plan).
- Builder methods accept only what the authoring schema accepts: constructing any model instance runs the existing pydantic validators, so raw policy payloads, fingerprints, lifecycle state, credentials, physical names, and unbounded content fail closed at construction with no fork from YAML behavior.
- Builder results flow through the unchanged shared pipeline (`validate_authoring` → `lower_authoring` → `export_authoring`); no builder-specific validation, lowering, or export path exists.
- Guarantee determinism and YAML equivalence: a builder-constructed document and an equivalent YAML document produce identical validation summaries, lowered assertion identities and payload hashes, and export bytes.
- Error surface: builder construction failures raise a bounded typed error carrying an authoring path and message, never echoing rejected values (no source marks — programmatic input has no line/column).
- Not in scope: YAML parser internally re-routing through builder calls (source marks and bounded parsing stay as-is; equivalence is guaranteed by the shared model and pipeline, verified by differential tests), scaffold/discovery generation (§9.3), and any runtime enforcement change.

## Capabilities

### New Capabilities
- `assembly-builder-api`: Fluent, fail-closed programmatic construction of semantic assembly authoring documents that is provably equivalent to YAML authoring and adds no lifecycle, credential, or fingerprint authority.

### Modified Capabilities

## Impact

- New module `src/nl2data_core/assembly/authoring/builder.py`; exports added to `assembly/authoring/__init__.py`.
- No changes to `models.py`, `loader.py`, `validation.py`, `lowering.py`, or `export.py` — the builder is a pure consumer of the existing model and pipeline.
- Tests: unit tests for the fluent surface and error classes; differential equivalence tests (builder document vs equivalent YAML) covering validation, lowering, and export.
- Docs: new section in `docs/guides/semantic-assembly-authoring.md` (+ zh-CN).
- No runtime, bundle, catalog, or Admin API changes.
