# mainflow-demo Specification

## Purpose
TBD - created by archiving change mainflow-demo-e2e. Update Purpose after archive.
## Requirements
### Requirement: Canonical Mainflow Demo Contract
The system SHALL provide one canonical demo contract that proves the complete product path from configuration to startup, query execution, and workflow outcome visibility through the public facade boundary.

#### Scenario: Deterministic profile demonstrates executable mainflow
- **WHEN** an operator runs the documented deterministic demo profile with valid bounded configuration and initializes the facade
- **THEN** the demo SHALL execute a query through the public facade and produce a protected `QueryOutcome` with explicit status semantics and observable workflow identity/handle behavior.

#### Scenario: Demo remains transport-neutral
- **WHEN** the canonical demo is executed
- **THEN** it SHALL use transport-neutral public APIs and SHALL NOT require internal-only module imports as part of the operator-facing path.

### Requirement: Durable Persistence and Recovery Proof
The system SHALL define mandatory demo acceptance checks for durable state behavior, including persistence, duplicate replay semantics, and recovery/cancellation handling.

#### Scenario: Durable workflow state survives process boundary
- **WHEN** the real-service demo profile runs with a durable state backend and execution is resumed or replayed after lifecycle restart
- **THEN** the observed behavior SHALL follow documented resume and idempotency semantics without fabricating new successful executions.

#### Scenario: Cancellation fail-fast is demonstrated
- **WHEN** cancellation is recorded for a non-terminal workflow in the demo profile
- **THEN** a later resume path SHALL fail fast with the documented cancellation outcome before any external adapter execution.

### Requirement: Demo Runbook and Evidence Interpretation
The system SHALL document a runbook for the canonical demo that includes prerequisites, command/step sequence, expected outcomes, and failure interpretation.

#### Scenario: Operator can run and validate outcomes without implicit knowledge
- **WHEN** a new operator follows the published runbook
- **THEN** the operator SHALL be able to determine pass/fail using explicit expected outcomes, including clear handling for unavailable optional services versus verified service execution.

#### Scenario: Runbook is published in canonical guide locations
- **WHEN** the demo documentation is published
- **THEN** the primary runbook SHALL be available at `docs/guides/mainflow-demo.md` and mirrored at `docs/guides/mainflow-demo.zh-CN.md`.

#### Scenario: Executable assets are discoverable at repository root
- **WHEN** a user needs to run or inspect demo assets
- **THEN** executable assets SHALL be organized under the repository-root `demo/` directory with documented subdirectories for schema, seed data, question set, and run entrypoints.

### Requirement: Reference Source Dataset Demonstrates Real Capability
The canonical demo SHALL include a meaningful PostgreSQL reference source dataset that can demonstrate join, aggregation, time-window analysis, governance denial, tenant isolation, and recovery-related behavior.

#### Scenario: Reference schema supports cross-table business questions
- **WHEN** the real-service demo profile is provisioned
- **THEN** the source dataset SHALL include a documented multi-table order-fulfillment domain with required keys, time fields, and segmentation fields sufficient for cross-table and time-window analytics.

#### Scenario: Dataset contains realistic anomalies and tenant partitions
- **WHEN** demo data is seeded
- **THEN** it SHALL include at least two tenant partitions and documented anomaly samples (for example cancelled orders, partial shipments, refunds, duplicate payment attempts, or delayed/null operational fields) used by acceptance cases.

#### Scenario: Standard question and SQL evidence suite is reproducible
- **WHEN** an operator runs the canonical demo verification flow
- **THEN** the system SHALL provide a documented standard question set and a corresponding SQL evidence set with deterministic result-shape assertions for pass/fail interpretation.

### Requirement: Demo Outcomes Provide End-User Decision Value
The canonical demo SHALL present outputs with practical reference meaning for end users, including role context, decision intent, and action guidance.

#### Scenario: Each demo question includes decision context
- **WHEN** a user reviews a standard demo question and its result
- **THEN** the runbook SHALL state why the question matters, which user role it serves, and what decision the result informs.

#### Scenario: Results include bounded action guidance and caveats
- **WHEN** a result is presented in the canonical demo
- **THEN** the runbook SHALL include at least one bounded action suggestion and one caveat about data freshness, lag, or interpretation limits.

