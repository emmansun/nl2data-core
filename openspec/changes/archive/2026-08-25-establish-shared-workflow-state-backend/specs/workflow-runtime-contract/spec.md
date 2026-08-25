## MODIFIED Requirements

### Requirement: Workflow stages and mandatory gates are explicit
The runtime SHALL expose ordered stages for memory, intent, IR building, IR/view validation, deterministic compilation, artifact guarding, governance, authorization, lease ownership, execution, protection, persistence, and completion, and SHALL reject execution or state commit when a required prior gate is missing, stale, or inconsistent.

#### Scenario: Adapter cannot bypass governance
- **WHEN** a workflow attempts to execute before current IR/view validation, compilation, artifact guard, governance, authorization, and lease gates pass
- **THEN** the adapter is not invoked and a safe workflow rejection is produced

#### Scenario: Stale owner cannot commit
- **WHEN** a worker loses its lease or fencing token before state persistence
- **THEN** the state store rejects the commit and the worker cannot claim terminal completion

### Requirement: Cancellation and deadlines are bounded
Every stage that can perform external work SHALL receive a bounded deadline/cancellation context and SHALL produce a typed timeout or cancellation outcome without leaking native task/provider objects. The runtime SHALL reverify compiler, artifact, governance, authorization, effective-limit, lease, and fencing evidence immediately before adapter execution.

#### Scenario: Cancelled workflow stops before execution
- **WHEN** cancellation is observed after IR validation but before adapter execution
- **THEN** no adapter call starts and the workflow returns a safe cancelled outcome

#### Scenario: Lease loss stops execution handoff
- **WHEN** the workflow lease expires or its fencing token is superseded before adapter handoff
- **THEN** the runtime rejects execution before adapter access
