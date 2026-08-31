# metadata-discovery-and-inference Delta

## MODIFIED Requirements

### Requirement: Semantic proposals are reviewed before bundle publication
The system SHALL generate bounded semantic proposals for entities, fields, types, relationships, grains, measures, synonyms, and classifications with trust/provenance metadata. Discovery proposals SHALL be adapted into semantic assembly assertions before they can become published bundle authority. Only assertions with explicit valid review decisions SHALL be eligible for approval and publication, and each proposal-derived assertion SHALL bind to its declared source snapshot fingerprint. An admin API review operation SHALL be an explicit authorized action and SHALL preserve immutable review history.

#### Scenario: Approved proposal becomes reviewed assertion
- **WHEN** a reviewer approves a valid proposal-derived assertion against a compatible snapshot and current draft revision
- **THEN** the resulting assembly draft marks the assertion approved, preserves proposal provenance as audit-side metadata, and makes the assertion eligible for publish-time bundle emission

#### Scenario: Unreviewed proposal remains inactive
- **WHEN** a proposal is inferred, observed, or LLM-suggested but has not produced a valid reviewed assertion
- **THEN** it cannot be published as active semantic model authority or used to grant View access

#### Scenario: Stale proposal-derived review is rejected
- **WHEN** a reviewer submits a decision for a proposal-derived assertion whose source snapshot fingerprint or draft revision no longer matches the current assembly draft
- **THEN** review returns a conflict or stale-source rejection and leaves the current review state unchanged

## ADDED Requirements

### Requirement: Discovery adapts proposals to deterministic assertions
Discovery and inference SHALL convert candidate semantic facts into deterministic `SemanticAssertion` identities when creating or updating an assembly draft. Assertion identity SHALL be stable across rediscovery when identity semantics are unchanged and SHALL not depend on proposal order, generation order, or random identifiers.

#### Scenario: Rediscovery preserves assertion identity
- **WHEN** the same source metadata fact is rediscovered with unchanged identity semantics
- **THEN** the generated assertion has the same ID as the prior assembly assertion

#### Scenario: Rediscovery reports changed payload
- **WHEN** rediscovery observes the same assertion identity with changed semantic payload
- **THEN** the assembly draft marks that assertion as modified and pending review while preserving unaffected assertion decisions

### Requirement: Incremental rediscovery replays safe review history
Incremental rediscovery SHALL compare new assertion candidates against a baseline published bundle or assembly draft by assertion ID and payload. It SHALL produce only added, modified, stale, or previously rejected candidates that require attention. Rejected assertions MAY be replayed as negative evidence according to policy but SHALL NOT become semantic payload.

#### Scenario: Unchanged assertions are omitted from review output
- **WHEN** incremental rediscovery finds assertions already reviewed with matching payload and valid binding
- **THEN** those assertions are not presented as new review work

#### Scenario: Previous rejection is remembered
- **WHEN** incremental rediscovery finds a candidate assertion that was previously rejected with the same identity and payload
- **THEN** the review output may default it to rejected negative evidence without adding it to canonical semantic payload

### Requirement: Suggested content cannot self-promote
LLM-suggested and statistical inference output SHALL enter assembly only as pending assertions or audit-side seed metadata. Confidence SHALL affect review presentation and ordering but SHALL NOT create a bypass around explicit review approval.

#### Scenario: High confidence does not bypass review
- **WHEN** discovery or inference produces a high-confidence semantic assertion
- **THEN** the assertion still requires a valid review decision before approval or publication

#### Scenario: LLM cannot alter provenance responsibility
- **WHEN** an LLM-suggested assertion is generated or enhanced
- **THEN** it cannot mark itself manual, approved, or publishable without a host-authorized human or governed review action