## ADDED Requirements

### Requirement: Recalled memory is context, not authority
The multi-turn resolver SHALL use recalled memory only to assemble bounded provider context and SHALL revalidate current tenant scope, semantic view, policy fingerprint, catalog fingerprint, and adapter/artifact references before inheriting prior logical scope.

#### Scenario: Fresh compatible reference can guide follow-up intent
- **WHEN** a recalled query reference matches the current trusted scope and current policy/catalog view
- **THEN** it may be included in provider-safe context for the next intent resolution

#### Scenario: Stale reference cannot authorize execution
- **WHEN** a recalled reference has a stale policy, catalog, tenant scope, or artifact fingerprint
- **THEN** it is rejected or converted into a bounded clarification and no adapter execution occurs from the stale reference

### Requirement: Follow-up context is bounded and safe
The resolver SHALL expose only bounded semantic references, confirmed decisions, and fingerprints to the model provider, excluding raw prior prompts, results, credentials, and native objects.

#### Scenario: Prior result rows are never included
- **WHEN** a follow-up request refers to a prior result
- **THEN** the provider context contains only a protected logical reference, not the prior rows or documents

### Requirement: Memory-unavailable requests degrade safely
The resolver SHALL support stateless resolution when Memory is unavailable and the current request is independently resolvable; it SHALL not invent or silently substitute prior context.

#### Scenario: Stateless fallback is used
- **WHEN** Memory is unavailable and the current prompt contains sufficient intent
- **THEN** resolution proceeds without recalled records and records a safe memory-unavailable signal

#### Scenario: Ambiguous request does not invent history
- **WHEN** Memory is unavailable and the current prompt depends on an earlier reference
- **THEN** resolution requests clarification or rejects safely without fabricating prior scope