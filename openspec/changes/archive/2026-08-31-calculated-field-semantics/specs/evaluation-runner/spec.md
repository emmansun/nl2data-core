# evaluation-runner Delta

## ADDED Requirements

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
