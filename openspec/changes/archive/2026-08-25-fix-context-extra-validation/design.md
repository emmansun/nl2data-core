## Context

`IntentResolver.resolve` accepts optional `context_extra` that is merged into the provider payload; the memory stage uses it to project recalled references into provider context. `_validate_context_extra` rejects keys whose lowercased name contains any of `password`, `credential`, `secret`, `token`, keys in the unsafe-key set (`credential(s)`, `password`, `secret`, `token`, `dsn`, `query`, `sql`, `mql`, `command`, `code`, `instructions`, `system`), non-JSON values, and strings carrying instruction/query text.

The memory projection (`MemoryContextProjection.safe_payload()`) embeds `AuthorizedModelContext.safe_payload()`, which includes the bounded integer field `max_output_tokens`. Because `"token" in "max_output_tokens"` is true, every recalled follow-up turn is rejected with `UNSAFE_INSTRUCTION_CONTENT` before the provider is invoked. The first turn succeeds because there is no recalled projection yet.

## Goals / Non-Goals

**Goals:**

- Allow the memory projection's authorized bounded configuration fields through the extra-context guard.
- Preserve rejection of all credential/secret/token-shaped keys, instruction overrides, query text, native objects, and non-JSON values.
- Add focused unit tests for the validator boundary and re-enable the six failing memory tests.

**Non-Goals:**

- Change the memory projection payload shape, the authorized model context shape, or any fingerprint.
- Relax any other safety check (unsafe output scanning, instruction bundle validation, view membership, governance).
- Modify provider, workflow runtime, or evaluation behavior.

## Decisions

### Allowlist the bounded configuration field instead of widening the marker set

Change the marker check to skip a small allowlist of authorized context keys (`max_output_tokens`) before applying the substring markers. Renaming or removing the `token` marker would weaken protection for credential-shaped keys such as `api_token`, which must remain rejected. Removing `max_output_tokens` from the projection payload would change the authorized model context shape and its fingerprints, affecting instruction identity evidence, so it is rejected as an alternative.

### Keep the validation location in the resolver

The guard stays in `_validate_context_extra` where the intent is documented and tested; no new validation path is introduced for the memory stage.
