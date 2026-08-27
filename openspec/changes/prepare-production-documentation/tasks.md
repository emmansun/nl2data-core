## 1. Documentation Structure and Entry Points

- [x] 1.1 Create the `docs/` hierarchy, language navigation, and a reader-oriented documentation index.
- [x] 1.2 Reduce the root README to product position, installation, minimal public example, support status, limitations, and documentation links.
- [x] 1.3 Expand `packages/nl2data-openai/README.md` with installation, credential injection, gateway compatibility, live testing, and rollback guidance.
- [x] 1.4 Add English/Chinese navigation and synchronization markers, with English identified as the normative source and untranslated pages labeled clearly.

## 2. User and Developer Guides

- [x] 2.1 Write a tested getting-started installation and quickstart using only supported public imports.
- [x] 2.2 Document composition, query lifecycle, protected outcomes, clarification, cancellation, and capability/health operations.
- [x] 2.3 Document local development setup, virtual environment, unit/integration tests, lint, type checking, package builds, and service prerequisites.
- [x] 2.4 Document how to add an adapter or provider without bypassing IR, compiler, governance, authorization, or result protection boundaries.

## 3. Architecture Documentation

- [x] 3.1 Add a Mermaid end-to-end execution-flow diagram and explain the path from prompt/instruction through provider, Semantic IR, View/Bundle, compiler, guard, governance, adapter, and result protection.
- [x] 3.2 Add a Mermaid component/package-boundary diagram and document public/internal imports plus optional dependency loading behavior.
- [x] 3.3 Add governance, authorization, and tenant-isolation diagrams with text explaining trust boundaries and decision ownership.
- [x] 3.4 Add a workflow-state sequence diagram covering leases, fencing, idempotency, persistence, and at-least-once execution semantics.
- [x] 3.5 Add a metadata discovery/inference/review/Bundle publication lifecycle diagram and explain schema drift behavior.
- [x] 3.6 Give each architecture diagram a reader question, labels, and a non-visual text equivalent; check Mermaid structure and rendered readability locally.
- [x] 3.7 Write `evidence-and-fingerprints.md` in Why/What/How form, covering canonicalization, SHA-256 identity, sensitivity exclusions, propagation, mismatch handling, and intentional version/rotation changes.
- [x] 3.8 Add a Mermaid fingerprint dependency/lifecycle diagram and a safe key-order canonicalization example; verify that examples contain no credentials, raw prompts, queries, or results.
- [x] 3.9 Translate the architecture overview, execution flow, and evidence-and-fingerprints pages into Chinese while preserving Mermaid meaning, identifiers, security warnings, and Why/What/How structure.

## 4. Operations and Reference

- [x] 4.1 Document PostgreSQL, Redis, MongoDB, and OpenAI configuration, health checks, timeouts, retries, and failure classification.
- [x] 4.2 Document secrets/token handling, environment injection, live AI tests, service integration profiles, and cleanup/rollback.
- [x] 4.3 Add configuration, error-code, capability/support, compatibility, and production-readiness reference pages.
- [x] 4.4 Add a troubleshooting guide for unavailable services, stale snapshots, lease/fencing conflicts, provider errors, and import-boundary issues.
- [x] 4.5 Translate installation/quickstart, configuration, secrets, and capability/compatibility reference pages into Chinese; link every translation to its English source.

## 5. Documentation Quality Gates

- [x] 5.1 Add Markdown/link validation to CI, fail on broken repository links, and structurally check Mermaid blocks.
- [x] 5.2 Add smoke checks for documented imports, quickstart code, package installation, and build commands.
- [x] 5.3 Add scans preventing tokens, DSNs, credentials, raw prompts, and raw provider/result payloads from documentation and examples.
- [x] 5.4 Reconcile documentation claims with source models, tests, OpenSpec specs, and package metadata; run full CI checks.
- [x] 5.5 Validate bilingual navigation, translation status markers, internal links, and representative English/Chinese code examples without requiring exact prose parity.

## 6. Semantic Layer and Composition Reference

- [x] 6.1 Add an architecture page explaining the Semantic View/Bundle layer (descriptor, view definition, resolution context, projection, authorized view, runtime consumption) with a Mermaid diagram, reader question, and text equivalent; document multi-root semantics and the one-root-per-query physical boundary (English normative + Chinese translation).
- [x] 6.2 Add a field-by-field `CompositionProfile` reference page with construction examples (pre-built runtime port, deterministic parts, metadata-lifecycle projection, multi-root data source), reconciled with the source model (English normative + Chinese translation).
- [x] 6.3 Correct the misleading "(all optional)" wording in the composition guide; document the executability gate (pre-built runtime or the four deterministic parts) and link the reference page.
- [x] 6.4 Register the new pages in the bilingual docs index, fix related-page links, and pass all documentation quality gates (check_docs, openspec validate, full test suite).
