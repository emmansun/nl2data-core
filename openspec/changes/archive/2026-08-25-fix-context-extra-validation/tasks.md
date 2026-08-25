## 1. Regression Fix

- [x] 1.1 Reproduce and characterize the regression: a second-turn memory request is rejected with `UNSAFE_INSTRUCTION_CONTENT` and reason `context_extra.max_output_tokens is not an authorized context field`.
- [x] 1.2 Refine `_validate_context_extra` in `src/nl2data_core/ai/resolver.py` to allow authorized bounded configuration keys (notably `max_output_tokens`) while keeping credential/secret/token-shaped keys, unsafe keys, instruction overrides, query text, native objects, and non-JSON values rejected.

## 2. Verification

- [x] 2.1 Add unit tests for the validator: bounded config fields pass; credential-shaped keys (`api_token`, `access_token`, `password`, `secret`), unsafe keys (`sql`, `instructions`, `system`), non-JSON values, and unsafe text still fail.
- [x] 2.2 Confirm the six previously failing memory tests pass again (3 in `tests/integration/test_memory_multiturn.py`, 3 in `tests/security/test_memory_security.py`).
- [x] 2.3 Run the full pytest suite, mypy (both `src` and `packages/nl2data-openai/src`), and ruff; all green.
