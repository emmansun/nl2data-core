## MODIFIED Requirements

### Requirement: Versioned workflow state
The workflow foundation SHALL represent a workflow instance with a versioned immutable state containing request identity, workflow identity, status, attempt counters, safe evidence references, and an optional tenant scope fingerprint. Durable stores SHALL persist only the safe representation.

#### Scenario: State snapshot is serializable
- **WHEN** a workflow state is created with valid identifiers and status
- **THEN** it can be serialized without raw prompts, raw queries, credentials, or raw result records

### Requirement: Valid transitions are enforced
The workflow foundation SHALL define allowed status transitions and SHALL reject transitions that bypass required foundation states or move from terminal states. Durable compare-and-set updates SHALL preserve these transition rules.

#### Scenario: Invalid transition is rejected
- **WHEN** code attempts to transition a completed or closed workflow to an active state
- **THEN** the state store rejects the transition with a structured workflow error

### Requirement: Events and budgets are bounded
Workflow events SHALL record transition identity and safe metadata, and workflow budgets SHALL bound attempts or event counts without accepting negative values. Durable snapshots SHALL preserve these bounds.

#### Scenario: Budget exhaustion is explicit
- **WHEN** a workflow exceeds its configured attempt or event budget
- **THEN** the runtime records a bounded failure or terminal status instead of continuing indefinitely

### Requirement: In-memory state store is available
P0 SHALL provide an in-memory state-store implementation behind a replaceable protocol for deterministic unit and contract tests, and P2 SHALL provide a durable SQLite implementation behind the same boundary.

#### Scenario: State can be created and read
- **WHEN** a valid workflow state is stored
- **THEN** it can be retrieved by workflow ID and reflects accepted transitions from either the in-memory or configured durable store