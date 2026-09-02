## ADDED Requirements

### Requirement: Semantic assembly lint emits deterministic bounded diagnostics
The system SHALL provide a deterministic lint operation for validated semantic assembly authoring models and lifecycle `AssemblyDraft` artifacts. Lint diagnostics SHALL include a stable code, severity, selected profile, target path, optional source location, bounded safe message, and optional safe references. Equivalent semantic content SHALL produce identical diagnostics and ordering regardless of YAML presentation or mapping insertion order.

#### Scenario: Equivalent content has stable lint output
- **WHEN** two equivalent assembly inputs differ only by YAML key order, comments, whitespace, bounded anchor spelling, or in-memory mapping insertion order
- **THEN** lint returns byte-equivalent diagnostic payloads for all presentation-invariant fields (code, severity, profile, target path, message, and references) in the same deterministic order, while source locations MAY shift when the presentation change alters physical line positions

#### Scenario: Diagnostics are safe and bounded
- **WHEN** lint reports an issue for names, descriptions, value semantics, deployment bindings, verification plans, or policy hints
- **THEN** the diagnostic contains only safe bounded text, paths, source marks, codes, severities, profiles, and references, without raw secrets, resolved credentials, SQL/MQL, native objects, raw sample rows, or unrestricted scalar values

### Requirement: Lint profiles classify advisory and blocking findings
The system SHALL define versioned lint profiles for `compatibility`, `recommended`, and `production`. Each built-in rule SHALL declare the profiles in which it runs and the severity it emits. A lint result SHALL expose whether the selected profile has blocking findings, and only `error` severity SHALL be blocking for that profile.

#### Scenario: Production profile blocks governance defects
- **WHEN** production-profile lint detects a production-governance defect such as an exposed sensitive field without masking metadata or a missing required tenant/source policy hint
- **THEN** the result contains an `error` diagnostic and reports the profile as blocking

#### Scenario: Recommended profile remains advisory for production-only rules
- **WHEN** recommended-profile lint detects a rule that is informational or warning-only outside production
- **THEN** the result reports the diagnostic with its configured non-error severity and does not mark the profile blocking

### Requirement: Lint stays separate from validation, lifecycle, verification, and audit authority
Lint SHALL NOT parse unsafe YAML, lower invalid models, approve or reject assertions, mutate draft state, create verification evidence, create publish audit records, publish or activate Bundles, or change semantic Bundle fingerprints. Validation failures SHALL remain validation diagnostics, while lint findings SHALL report quality or readiness issues on otherwise validated content.

#### Scenario: Lint does not mutate drafts
- **WHEN** lint runs against an existing assembly draft at an expected revision
- **THEN** the draft revision, assertion review state, approval state, verification plan, publication records, and activation state remain unchanged

#### Scenario: Lint does not replace verification
- **WHEN** an assembly has no lint errors under the selected profile
- **THEN** publish eligibility still depends on the existing lifecycle approval and Verification Suite requirements

### Requirement: Built-in rules cover quality, ambiguity, governance, and verification readiness
The built-in lint catalog SHALL include deterministic rules for ambiguous or duplicated business names, missing or weak descriptions, conflicting business-term mappings, risky sensitivity/masking exposure, default-queryable sensitive fields, orphan-like semantic references, missing mandatory tenant/source controls, and verification-plan readiness gaps. Rule codes SHALL use a stable `SAL###` namespace and SHALL remain documented.

#### Scenario: Ambiguous business names are reported
- **WHEN** two semantic fields, measures, calculated fields, entities, or value mappings expose the same or confusable business label within one assembly scope without disambiguation metadata
- **THEN** lint reports stable ambiguity diagnostics that identify the affected semantic paths without using physical names

#### Scenario: Verification plan readiness is reported before publish
- **WHEN** an assembly targets production but its verification plan lacks enabled smoke cases, enabled semantic contract cases, deadlines, or required executor capability references
- **THEN** lint reports readiness diagnostics before any Verification Suite execution is attempted

### Requirement: Lint supports authoring source locations and draft paths
Lint SHALL preserve authoring source locations when available and SHALL always include semantic target paths that are stable across parse/export/parse round trips. Draft-only lint diagnostics MAY omit line and column positions, but they SHALL still identify the lifecycle draft member or assertion path that needs attention.

#### Scenario: Authoring lint is source-located
- **WHEN** lint runs after successful authoring parse and validation for content that has source marks
- **THEN** each diagnostic with a source-backed target includes the authoring path and line/column location

#### Scenario: Draft lint remains actionable without source marks
- **WHEN** lint runs against a stored draft that no longer has authoring source marks
- **THEN** each diagnostic includes a stable draft/assertion path sufficient for Admin UI and CI reporting
