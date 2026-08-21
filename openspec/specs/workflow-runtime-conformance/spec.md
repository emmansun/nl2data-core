# workflow-runtime-conformance Specification

## Purpose
TBD - created by archiving change establish-p2-governed-workflow-runtime. Update Purpose after archive.
## Requirements
### Requirement: Workflow conformance covers the complete governed path
The conformance suite SHALL cover normal reads, Memory-compatible follow-up, stale context, policy denial, malformed intent, timeout, cancellation, durable resume, idempotency, protected results, and safe evidence.

#### Scenario: Repeated conformance runs are deterministic
- **WHEN** the same fixtures, fake provider responses, tenant scope, policy/catalog fingerprints, and fixed clock are used
- **THEN** protected workflow evidence and mandatory assertion results are identical

### Requirement: Gate failures are mandatory failures
Conformance SHALL fail if any adapter executes before required validation/authorization, if raw context enters checkpoints, or if a stale/foreign tenant scope reaches execution.

#### Scenario: Security gate failure cannot be averaged away
- **WHEN** one mandatory gate assertion fails
- **THEN** the workflow case and conformance report are not passing regardless of other scores

