## ADDED Requirements

### Requirement: Tenant isolation has deterministic positive and negative cases
The conformance suite SHALL test valid same-tenant propagation, cross-tenant authorization mismatch, missing trusted context, inactive tenant, delegated scope, and namespace separation.

#### Scenario: Cross-tenant reuse fails
- **WHEN** a scope, authorization, workflow namespace, or cache key from tenant A is presented under tenant B
- **THEN** the conformance case fails closed and records no protected result

### Requirement: Isolation evidence is protected
Tenant conformance reports SHALL contain safe decision codes, scope fingerprints, profile metadata, and bounded reasons without raw credentials, tokens, or unrestricted identity claims.

#### Scenario: Adversarial tenant input is not leaked
- **WHEN** a client submits conflicting or malicious tenant claims
- **THEN** the report records a normalized denial without the raw claim value