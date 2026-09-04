# semantic-bundle-verification-suite Specification

## Purpose
TBD - created by archiving change semantic-bundle-verification-suite. Update Purpose after archive.
## Requirements
### Requirement: Verification plans are versioned bounded lifecycle artifacts
The system SHALL represent pre-publication verification intent as a frozen versioned `VerificationPlan` containing a policy profile, bounded Layer 2 smoke cases, bounded Layer 3 semantic contract cases, per-case deadlines, and required executor capability references. The plan SHALL have a deterministic fingerprint and stable case identities, SHALL be JSON-wire safe, and SHALL contain no credentials, resolved deployment values, raw SQL/MQL, arbitrary executable code, native objects, or unbounded expected values. The plan and its fingerprint SHALL remain outside the Bundle semantic fingerprint domain.

#### Scenario: Equivalent plans have stable identity
- **WHEN** equivalent verification plans are constructed with different mapping insertion or case declaration order
- **THEN** their canonical payloads, sorted case identities, and plan fingerprints are byte-identical

#### Scenario: Unsafe verification content is rejected
- **WHEN** a plan contains SQL/MQL text, callable code, credentials, native values, unsupported assertion kinds, duplicate case IDs, or values beyond configured bounds
- **THEN** plan construction fails before any fixture, compiler, adapter, or external service is invoked

### Requirement: Verification policy determines required layers
The system SHALL define versioned fail-closed verification policy profiles. Layer 1 structural verification SHALL be required for every publication. The production profile SHALL additionally require at least one enabled Layer 2 case and one enabled Layer 3 case, and every required case SHALL pass. A compatibility profile MAY permit structural-only verification for legacy trusted manual publication, but the selected profile and result SHALL be explicit in audit evidence and SHALL NOT be silently inferred or downgraded.

#### Scenario: Production requires all layers
- **WHEN** production verification is requested with no Layer 2 case, no Layer 3 case, or a required layer not executed
- **THEN** verification fails and publication remains unavailable

#### Scenario: Compatibility profile is explicit
- **WHEN** a trusted embedded host uses structural-only compatibility verification
- **THEN** the result records the compatibility profile and absent layers, and it cannot be reported as production three-layer verification

### Requirement: Layer 1 validates the frozen semantic candidate
Layer 1 SHALL be core-owned and SHALL validate the frozen approved draft revision, review bindings, accepted-assertion manifest equivalence, emitted Bundle structure and references, source and business identity, calculated-field invariants, compatibility, tenant/source scope, and verification-plan binding. Layer 1 SHALL run without database access and SHALL emit only bounded issue codes and evidence fingerprints.

#### Scenario: Structural mismatch fails before external work
- **WHEN** the draft, plan, manifest, emitted Bundle, source scope, or semantic references disagree
- **THEN** Layer 1 fails and no Layer 2/3 executor or catalog write is invoked

### Requirement: Layer 2 executes governed smoke cases
Each Layer 2 case SHALL contain a validated canonical `SemanticQueryIR` or a deterministic governed request that resolves to one before execution, a deployment/fixture profile reference, and a closed set of protected assertions: successful outcome, result shape, bounded row count, bounded scalar equality, null behavior, or structured error code. The runner SHALL execute through existing view, planning, compilation, governance, authorization, adapter, and result-protection boundaries against the frozen candidate Bundle. Raw backend query text, physical names, and unrestricted result rows SHALL not enter the plan or persisted evidence.

#### Scenario: Smoke case passes through governed execution
- **WHEN** a required smoke case executes successfully and all protected assertions match
- **THEN** Layer 2 records a passing case result linked to the plan, candidate Bundle, draft revision, executor identity, and protected result fingerprint

#### Scenario: Smoke mismatch blocks verification
- **WHEN** execution fails, protected output differs, or the case cannot bind to the frozen candidate Bundle
- **THEN** the case and Layer 2 fail without returning raw rows or backend exception text

### Requirement: Layer 3 evaluates a closed semantic contract DSL
Each Layer 3 case SHALL express one or more bounded semantic contracts using a closed operator set over protected outputs, including exact protected result, scalar equality, row-count relation, aggregate total, governed mapping outcome, null behavior, and structured error-code equality. Contracts SHALL reference semantic field or selection identifiers only and SHALL NOT contain arbitrary expressions, Python, SQL/MQL, regex, function calls, physical names, or host callbacks. The evaluator SHALL be deterministic and fail closed on unsupported operators or type mismatches.

#### Scenario: Semantic contract detects drift
- **WHEN** a mapping, calculated field, aggregation, or null-policy change causes a protected outcome to violate its declared semantic contract
- **THEN** Layer 3 fails with a bounded contract/case reference and publication is blocked

#### Scenario: Executable contract material is impossible
- **WHEN** a contract input attempts to include code, backend syntax, an unknown operator, or a physical identifier
- **THEN** structural validation rejects the contract before execution

### Requirement: Required outcomes are fail-closed and deadline bounded
The runner SHALL classify every layer and case as `passed`, `failed`, `skipped`, `unavailable`, `timed_out`, or `not_run`. Only `passed` satisfies a required case or layer. `skipped`, `unavailable`, timeout, exception, cancellation, missing executor capability, missing secret resolver, or missing fixture SHALL fail required verification. Every case, layer, and suite SHALL have bounded deadlines and cancellation propagation, and cleanup SHALL always be attempted without masking the primary result.

#### Scenario: Unavailable is not a pass
- **WHEN** a required database, deployment binding, fixture, executor capability, or credential resolver is unavailable
- **THEN** verification records `unavailable`, the suite fails, and publish creates no artifact

#### Scenario: Timeout is bounded and safe
- **WHEN** a case exceeds its deadline
- **THEN** it is cancelled or abandoned according to the executor contract, cleanup is attempted, and persisted evidence contains only `timed_out` plus bounded safe references

### Requirement: Verification evidence is deterministic and safe
The suite SHALL emit frozen `VerificationCaseEvidence`, `VerificationLayerEvidence`, and `VerificationSuiteEvidence` models. Evidence SHALL bind plan fingerprint/version, policy profile/version, frozen draft revision, candidate Bundle fingerprint, manifest fingerprint, runner identity/version, executor capability fingerprint, layer/case IDs, statuses, durations excluded from semantic identity, protected result/evidence fingerprints, and bounded issue codes. Persisted or serialized evidence SHALL contain no raw rows, scalar values, prompts, queries, SQL/MQL, physical names, credentials, connection references, native objects, or unrestricted exceptions.

#### Scenario: Evidence fingerprint is repeatable
- **WHEN** the same deterministic plan executes against the same candidate and protected outcomes
- **THEN** suite evidence has the same fingerprint regardless of wall-clock duration or case execution scheduling

#### Scenario: Evidence serialization is redacted
- **WHEN** suite evidence and publish audit are serialized
- **THEN** only bounded statuses, identifiers, counts, versions, and fingerprints are present

### Requirement: Runner and executor identity drift fails closed
The verification context and evidence SHALL carry a core runner identity and the identity/capability fingerprint of each external executor. Pre-publication verification SHALL reject one-sided identity, version drift, plan drift, candidate Bundle drift, draft-revision drift, manifest drift, or evidence from another tenant/source context. Legacy evidence SHALL be accepted only under an explicit compatibility profile.

#### Scenario: Stale verification cannot publish changed content
- **WHEN** an assertion, verification plan, draft revision, candidate Bundle, executor identity, or required capability changes after verification
- **THEN** the prior evidence is rejected and the current frozen inputs must be verified again

### Requirement: Verification plans and evidence use the shared canonical JSON profile
Verification plans, case evidence, layer evidence, and suite evidence SHALL use the shared fingerprint-critical canonical JSON profile for canonical bytes and fingerprints. Durations, scheduling order, cleanup timing, backend exception text, raw rows, scalar values, SQL/MQL, physical names, credentials, deployment references, and native objects SHALL remain excluded or rejected according to the existing evidence safety rules.

#### Scenario: Verification evidence profile is repeatable
- **WHEN** the same deterministic plan executes against the same candidate and protected outcomes
- **THEN** the Verification Suite evidence canonical bytes and fingerprint are stable under the shared canonical JSON profile regardless of case scheduling or wall-clock duration

#### Scenario: Unsupported evidence value is rejected
- **WHEN** verification plan or evidence construction attempts to include a native object, non-finite number, raw result value, SQL/MQL, physical identifier, credential, or unrestricted backend exception
- **THEN** construction or canonicalization fails closed before evidence is persisted or accepted for publication

