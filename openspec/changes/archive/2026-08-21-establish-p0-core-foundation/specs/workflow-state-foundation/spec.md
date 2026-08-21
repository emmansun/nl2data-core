## ADDED Requirements

### Requirement: Versioned workflow state
The workflow foundation SHALL represent a workflow instance with a versioned immutable state containing request identity, workflow identity, status, attempt counters, and safe evidence references.

#### Scenario: State snapshot is serializable
- **WHEN** a workflow state is created with valid identifiers and status
- **THEN** it can be serialized without raw prompts, raw queries, credentials, or raw result records

### Requirement: Valid transitions are enforced
The workflow foundation SHALL define allowed status transitions and SHALL reject transitions that bypass required foundation states or move from terminal states.

#### Scenario: Invalid transition is rejected
- **WHEN** code attempts to transition a completed or closed workflow to an active state
- **THEN** the state store rejects the transition with a structured workflow error

### Requirement: Events and budgets are bounded
Workflow events SHALL record transition identity and safe metadata, and workflow budgets SHALL bound attempts or event counts without accepting negative values.

#### Scenario: Budget exhaustion is explicit
- **WHEN** a workflow exceeds its configured attempt or event budget
- **THEN** the runtime records a bounded failure or terminal status instead of continuing indefinitely

### Requirement: In-memory state store is available
P0 SHALL provide an in-memory state-store implementation behind a replaceable protocol for deterministic unit and contract tests.

#### Scenario: State can be created and read
- **WHEN** a valid workflow state is stored
- **THEN** it can be retrieved by workflow ID and reflects accepted transitions
