## Why

The assembly lifecycle now has a safe internal `AssemblyDraft` wire format, but hand authors must still provide control-plane fields such as assertion IDs, provenance objects, review state, and draft revisions. A purpose-built YAML authoring contract is needed so model owners can express semantic content ergonomically without gaining a path to forge review or approval authority.

## What Changes

- Add a versioned, human-oriented semantic assembly YAML format for bundle identity, entities, fields, relationships, calculated fields, measures, grains, source references, compatibility, and safe deployment binding references.
- Deterministically lower authoring documents into internal `AssemblyDraft` records with derived assertion IDs, manual provenance, pending review state, revision `0`, and trusted host-derived author identity.
- Add strict schema validation with bounded, source-located diagnostics for duplicate/unknown members, invalid references, unsupported versions, YAML hazards, unsafe text, inline credentials, and calculated-field violations.
- Define canonical authoring normalization so comments, anchors, aliases, mapping order, and formatting cannot change lowered semantic assertions or the eventual published semantic fingerprint.
- Extend the Admin service with authorized validate/import operations that return bounded diagnostics or create a draft through the existing lifecycle boundary; validation performs no persistence and import cannot publish or approve.
- Add a documented example and round-trip/export profile that emits stable reviewable YAML without lifecycle authority, raw secrets, review bindings, or audit identities.
- Keep arbitrary YAML tags, remote/file includes, environment interpolation, templates/macros, executable expressions, and automatic review/approval/publication out of scope.

## Capabilities

### New Capabilities
- `semantic-assembly-yaml-authoring`: Versioned safe YAML authoring schema, bounded parsing and diagnostics, deterministic lowering/export, reference validation, and semantic equivalence rules.

### Modified Capabilities
- `semantic-assembly-lifecycle`: Define the trusted conversion boundary from an authoring document to a revision-zero draft with derived pending assertions and no caller-controlled lifecycle authority.
- `semantic-admin-api`: Add tenant/source-authorized authoring validation and draft import operations with bounded safe DTOs and no review/publication bypass.

## Impact

- **Core**: extend `nl2data_core.assembly` with authoring models, parser/diagnostic contracts, lowering and export helpers; keep `AssemblyDraft` as the internal lifecycle artifact.
- **Admin package**: add authoring document validation/import commands and result DTOs that delegate lifecycle state construction to core and trusted identity derivation to the service boundary.
- **Tests**: unit and security coverage for YAML parsing, bounds, references, deterministic lowering, lifecycle-field rejection, secret safety, calculated fields, canonical equivalence, and import authorization.
- **Docs/demo**: add English and Chinese bundle-as-code authoring guidance and a complete example consumable by a deterministic demo.
- **Dependencies**: continue using the existing `PyYAML` dependency through safe loading; no template, include, or code-execution dependency is introduced.
