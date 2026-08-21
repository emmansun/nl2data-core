# workflow-resume-and-idempotency Specification

## Purpose
TBD - created by archiving change establish-p2-persistent-workflow-state. Update Purpose after archive.
## Requirements
### Requirement: Workflow resume is tenant-scoped
The system SHALL retrieve checkpoints by workflow/request identity and matching tenant scope fingerprint, and SHALL reject missing or mismatched scope for tenant-scoped records.

#### Scenario: Cross-tenant checkpoint lookup is denied
- **WHEN** tenant B requests a workflow checkpoint created under tenant A
- **THEN** the store returns no state or a structured scope conflict without exposing the snapshot

#### Scenario: Matching scope resumes safely
- **WHEN** the same tenant scope reopens a persisted non-terminal workflow
- **THEN** it receives the immutable safe snapshot and can continue through the existing transition rules

### Requirement: Idempotency prevents conflicting duplicate requests
The system SHALL bind an idempotency key to request identity, tenant scope, workflow identity, and expiry, and SHALL reject reuse with a different request or scope.

#### Scenario: Repeated terminal request returns the same safe reference
- **WHEN** the same tenant submits the same idempotency key after terminal completion
- **THEN** the system returns the existing workflow/outcome fingerprint without re-executing the external query

#### Scenario: Conflicting idempotency reuse is rejected
- **WHEN** a key is reused with a different request or tenant scope
- **THEN** the store returns a structured idempotency conflict

### Requirement: Recovery does not claim exactly-once execution
The system SHALL represent ambiguous post-crash recovery using safe status and evidence references and SHALL NOT claim that an external query executed exactly once.

#### Scenario: Crash after external execution remains reconcilable
- **WHEN** a worker terminates after external execution but before terminal state commit
- **THEN** recovery exposes the checkpoint/evidence state for reconciliation rather than silently replaying or claiming success

