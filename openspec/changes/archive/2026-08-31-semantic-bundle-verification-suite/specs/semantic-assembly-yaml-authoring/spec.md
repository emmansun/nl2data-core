## MODIFIED Requirements

### Requirement: Authoring documents use a versioned semantic-only schema
The authoring schema SHALL optionally express a bounded `verificationPlan` containing policy identity, deadlines, canonical semantic IR, fixture profile references, capability requirements, smoke assertions, and semantic contracts. It SHALL NOT accept computed plan/query fingerprints, approval bindings, runner/executor identity, statuses, observations, evidence, SQL/MQL, physical names, credentials, or native values.

#### Scenario: Verification plan lowers without lifecycle evidence
- **WHEN** valid authoring YAML contains a verification plan
- **THEN** lowering attaches the equivalent core `VerificationPlan` to the revision-zero draft without accepting or exporting fingerprints, statuses, or evidence

#### Scenario: Verification lifecycle authority is rejected
- **WHEN** verification authoring contains computed fingerprints, runner/executor identity, statuses, observations, evidence, backend syntax, or credentials
- **THEN** parsing or model validation fails before draft creation and no unsafe value is echoed