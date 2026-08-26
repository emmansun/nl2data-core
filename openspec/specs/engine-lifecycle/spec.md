## Purpose

Define the explicit engine lifecycle with startup, health, capability, and shutdown semantics.

## Requirements

### Requirement: Engine lifecycle is explicit
`NL2DataEngine` SHALL expose creation, readiness, draining, and close behavior through a defined lifecycle, and query operations SHALL require ready state.

#### Scenario: Query before readiness is rejected
- **WHEN** an application invokes query before engine initialization completes
- **THEN** the engine returns a structured lifecycle error and does not invoke a workflow

#### Scenario: Close is idempotent
- **WHEN** an application closes an engine more than once
- **THEN** subsequent close calls complete without reopening or corrupting engine state

### Requirement: Engine binds immutable dependencies
The engine SHALL bind one effective configuration snapshot and one plugin-registry generation for each active lifecycle, and SHALL not expose mutable internal registries through public models.

#### Scenario: Public capabilities are a snapshot
- **WHEN** an application requests engine capabilities
- **THEN** it receives a public immutable capability snapshot rather than internal registry objects

### Requirement: Query boundary is workflow-only
The engine SHALL submit public requests to a workflow port and SHALL NOT directly invoke native database, LLM, or provider executors.

#### Scenario: Missing workflow is explicit
- **WHEN** the engine is ready but no executable workflow is configured
- **THEN** a query returns a stable not-configured outcome without a fabricated answer or raw provider call

### Requirement: Drain prevents new work
When draining, the engine SHALL reject new query submissions while allowing already accepted work to follow the configured bounded shutdown behavior.

#### Scenario: New query during drain is rejected
- **WHEN** the engine has entered draining state and a new query is submitted
- **THEN** submission fails with a lifecycle error and no new workflow instance is created
