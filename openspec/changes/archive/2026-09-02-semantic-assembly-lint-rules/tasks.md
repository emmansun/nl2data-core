## 1. Characterization and Contracts

- [x] 1.1 Add failing unit tests for lint diagnostic model serialization, deterministic ordering, `SAL###` code validation, severity/profile behavior, and safe truncation/redaction.
- [x] 1.2 Add authoring lint tests that prove equivalent YAML presentation yields stable diagnostics and source-located paths after successful parse/validation.
- [x] 1.3 Add draft lint tests that prove stored `AssemblyDraft` linting is revision-stable, source-mark optional, and has no lifecycle mutation side effects.
- [x] 1.4 Add Admin contract/schema characterization tests for new lint command/result DTOs and safe bounded response fields.

## 2. Core Lint Models and Engine

- [x] 2.1 Create semantic assembly lint models for profiles, severities, source locations, target paths, diagnostic references, diagnostic payloads, and lint result summaries.
- [x] 2.2 Implement deterministic diagnostic normalization and ordering by severity, code, target path, source location, and safe reference.
- [x] 2.3 Implement built-in lint profile definitions for `compatibility`, `recommended`, and `production`, including profile version and blocking calculation.
- [x] 2.4 Add safe message construction, scalar truncation, and secret-like value redaction using existing bounded/safe-content conventions.
- [x] 2.5 Export the public lint models and runner from the appropriate core assembly surface without importing Admin or transport modules.

## 3. Built-in Rule Catalog

- [x] 3.1 Implement naming and ambiguity rules for duplicate or confusable business labels across entities, fields, measures, calculated fields, and value mappings.
- [x] 3.2 Implement description-quality rules for missing, too-short, or placeholder descriptions where semantic clarity is required by profile.
- [x] 3.3 Implement governance-readiness rules for sensitive/PII exposure, masking metadata gaps, default-queryable risky fields, and missing tenant/source policy hints.
- [x] 3.4 Implement semantic-consistency rules for conflicting business-term mappings, orphan-like references, and calculated-field metadata mismatches that are not hard validation failures.
- [x] 3.5 Implement verification-plan readiness rules for missing enabled smoke cases, semantic contract cases, deadlines, and executor capability requirements in production-oriented assemblies.
- [x] 3.6 Document the initial `SAL###` rule catalog with severity by profile and stable remediation guidance.

## 4. Authoring and Draft Integration

- [x] 4.1 Add a lint entry point for validated authoring models that preserves authoring paths and source marks when available.
- [x] 4.2 Add a lint entry point for lifecycle `AssemblyDraft` artifacts that uses stable draft/assertion paths when source marks are absent.
- [x] 4.3 Ensure lint does not parse unsafe YAML, lower invalid models, mutate drafts, create review bindings, create verification evidence, create audit records, or affect Bundle fingerprints.
- [x] 4.4 Add round-trip tests for parse/export/parse path stability and equivalent-content diagnostic stability.

## 5. Admin API Integration

- [x] 5.1 Add Admin command/result DTOs for `lint_authoring` and `lint_draft`, including profile selection, expected draft revision, diagnostic counts, blocking status, and safe ordered diagnostics.
- [x] 5.2 Wire `lint_authoring` through existing safe authoring parse/validation without persistence or lifecycle authority.
- [x] 5.3 Wire `lint_draft` through existing draft loading, trusted tenant/source scope, read permission, and expected revision checks without mutation.
- [x] 5.4 Update Admin capability and schema generation so lint operations and prerequisites are advertised accurately.
- [x] 5.5 Add Admin security tests for missing permission, cross-scope denial, stale revision conflict, and redacted diagnostic responses.

## 6. Validation and Documentation

- [x] 6.1 Add focused docs for lint profiles, built-in rule codes, deterministic output, and the boundary between validation, lint, Verification Suite, and publish audit.
- [x] 6.2 Run focused unit and Admin contract tests for semantic assembly lint rules.
- [x] 6.3 Run `ruff check` for touched source and tests.
- [x] 6.4 Run the relevant type-check command for touched core and Admin modules.
- [x] 6.5 Run `openspec validate semantic-assembly-lint-rules --strict` and resolve any spec/task issues.