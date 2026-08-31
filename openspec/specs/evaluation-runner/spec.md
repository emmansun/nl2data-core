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

### Requirement: Evaluation reports carry value-semantics attribution
For corpus questions whose filters target enum-coded fields, the evaluation report SHALL record per-question value-semantics attribution distinguishing: a filter value resolved from the declared mapping (`VS_HIT`), a declared mapping that the value missed (`VS_MISS`), and a field with no value semantics declared (`VS_UNPOLICIED`). Attribution SHALL be bounded, evidence-safe, and reported per run so stage gates can read the hit rate.

#### Scenario: Annotated hit question reports VS_HIT
- **WHEN** a corpus question annotated with an expected mapping hit resolves its filter value from the declared mapping
- **THEN** the report records `VS_HIT` attribution for that question

#### Scenario: A miss is attributed, not silently degraded
- **WHEN** a corpus question's filter value fails to resolve against a declared mapping
- **THEN** the report records `VS_MISS` attribution and the case outcome reflects the resolution failure

#### Scenario: Unpolicied fields are measured separately
- **WHEN** a corpus question filters an enum-coded field with no value semantics declared
- **THEN** the report records `VS_UNPOLICIED` attribution, distinct from `VS_MISS`

#### Scenario: Attribution is evidence-safe
- **WHEN** the evaluation report is serialized
- **THEN** attribution output contains bounded codes and counts, never raw result rows or physical values

#### Scenario: Attribution granularity is per value, per case, per run
- **WHEN** a case contains enum-filter occurrences whose individual values resolve differently, including mixed `in` lists
- **THEN** each value outcome is recorded individually, aggregated per case, and summarized per run

### Requirement: Evaluation reports carry calculated-field attribution
For corpus questions whose selections target declared or expected calculated fields, the evaluation report SHALL record bounded calculated-field attribution distinguishing: a selection resolved to a declared calculated field and compiled (`CF_HIT`), a referenced calculated field that failed expansion (`CF_COMPILE_FAIL`), an annotated expected calculated field that is not declared in the active bundle (`CF_NOT_DECLARED`), and an annotated expected calculated field that is declared but not referenced by the case (`CF_NOT_REFERENCED`). Attribution SHALL be bounded, evidence-safe, and recorded per selection, aggregated per case, and summarized per run so stage gates can read the hit, not-declared, not-referenced, and compile-failure rates.

#### Scenario: A referenced calculated field reports CF_HIT
- **WHEN** a case's selection references a declared calculated field and compiles successfully
- **THEN** the report records `CF_HIT` attribution for that selection

#### Scenario: A compile failure is attributed, not silently degraded
- **WHEN** expansion of a referenced calculated field fails
- **THEN** the report records `CF_COMPILE_FAIL` attribution and the case outcome reflects the compilation failure

#### Scenario: An undeclared field is attributed to the bundle
- **WHEN** a corpus question annotated with an expected calculated field runs against a bundle that does not declare it
- **THEN** the report records `CF_NOT_DECLARED` attribution; the deviation is a recorded attribution, not a case crash

#### Scenario: An unreferenced declaration is attributed to prompt context
- **WHEN** the active bundle declares the expected calculated field but the case does not reference it
- **THEN** the report records `CF_NOT_REFERENCED` attribution, keeping prompt-context improvements measurable separately from bundle authoring

#### Scenario: Attribution granularity is per selection, per case, per run
- **WHEN** a case contains multiple calculated-field selections with differing outcomes
- **THEN** each selection outcome is recorded individually, aggregated per case, and summarized per run

#### Scenario: Attribution is evidence-safe
- **WHEN** the evaluation report is serialized
- **THEN** attribution output contains bounded codes and counts, never expressions, physical names, or raw values