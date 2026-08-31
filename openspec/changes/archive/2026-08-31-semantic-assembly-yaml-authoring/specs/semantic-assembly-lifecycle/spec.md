## ADDED Requirements

### Requirement: Authoring import cannot grant lifecycle authority
A semantic assembly authoring document SHALL enter the lifecycle only through a trusted lowering boundary that receives host-authorized `draft_id` and author identity separately from YAML. The boundary SHALL create a revision-zero `DRAFT` whose assertions are derived, manual, and pending, and SHALL persist it only through the existing tenant-scoped draft store and Admin creation authorization. Authoring validation or import SHALL NOT review, approve, publish, activate, or create audit evidence.

#### Scenario: Imported content starts pending
- **WHEN** a valid authoring document is imported by an authorized Author
- **THEN** the persisted draft is at revision `0`, all assertions are pending, and review and approval metadata are absent

#### Scenario: Caller cannot smuggle reviewed assertions
- **WHEN** YAML attempts to contain assertion IDs, review decisions, revision values, or approval identities
- **THEN** authoring validation rejects the document before the lifecycle draft store is called

#### Scenario: Import uses tenant-scoped create semantics
- **WHEN** two authorized tenants import documents using the same draft ID
- **THEN** each tenant receives an isolated draft, while a duplicate ID within one tenant follows the existing create conflict behavior
