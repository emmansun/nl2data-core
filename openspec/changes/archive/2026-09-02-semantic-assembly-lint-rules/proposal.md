## Why

Verification Suite now protects publish-time correctness, but authors and reviewers still need deterministic feedback before they reach approval or publication. A semantic assembly lint layer gives early, source-located diagnostics for clarity, governance, and production-readiness issues without duplicating validation or verification.

## What Changes

- Add deterministic semantic assembly lint rules that run against authoring models and lifecycle drafts before review, approval, or publish.
- Define stable lint diagnostic codes, severities, profiles, source paths, truncation behavior, and safe serialization rules.
- Distinguish hard validation failures from lint findings: lint may report warnings and errors, but it does not create review, approval, verification, audit, or publish authority.
- Add profile-driven behavior for compatibility, recommended, and production quality gates.
- Expose lint results through the transport-neutral Admin API so hosts, CI, and authoring tools can invoke the same rule engine.

## Capabilities

### New Capabilities

- `semantic-assembly-lint-rules`: Deterministic static diagnostics for semantic assembly quality, governance readiness, verification-plan readiness, and authoring ergonomics.

### Modified Capabilities

- `semantic-admin-api`: Add a bounded lint operation and DTO contract for assembly drafts without changing lifecycle authority or publication semantics.

## Impact

- Affected code: semantic assembly authoring/lifecycle models, a new lint package or module, Admin service DTOs/schema/service wiring, and tests for deterministic diagnostics.
- Affected APIs: new Admin lint command/result models; public lint models may be exported from the core semantic assembly surface.
- Dependencies: no new runtime dependency expected; lint rules should use existing validated models, canonical helpers, and source mark/path diagnostics.
- Systems: CI and host authoring tools can treat production-profile lint errors as blocking before review or publish.