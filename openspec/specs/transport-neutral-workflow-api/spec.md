## ADDED Requirements

### Requirement: Workflow handles expose safe status and outcomes
The public API SHALL expose a transport-neutral workflow handle/status contract containing only workflow identity, bounded stage/status, fingerprints, cancellation state, and protected outcome references.

#### Scenario: Workflow status contains no raw state
- **WHEN** an application requests workflow status
- **THEN** the returned model excludes prompts, SQL/MQL, rows/documents, credentials, native clients, and provider objects

### Requirement: Clarification is resumable through public contracts
The public API SHALL represent clarification questions and bounded options as structured models that a later turn can reference without exposing internal workflow state.

#### Scenario: Clarification options round-trip safely
- **WHEN** a workflow requires clarification
- **THEN** the public result contains a bounded clarification model with stable identifiers and no raw provider payload

### Requirement: Cancellation is explicit and bounded
The public API SHALL provide cancellation through a transport-neutral operation that propagates to the workflow runtime and returns a stable cancelled or already-terminal result.

#### Scenario: Cancellation before execution prevents adapter work
- **WHEN** a workflow is cancelled before its execution gate
- **THEN** the adapter is not invoked and the public result is a safe cancelled outcome
