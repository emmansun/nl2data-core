# Semantic Assembly Lint Reference

> **Reader**: assembly authors and Admin/CI integrators. **Prerequisites**:
> [Semantic Assembly YAML authoring](../guides/semantic-assembly-authoring.md).
> Status vocabulary: **Implemented** (exists in source), **Conformant**
> (passes the deterministic conformance suite).

Semantic assembly lint is a deterministic, side-effect-free quality and
readiness check for **already validated** semantic content: a validated
authoring model or a stored lifecycle `AssemblyDraft`. Lint reports
advisory findings with stable codes and never replaces validation,
lifecycle authority, the Verification Suite, or publish audit.

## Deterministic output

- Lint accepts only parsed and validated objects. It never parses unsafe
  YAML, lowers invalid models, mutates drafts, creates review bindings,
  verification evidence, or audit records, and never changes semantic
  Bundle fingerprints.
- Diagnostics carry a stable `SAL###` code, a severity, the selected
  profile, a semantic target path, an optional source location
  (line/column, when authoring source marks are available), a bounded
  safe message (at most 256 characters), and at most 8 safe references.
- Equivalent semantic content produces identical diagnostics in the same
  order regardless of YAML key order, comments, whitespace, or in-memory
  mapping insertion order. Authoring target paths are identity-based
  (for example `$.spec.entities.orders.fields.email`) and remain stable
  across parse/export/parse round trips; draft diagnostics use stable
  assertion paths (`$.assertions.<assertion-id>`).
- Results contain at most 100 diagnostics. The summary reports the
  diagnostic/error/warning/info counts, whether any `error` severity
  exists, and whether the selected profile is **blocking**.
- Messages never contain credentials, resolved secrets, SQL/MQL, raw
  sample rows, native objects, or unrestricted scalar values. Secret-like
  scalars are replaced with `[redacted]`; other scalars are truncated.

## Lint profiles

Profiles are versioned (current version: `1`). Only `error` severity is
blocking for the selected profile.

| Profile | Purpose |
| --- | --- |
| `compatibility` | Legacy-friendly minimum: only rules that were always treated as defects run. |
| `recommended` | Default advisory profile for authoring and review. Never blocking. |
| `production` | Publish-readiness profile. Governance defects are errors and block. |

## Built-in rule catalog

| Code | Rule | compatibility | recommended | production |
| --- | --- | --- | --- | --- |
| `SAL001` | Duplicate or confusable business labels across entities, fields, measures, calculated fields, and value-mapping terms | warning | warning | error |
| `SAL002` | Missing or too-short business descriptions where semantic clarity is required | – | warning | warning |
| `SAL003` | Placeholder descriptions where semantic clarity is required | – | warning | warning |
| `SAL004` | PII-classified field without handling or masking description | – | warning | error |
| `SAL005` | PII-classified field exposing sample values | – | warning | error |
| `SAL006` | Source binding missing a catalog fingerprint policy hint | – | warning | error |
| `SAL007` | Business term mapped to multiple distinct stored values | warning | warning | error |
| `SAL008` | Grain not referenced by any measure attribute | – | warning | warning |
| `SAL009` | Calculated field with strict zero-division policy and no explaining description | – | info | warning |
| `SAL010` | Missing verification plan or no enabled smoke/semantic contract cases | – | warning | error |
| `SAL011` | Enabled verification cases without executor capability requirements | – | info | warning |

Remediation guidance is stable per code: disambiguate duplicated labels
(`SAL001`), write concrete business descriptions of at least 16
characters (`SAL002`, `SAL003`), describe PII handling and remove sample
values (`SAL004`, `SAL005`), add catalog fingerprint hints to source
bindings (`SAL006`), keep one stored value per business term (`SAL007`),
reference grains from measures (`SAL008`), document zero-division
behavior (`SAL009`), and enable smoke and semantic contract cases with
executor capability requirements (`SAL010`, `SAL011`).

## Boundary: validation, lint, Verification Suite, publish audit

| Concern | Owner | Authority |
| --- | --- | --- |
| Structural/semantic correctness of authoring YAML | Authoring validation | Rejects invalid content with diagnostics; lint findings never rescue or replace validation failures |
| Quality and readiness advice on validated content | Lint | Advisory diagnostics; only production-profile `error` severities mark a profile blocking; lint never approves, rejects, publishes, or mutates anything |
| Behavioral correctness of a draft | Verification Suite | Executes controlled smoke and semantic contract cases and produces evidence; a clean lint result never substitutes for suite evidence |
| Publication record of a published Bundle | Publish audit | Immutable audit trail; lint never creates or changes audit records or Bundle fingerprints |

## Admin operations

The Admin service exposes two side-effect-free lint operations
(`authoring lint` requires `BUNDLE_VALIDATE`; `assembly lint` requires
`ASSEMBLY_READ`):

- `lint_authoring` parses and validates the document safely first and
  returns bounded lint diagnostics; nothing is persisted.
- `lint_draft` loads the draft inside the trusted tenant/source scope,
  requires the expected draft revision (a stale revision returns the
  existing safe conflict response), and returns diagnostics without
  changing draft state.

Both responses contain only profile metadata, diagnostic counts,
blocking status, and ordered safe diagnostics (code, severity, path,
optional source location, bounded message).

## Where lint runs

```python
from nl2data_core.assembly.authoring import SemanticAssemblyAuthoringLoader
from nl2data_core.assembly.lint import LintProfileId, lint_authoring, lint_draft

parsed = SemanticAssemblyAuthoringLoader().load(yaml_text)
if parsed.loaded:
    result = lint_authoring(
        parsed.model,
        profile=LintProfileId.PRODUCTION,
        source_marks={entry.path.parts: entry.mark for entry in parsed.source_marks},
    )
    assert result.summary.blocking or result.summary.warning_count > 0

# Stored drafts lint without source marks, at stable assertion paths.
# draft_result = lint_draft(draft, profile=LintProfileId.PRODUCTION)
```

See also: [Capabilities and support](capabilities.md),
[Verification Suite](../architecture/verification-suite.md).
