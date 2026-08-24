## Purpose

Define the versioned workflow state, transition, budget, event, and replaceable state-store contracts.
## Requirements
### Requirement: Versioned workflow state
The workflow foundation SHALL represent a workflow instance with a versioned immutable state containing request identity, workflow identity, status, current stage identity, attempt counters, safe evidence references, optional tenant scope fingerprint, and checkpoint compatibility fingerprints. Compatibility fingerprints SHALL include the canonical Semantic Query IR version/fingerprint when a workflow has a planned query; legacy Semantic Query Plan identities SHALL not be persisted or accepted. Durable stores SHALL persist only the safe representation.

#### Scenario: State snapshot is serializable
- **WHEN** a workflow state is created with valid identifiers, stage, and status
- **THEN** it can be serialized without raw prompts, raw queries, credentials, provider objects, raw result records, SQL/MQL, physical driver values, or legacy plan objects

#### Scenario: IR compatibility is checked on resume
- **WHEN** a persisted workflow checkpoint references a Semantic Query IR version or fingerprint that is not compatible with the current runtime
- **THEN** resume is rejected as stale or incompatible rather than executing the checkpoint under changed semantics

#### Scenario: Legacy plan checkpoint is rejected
- **WHEN** a checkpoint contains a legacy Semantic Query Plan identity without canonical IR evidence
- **THEN** resume is rejected as incompatible and no adapter work starts

### Requirement: Valid transitions are enforced
The workflow foundation SHALL define allowed status and stage transitions and SHALL reject transitions that bypass mandatory runtime gates or move from terminal states. Durable compare-and-set updates SHALL preserve these rules.

#### Scenario: Gate-bypassing transition is rejected
- **WHEN** code attempts to enter execution without current validation, governance, and authorization evidence
- **THEN** the runtime/state store rejects the transition with a structured workflow error

### Requirement: Events and budgets are bounded
Workflow events SHALL record transition/stage identity and safe metadata, and workflow budgets SHALL bound attempts, events, retries, repairs, and elapsed time without accepting negative values.

#### Scenario: Runtime budget exhaustion is explicit
- **WHEN** a workflow exceeds its configured attempt, event, retry, repair, or deadline budget
- **THEN** the runtime records a bounded terminal or resumable failure instead of continuing indefinitely

### Requirement: In-memory state store is available
P0 SHALL provide an in-memory state-store implementation and P2 SHALL provide a durable SQLite implementation behind a replaceable protocol; both SHALL support runtime checkpoint identity and tenant-scoped lookup.

#### Scenario: Runtime checkpoint can be resumed safely
- **WHEN** a matching tenant and compatible checkpoint are loaded
- **THEN** the runtime resumes from the persisted stage without exposing raw checkpoint content

