## Why

Package refactoring has produced backend-capable building blocks, but product readiness still lacks one canonical, operator-facing mainflow demo that proves the full path from configuration to durable recovery. We need a single, repeatable demonstration contract that teams can run in CI and locally.

## What Changes

- Add a dedicated mainflow demo capability that defines one end-to-end path: configuration -> initialize -> execute -> durable persist/recover.
- Define mandatory acceptance scenarios for deterministic profile and real-service profile (PostgreSQL workflow state + Redis memory).
- Define a meaningful PostgreSQL reference source dataset (tables, row-scale targets, anomaly samples, and tenant isolation fields) for demo credibility.
- Define a standard demo question suite and SQL evidence set so capability coverage is visible and repeatable.
- Require user-facing runbook guidance so operators can run, diagnose, and validate the demo outcome consistently.
- Align demo verification with existing integration and conformance evidence without introducing internal-only APIs.

## Capabilities

### New Capabilities
- `mainflow-demo`: Contract for a canonical, repeatable end-to-end product demo covering configuration, startup, execution, and durable persistence/recovery.

### Modified Capabilities
- None.

## Impact

- Affected docs: getting-started and operations guides for demo runbook and evidence interpretation.
- Affected tests/verification: integration profile selection and explicit pass/fail criteria for persistence and recovery semantics.
- Affected fixtures/assets: reference schema, seed strategy, and expected query evidence for the demo domain.
- Affected packaging/composition guidance: clarify minimal package set for a production-like demo (`nl2data-core`, `nl2data-workflow-postgres`, `nl2data-memory-redis`, plus selected adapter/provider packages).
