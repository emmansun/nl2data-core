# fix-context-extra-validation

## Why

Multi-turn memory follow-up is broken at HEAD: any second-turn request that recalls memory is rejected before the provider call with `UNSAFE_INSTRUCTION_CONTENT` and reason `context_extra.max_output_tokens is not an authorized context field`. The extra-context guard added in `412a4d3` ("feat: add model instruction contract") rejects any `context_extra` key whose lowercased name contains the substring `token`; the memory projection legitimately embeds the authorized model context, which includes the bounded `max_output_tokens` configuration field. Six existing tests fail on a clean checkout (3 in `tests/integration/test_memory_multiturn.py`, 3 in `tests/security/test_memory_security.py`), so the repository's own test suite is red at HEAD.

## What Changes

- Refine `_validate_context_extra` in `src/nl2data_core/ai/resolver.py` so authorized bounded configuration fields (notably `max_output_tokens`) pass the extra-context guard.
- Keep rejecting credential/secret/token-shaped keys, instruction overrides, physical query text, native objects, and non-JSON values exactly as before.
- Add unit tests covering the validator boundary (bounded config fields accepted; credential-shaped, unsafe, and non-JSON content still rejected).
- Confirm the six previously failing memory tests pass again and run the full suite, mypy, and ruff.

## Capabilities

### Modified Capabilities

- `model-instruction-contract`: Additional model context validation SHALL accept authorized bounded configuration fields (such as output-token limits) and SHALL NOT reject them solely because the field name contains a credential marker substring; credential/secret/query/instruction-shaped and non-JSON content remains rejected before provider invocation.

## Impact

A small, targeted fix in the core resolver's extra-context validation. It restores the intended multi-turn memory behavior (recalled references reach the provider context) without weakening the safety boundary. No provider package, workflow runtime, evaluation, or public API contract changes; the memory projection payload shape and fingerprints are untouched.
