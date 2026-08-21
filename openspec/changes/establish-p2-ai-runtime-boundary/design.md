## Context

P1 accepts a structured, immutable Semantic Query Plan and routes it through SQL validation, governance, execution authorization, and protected results. It deliberately has no natural-language interpretation or model-provider dependency. The next capability must introduce AI without weakening that boundary or coupling the core to OpenAI, LangChain, a hosted API, or an agent framework.

The AI layer will be consumed later by Memory, multi-turn workflow, HTTP hosting, and additional Query Adapters. It therefore needs stable typed inputs and outputs, deterministic test doubles, bounded resource use, and an explicit handoff to semantic planning.

## Goals / Non-Goals

**Goals:**

- Define a provider-neutral asynchronous model invocation port for structured output.
- Convert natural-language requests into validated intent, clarification, or safe failure.
- Keep raw prompts and provider responses outside Semantic Query Plan, Governance, audit, and public result models unless an explicit protected evidence policy permits references.
- Provide deterministic fake-provider evaluation for normal, ambiguous, malformed, timeout, and prompt-injection cases.
- Make model calls bounded by timeout, attempts, input size, output size, and token/usage metadata.
- Preserve the existing P1 plan contract and ensure model output cannot directly execute SQL or MQL.

**Non-Goals:**

- A production provider integration or vendor SDK.
- Prompt catalog authoring, few-shot retrieval, embeddings, vector storage, or answer summarization.
- Autonomous agent loops, tool calling, model-written SQL/MQL, or direct database access from a model provider.
- Memory persistence, multi-turn state, HTTP hosting, or GitHub Actions.

## Decisions

1. **Use a structured-output `ModelProvider` port.** The provider accepts a bounded invocation request and returns a typed JSON-compatible response envelope or a normalized model error. It never receives database clients or unfiltered internal catalog objects. A provider-specific client interface was rejected because it would leak vendor semantics into the core.

2. **Separate model invocation from intent resolution.** `ModelProvider` handles transport/model concerns; `IntentResolver` validates the returned structure, applies allowed semantic vocabulary, and emits `ResolvedIntent` or `ClarificationRequired`. Combining these layers was rejected because model output validation and business semantic resolution have different trust and test boundaries.

3. **Models emit intent, not executable queries.** The structured output may contain entity, metric, dimension, filter, time, ambiguity, and clarification references, but no raw SQL, MQL, shell text, AST, driver object, or authorization decision. The existing Semantic Query Plan builder remains the only path to adapter compilation.

4. **Keep provider dependencies optional and lazy.** P2.1 ships only a deterministic fake provider and protocols/models. Vendor adapters belong in optional packages or later changes. The core import boundary must work without network libraries or API credentials.

5. **Use immutable fingerprints and safe evidence references.** Invocation configuration, structured output, resolved intent, and clarification payloads receive canonical fingerprints. Logs and evaluation reports store fingerprints, bounded metadata, and normalized error types rather than raw prompt or provider payloads by default.

6. **Fail closed on malformed or unsafe model output.** Invalid schema, unsupported semantic IDs, executable-query fields, oversized output, and prompt-injection markers that attempt to alter system constraints produce a structured failure or clarification result. They never fall through to plan compilation.

7. **Use deterministic evaluation before live-provider evaluation.** The evaluation runner injects fixed provider responses and verifies intent equivalence, clarification behavior, redaction, bounded calls, and rejection of executable output. Live provider conformance is deferred until a provider package and credential policy exist.

## Risks / Trade-offs

- [Risk] A generic structured intent may be too small for complex business questions. → Version the intent contract and add fields through compatible capability changes rather than embedding provider-specific blobs.
- [Risk] Prompt-injection detection based on patterns can be incomplete. → Treat detection as defense in depth; never rely on it for authorization, and always require semantic validation and governance after resolution.
- [Risk] Provider retries can duplicate billable calls. → Enforce request-wide attempt budgets and expose safe usage metadata for later FinOps integration.
- [Risk] Different providers may interpret the same question differently. → Compare only validated structured intent and plan properties in conformance tests; do not claim provider equivalence from text similarity.
- [Risk] Redacting all prompts can make debugging difficult. → Permit opt-in protected evidence references and local test fixtures while keeping raw content out of normal logs and public contracts.

## Migration Plan

1. Add internal AI contracts and deterministic fake provider without changing P1 execution behavior.
2. Add intent resolution and a plan-builder handoff that is opt-in; requests without an AI workflow continue using the P1 not-configured or structured-plan paths.
3. Add contract and security evaluation cases before any vendor provider is introduced.
4. Add vendor integrations later behind optional packages and explicit configuration profiles.
5. Roll back by disabling the AI workflow binding; existing structured-plan and P1 adapter paths remain available.

## Open Questions

- Should the first intent vocabulary be limited to the current selection/filter/order model, or include clarification and time-range primitives immediately?
- Which provider-neutral usage fields are required for the later FinOps design?
- Should prompt-injection handling return a generic rejected outcome or a user-visible clarification request?
- Where should the first semantic catalog lookup port live: planning or a separate semantic package?