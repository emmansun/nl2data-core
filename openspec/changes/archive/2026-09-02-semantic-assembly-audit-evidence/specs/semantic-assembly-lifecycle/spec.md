## ADDED Requirements

### Requirement: Assembly lifecycle emits a coherent audit-evidence trail
The assembly lifecycle SHALL represent completed authoring/import, lint, assertion review, approval, verification, publication, activation, and rollback actions as bounded audit-evidence entries linked into a coherent trail. Each entry SHALL carry a stable event identity, event kind, subject reference, tenant/source scope fingerprints, relevant draft revision or artifact fingerprints, safe outcome/status, optional host-provided operator audit reference, predecessor references, and an entry fingerprint. Audit-evidence entries SHALL remain outside semantic Bundle fingerprints and SHALL NOT grant lifecycle authority.

#### Scenario: Review decision creates bounded evidence
- **WHEN** an authorized reviewer approves, rejects, or edits an assertion at a current draft revision
- **THEN** the lifecycle can expose a bounded audit-evidence entry linking the assertion ID, reviewed payload hash, draft revision, decision outcome, and operator audit reference without exposing raw operator identity or unsafe payload material

#### Scenario: Later draft edits do not rewrite old evidence
- **WHEN** a draft changes after prior review, approval, lint, or verification actions
- **THEN** prior audit-evidence entries remain linked to the revision/fingerprint facts that were current when the action completed and are not recomputed from the newer draft state

### Requirement: Publication audit evidence binds release readiness inputs
Publication SHALL create or reference a bounded audit-evidence entry that binds the approved draft revision, approved verification-plan fingerprint, accepted-assertion manifest fingerprint, Verification Suite evidence fingerprint, selected verification policy profile/version, lint readiness reference when present, tenant/source scope fingerprints, separation-of-duties result, publish audit reference, and immutable Bundle fingerprint. This publication audit evidence SHALL be validated before catalog persistence and SHALL remain outside the Bundle semantic fingerprint domain.

#### Scenario: Publication evidence explains authoritative release
- **WHEN** an approved draft is successfully published after required verification passes
- **THEN** the publication audit-evidence entry links the release readiness inputs and resulting Bundle fingerprint without including canonical semantic bytes, raw assertions, raw queries, credentials, physical names, or resolved deployment values

#### Scenario: Mismatched publication evidence fails closed
- **WHEN** publication audit evidence references a different draft revision, plan fingerprint, manifest fingerprint, verification evidence fingerprint, tenant/source scope, policy profile, or Bundle fingerprint than the publication aggregate
- **THEN** publication fails before catalog persistence and exposes no partial Bundle, audit, evidence, or supersession record

### Requirement: Activation and rollback preserve historical evidence links
Activation and rollback SHALL record bounded audit-evidence entries that link the requested Bundle fingerprint, prior active fingerprint when present, resulting active fingerprint, operator audit reference, tenant/source scope, validation outcome, and predecessor publication evidence. Rollback SHALL NOT republish semantic content or create a new semantic Bundle fingerprint.

#### Scenario: Activation links to publication evidence
- **WHEN** an operator activates a published Bundle
- **THEN** the activation audit-evidence entry links to the immutable publication evidence for that Bundle and records the active-pointer outcome without mutating the published artifact

#### Scenario: Rollback evidence keeps both versions explainable
- **WHEN** an operator rolls back to a prior valid Bundle fingerprint
- **THEN** the rollback audit-evidence entry identifies the previous active fingerprint, restored fingerprint, operator audit reference, and predecessor publication evidence for both versions without republishing either Bundle
