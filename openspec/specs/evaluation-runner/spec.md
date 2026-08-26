## Purpose

Define the deterministic evaluation runner with isolated fixture lifecycles and controlled execution.

## Requirements

### Requirement: Evaluation runner isolates fixture lifecycle
The evaluation runner SHALL validate a case, provision or reset its controlled fixture, execute the case, collect protected evidence, run mandatory assertions, record a case result, and reset or dispose the fixture.

#### Scenario: Fixture cleanup occurs after a case
- **WHEN** a case succeeds or fails
- **THEN** the runner attempts the declared reset or disposal strategy before finalizing the run

### Requirement: Scorers cannot access native fixture state
The runner SHALL provide scorers and assertions only protected case outputs and safe evidence references, never fixture credentials, native database clients, raw result objects, or raw prompts when the evidence policy forbids them.

#### Scenario: Evidence is safe by default
- **WHEN** an evaluation case produces a result
- **THEN** its report contains protected outcome data and fingerprints rather than credentials, native clients, or unrestricted provider errors

### Requirement: Mandatory assertion failures cannot be hidden by averages
The evaluation report SHALL preserve individual mandatory assertion failures independently of aggregate scores and SHALL distinguish pass, fail, skipped, and unavailable outcomes.

#### Scenario: Security failure fails the case
- **WHEN** a mandatory governance or result-protection assertion fails
- **THEN** the case is not considered passing even if non-mandatory scores are high