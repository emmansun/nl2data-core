## Context

The repository currently uses the root README as an overview, quickstart, architecture note, operations guide, and historical status log. OpenSpec specs provide normative development contracts, but they are not organized for application users or operators. The new documentation must reflect the implemented core plus the sibling OpenAI package, without inventing unsupported HTTP hosting or production deployment behavior.

## Goals / Non-Goals

**Goals:**

- Create a navigable documentation hierarchy for users, developers, architects, and operators.
- Keep README concise and link to deeper task-oriented guides.
- Make quickstarts copyable and aligned with the public `nl2data` API and package boundaries.
- Explain the full governed flow and security responsibilities in architecture docs.
- Document local/CI service profiles, live AI testing, secrets handling, failure semantics, and rollback.
- Add lightweight documentation checks to CI without introducing a documentation framework dependency unless justified.
- Treat OpenSpec and source models as facts to reconcile against, not content to duplicate blindly.

**Non-Goals:**

- Changing runtime behavior, public APIs, package names, or deployment architecture.
- Building a hosted documentation website or publishing platform.
- Copying every OpenSpec requirement into prose.
- Claiming general production support for unverified adapters, transports, or deployment topologies.
- Persisting secrets, live credentials, raw prompts, raw query results, or provider responses in examples.

## Decisions

### Documentation is organized by reader task

Use `docs/getting-started`, `docs/guides`, `docs/architecture`, `docs/development`, `docs/operations`, and `docs/reference`. The root README remains a product landing point for the library, not a complete manual. The sibling provider README remains install-focused and links to the core guides.

### English is normative; Chinese is staged translation

English documentation is the canonical source for technical contracts, API names, configuration keys, error codes, support claims, and diagrams. Chinese documentation is maintained as a reader-facing translation, not an independent specification. The first release translates the docs index, installation/quickstart, architecture overview and execution flow, evidence-and-fingerprints, configuration, secrets, and capability/compatibility pages; other pages may link to English until translated. Paired pages must link to each other and display a small synchronization marker (for example, the English source path and translation status). Translation must preserve code, identifiers, Mermaid node labels where precision matters, normative keywords, and security warnings.

### Architecture documentation is visual and explanatory

Architecture pages SHALL combine concise prose with rendered Mermaid diagrams and nearby explanations. The minimum visual set is an end-to-end execution flow, a component/package boundary diagram, a governance and authorization boundary view, a workflow-state sequence, and a metadata-to-bundle lifecycle. Each diagram must have a purpose statement, labeled ownership boundaries, and text explaining the security or lifecycle decision that the reader should take away. Diagrams are source-controlled Markdown, not screenshots, so they remain reviewable and maintainable.

The architecture set SHALL include a dedicated evidence-and-fingerprints page organized as **Why, What, How**. It must explain why deterministic opaque identities are needed for reproducibility, compatibility checks, authorization binding, cache/idempotency correlation, drift detection, and safe telemetry; what is fingerprinted and what is deliberately excluded; and how canonicalization, SHA-256 formatting, sensitivity filtering, propagation, comparison, and rotation/version changes work. The page should include a Mermaid dependency/lifecycle diagram and at least one canonicalization example without secrets or raw user data.

### Examples use public boundaries and safe defaults

User-facing examples import from `nl2data` where possible. Internal package examples are explicitly labeled for adapter/provider authors. Every live-service example reads credentials and endpoints from environment variables or host secret injection; no example contains a real token, DSN, or persisted prompt/result artifact.

### One source of truth for versioned facts

Configuration names, error codes, capability matrices, package versions, and support claims are derived from `pyproject.toml`, source models, tests, and OpenSpec specs. Documentation task completion includes a consistency review so stale claims are corrected rather than copied forward.

### Documentation checks stay lightweight

CI should validate Markdown syntax/links and execute a small set of import/quickstart smoke checks. A full static-site generator is not required for the first pass. If a checker requires a third-party package, pin it in a dedicated documentation/dev dependency only after the check proves useful locally.

Diagram source and Mermaid fences should also be checked for basic syntax/structure in CI where practical. A full browser rendering pipeline is optional for the first pass, but architecture pages must retain readable text equivalents so that diagrams are not the sole representation of a contract.

### Production status is explicit

Documentation distinguishes `Implemented`, `Conformant`, `Verified`, and `Production Supported`. Real PostgreSQL/Redis/MongoDB and OpenAI tests are environment-dependent; the docs must state whether a command uses fake clients, service containers, or live credentials.

### Language quality is validated without duplicating authority

Documentation checks SHALL validate both language trees for broken links, required bilingual entry points, and matching code/example blocks where practical. They do not require byte-for-byte parity: English remains the authority, and a Chinese page may intentionally be narrower when it clearly links to the full English source.

## Risks / Trade-offs

- [Documentation becomes another stale copy] → Add CI link/example checks and a source/spec consistency checklist.
- [README shrink loses discoverability] → Keep a clear capability map and links to every major guide.
- [Examples leak operational secrets] → Use placeholders/environment variables and add secret-pattern scans.
- [Production claims are overstated] → Label service profiles and known limitations explicitly.
- [Too many documents overwhelm readers] → Start with a small high-value set and a single docs index.
- [Diagrams become decorative or stale] → Give every diagram a stated reader question, keep it adjacent to authoritative prose, and include diagram/source consistency in the documentation review.
- [Fingerprint terminology hides security semantics] → Make the evidence-and-fingerprints page explicit about identity versus authorization, canonical inputs, excluded data, propagation, mismatch behavior, and non-reversibility.

## Migration Plan

1. Create the docs index and minimum user/developer/architecture/operations/reference pages.
2. Move detailed README sections into the appropriate guides without changing technical claims.
3. Reduce README and align the OpenAI package README with the new navigation.
4. Add Markdown/link/example checks to CI.
5. Review all commands and support claims against tests and package metadata.
6. Roll back by restoring the previous README and removing docs-only CI checks; no runtime migration is involved.

## Open Questions

- Whether to adopt MkDocs, Sphinx, or another static-site generator after the first documentation set stabilizes.
- Whether API reference generation should be included in a later release workflow.
- Which deployment scenarios should receive dedicated operator runbooks once an HTTP host exists.
