## ADDED Requirements

### Requirement: Canonical mainflow verification executes the target question set
The canonical mainflow verification SHALL include q2, q5, q6, and q8 as executable governed cases, with fixed deterministic expectations, protected result assertions, and corresponding runbook interpretation. Question evidence SQL alone SHALL NOT count as successful execution of a case.

#### Scenario: Deterministic mainflow verifies each target question
- **WHEN** the deterministic mainflow verification is run
- **THEN** it SHALL execute all four target questions through the public facade and report each case's explicit pass/fail result against its protected assertions

#### Scenario: Real-service mainflow distinguishes unavailable from passing
- **WHEN** the PostgreSQL mainflow verification is run with or without its optional source service
- **THEN** available executions SHALL be verified against protected assertions, while unavailable executions SHALL be reported as skipped/unavailable and never as passing

#### Scenario: Runbook identifies target question outcomes
- **WHEN** an operator follows the canonical demo runbook
- **THEN** it SHALL identify the four target cases, their expected result shapes, their governance/compiler execution path, and the meaning of unavailable PostgreSQL results