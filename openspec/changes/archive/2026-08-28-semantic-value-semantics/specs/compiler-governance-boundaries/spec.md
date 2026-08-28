# compiler-governance-boundaries Delta

## ADDED Requirements

### Requirement: Pre-execution guard verifies planner identity consistency
The pre-execution guard SHALL reject execution when compilation evidence carries a planner identity that differs from the planner identity in the compilation context, and when exactly one of the two sides carries a planner identity while the other lacks one. Once planner-identity versioning is actively used, evidence missing a planner identity SHALL be rejected outright.

#### Scenario: Planner identity drift is rejected before execution
- **WHEN** compilation evidence was produced under a planner identity different from the current context's planner identity
- **THEN** the guard returns a human-safe planner-identity drift reason and execution is not attempted

#### Scenario: Evidence without the context's planner identity is rejected
- **WHEN** the compilation context carries a planner identity but the evidence lacks one
- **THEN** the guard fails with a planner-identity reason before adapter execution

#### Scenario: Evidence identity without the context's planner identity is rejected
- **WHEN** compilation evidence carries a planner identity but the context lacks one
- **THEN** the guard fails with a planner-identity reason because one-sided identity cannot be drift-checked

#### Scenario: Both sides unset keeps legacy direct-compile paths working
- **WHEN** neither the context nor the evidence carries a planner identity and identity versioning is not active
- **THEN** the guard makes no planner-identity determination and existing behavior is unchanged
