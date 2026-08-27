## ADDED Requirements

### Requirement: Documentation has a navigable reader-oriented structure
The project SHALL provide a documentation index linking to getting-started, guides, architecture, development, operations, and reference sections. Each section SHALL identify its intended reader and prerequisites.

#### Scenario: New user finds a working path
- **WHEN** a new user opens the repository README or docs index
- **THEN** they can reach installation, quickstart, and first-query instructions without reading OpenSpec history

#### Scenario: Operator finds deployment prerequisites
- **WHEN** an operator needs PostgreSQL state, Redis Memory, metadata discovery, or secret guidance
- **THEN** the docs index leads to task-specific operational guidance

### Requirement: Documentation supports staged English and Chinese access
The project SHALL provide English as the normative documentation language and a staged Chinese translation for the highest-value reader paths: documentation index, installation/quickstart, architecture overview and execution flow, evidence-and-fingerprints, configuration, secrets, and capability/compatibility reference. Paired pages SHALL link to each other, identify translation status/source, and preserve code, identifiers, Mermaid meaning, normative requirements, and security warnings. Untranslated pages MAY remain English-first when the index makes that status clear.

#### Scenario: Reader switches language at an entry point
- **WHEN** a reader opens the root README or docs index
- **THEN** they can reach the English canonical page and the available Chinese translation, with no broken language link

#### Scenario: Translation does not alter a contract
- **WHEN** an English page and its Chinese translation describe an API, configuration key, fingerprint rule, or security constraint
- **THEN** identifiers and normative behavior remain equivalent, while English remains the source of truth for resolving discrepancies

#### Scenario: Partial translation is honest
- **WHEN** a requested page has no Chinese translation yet
- **THEN** the docs navigation labels it as English-first or links directly to the English canonical page rather than implying complete bilingual coverage

### Requirement: README is a concise and accurate entry point
The root README SHALL summarize product position, supported packages, installation, minimal public usage, capability/support status, limitations, and links to deeper documentation. Detailed architecture and operational contracts SHALL live under `docs/`.

#### Scenario: README remains useful without internal knowledge
- **WHEN** a reader knows only Python packaging and the public `nl2data` import
- **THEN** the README provides a valid next step without requiring internal module knowledge

#### Scenario: README does not overclaim support
- **WHEN** a capability depends on optional services, credentials, or host integration
- **THEN** the README labels those prerequisites and distinguishes implemented, verified, and production-supported status

### Requirement: User and developer guides are executable and safe
Getting-started and developer documentation SHALL include tested installation, composition, query, testing, adapter/provider extension, and local service examples. Examples SHALL use supported public boundaries where applicable and SHALL never persist or embed credentials, raw prompts, raw query artifacts, or raw result payloads.

#### Scenario: Quickstart matches the public API
- **WHEN** a clean environment follows the quickstart
- **THEN** imports, initialization, query lifecycle, and protected outcome handling match the implemented public API

#### Scenario: Live integration uses ephemeral secrets
- **WHEN** a developer follows live PostgreSQL, Redis, MongoDB, or AI instructions
- **THEN** credentials/endpoints are supplied through temporary environment or host secret injection and are removed after use

### Requirement: Architecture and operations are documented
Architecture docs SHALL explain Semantic Query IR, Semantic View/Bundle, instruction/provider, compiler/governance, tenant isolation, workflow state, Memory, and evidence boundaries. Architecture docs SHALL also explain Semantic View resolution and projection consumption by the runtime, including multi-root data sources and the one-root-per-query physical compilation boundary. Operations docs SHALL explain service prerequisites, configuration, health/failure behavior, retention, drift, rollback, and at-least-once semantics.

#### Scenario: Responsibility boundary is clear
- **WHEN** a reader asks who owns system instructions, provider calls, compilation, authorization, or result protection
- **THEN** the architecture docs identify the owning layer and the relevant contract

#### Scenario: Failure and rollback behavior is discoverable
- **WHEN** a service or metadata source is unavailable or a snapshot drifts
- **THEN** the operations docs describe safe failure, active-state preservation, recovery, and rollback

#### Scenario: Multi-root source is understood
- **WHEN** a source exposes several root entities across physical objects
- **THEN** the architecture docs explain that every query selects exactly one root entity, that a view may authorize many roots, and that each profile compiles against one physical object

### Requirement: Composition profile inputs are documented field by field
The project SHALL provide a reference page that documents every public `CompositionProfile` field (port fields, opaque deterministic parts, scalar settings) with its source, default, executability role, and construction examples covering a pre-built runtime port, the deterministic parts, metadata-lifecycle-derived projections, and multi-root data sources.

#### Scenario: Reader composes a deterministic profile
- **WHEN** an integrator binds adapter, policy scope, view, and plan resolver
- **THEN** the reference explains each field, the required four-part gate, the safe NOT_CONFIGURED fallback, and the optional role of the physical binding

#### Scenario: Reader composes a lifecycle profile
- **WHEN** a host resolves a Semantic View projection from the metadata lifecycle
- **THEN** the reference shows how `projection` and `AuthorizedView.from_projection` fold into the profile and what evidence the runtime derives from them

#### Scenario: Reader composes for multiple roots
- **WHEN** a data source has several root tables
- **THEN** the reference shows one profile per physical object with object-scoped policy, view, binding, and plan resolver

### Requirement: Architecture documentation is visual and accessible
Architecture documentation SHALL combine explanatory prose with source-controlled Mermaid diagrams for the end-to-end execution flow, component/package boundaries, governance and authorization boundaries, workflow-state lifecycle, and metadata-to-bundle lifecycle. Each diagram SHALL state the reader question it answers, identify ownership and trust boundaries, and have a nearby text explanation that remains meaningful when diagrams are not rendered.

#### Scenario: Reader can trace a governed query
- **WHEN** a reader opens the architecture overview or execution-flow page
- **THEN** they can follow the request from instruction/provider input through Semantic IR, compilation, guard, governance, authorization, adapter execution, and result protection using both a diagram and explanatory text

#### Scenario: Diagram communicates a security boundary
- **WHEN** a reader opens the governance, tenant-isolation, or package-boundary page
- **THEN** the diagram labels the relevant trust/ownership boundary and the prose explains which layer may or may not make the protected decision

#### Scenario: Diagram source remains reviewable
- **WHEN** CI checks documentation
- **THEN** Mermaid blocks and their internal links pass the available structural/link checks, and documentation review can compare diagram labels with source contracts

### Requirement: Fingerprint design is explained in Why, What, How form
The architecture documentation SHALL provide a dedicated evidence-and-fingerprints guide that explains: **Why** deterministic opaque fingerprints support reproducibility, compatibility checks, authorization binding, cache/idempotency correlation, drift detection, and safe telemetry; **What** artifacts and contexts receive fingerprints and which secrets, raw prompts, raw queries/results, credentials, native objects, and unapproved tenant identifiers are excluded; and **How** canonicalization, sensitivity filtering, SHA-256 formatting, propagation, comparison, mismatch handling, and intentional version/rotation changes work. The guide SHALL include a Mermaid fingerprint dependency/lifecycle diagram and a safe canonicalization example.

#### Scenario: Reader distinguishes identity from authorization
- **WHEN** a reader uses a fingerprint in evidence, workflow state, or governance
- **THEN** the guide explains that a fingerprint is an opaque identity/reference and does not itself grant authorization or expose the source payload

#### Scenario: Equivalent safe inputs remain comparable
- **WHEN** equivalent safe payloads differ only in mapping key insertion order
- **THEN** the guide explains that canonical serialization produces the same `sha256:<lowercase hex>` fingerprint

#### Scenario: Sensitive or incompatible changes fail safely
- **WHEN** a fingerprint input contains excluded sensitive data or a later context fingerprint no longer matches
- **THEN** the guide explains filtering/rejection and the resulting compatibility, revalidation, or rollback behavior without suggesting plaintext recovery

### Requirement: Reference documentation matches implementation
Reference pages SHALL document configuration fields, error codes, package extras/distributions, capability/support matrix, compatibility policy, and security constraints based on current source, tests, and specifications. CI SHALL validate Markdown links and selected code/import examples.

#### Scenario: Documented command is verifiable
- **WHEN** CI runs the documented test, lint, type-check, or build command
- **THEN** the command succeeds or its optional-service skip behavior is explicitly expected

#### Scenario: Broken internal link is rejected
- **WHEN** a documentation link targets a missing repository file
- **THEN** the documentation check fails before merge
