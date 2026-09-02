# Baseline Test Results

Captured: 2026-08-31

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Result:

```text
2397 passed, 63 skipped, 5 xfailed, 4 warnings in 124.23s (0:02:04)
```

Warnings observed:

- `PytestUnknownMarkWarning` for `pytest.mark.integration` in
  `tests/integration/test_mainflow_demo.py`
- `PytestUnknownMarkWarning` for `pytest.mark.integration` in
  `tests/integration/test_mainflow_demo_real.py`

This run was captured before moving implementation code for the semantic
control-plane refactor.