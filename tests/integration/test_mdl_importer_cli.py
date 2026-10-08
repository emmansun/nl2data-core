"""Integration tests for the offline ``nl2data-mdl`` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nl2data_core.mdl_importer.cli import main

FIXTURES = Path(__file__).parents[1] / "fixtures" / "mdl_importer"
ACCEPTED = FIXTURES / "accepted_mdl.json"
COLLIDING = FIXTURES / "colliding_field_mdl.json"
MANIFEST = FIXTURES / "colliding_field_manifest.json"

_BASE_ARGS = [
    "--source-id",
    "warehouse",
    "--bundle-id",
    "sales",
    "--model-version",
    "1.0.0",
]


def _base(tmp_path: Path, **overrides: object) -> list[str]:
    args = ["import", "--input", str(ACCEPTED), *_BASE_ARGS]
    for key, value in overrides.items():
        if value is not None:
            args.extend([f"--{key.replace('_', '-')}", str(value)])
    return args


def test_valid_conversion_writes_deterministic_yaml(tmp_path: Path, capsys) -> None:
    output = tmp_path / "out.yaml"
    first = main(_base(tmp_path) + ["--output", str(output)])
    assert first == 0
    assert output.exists()
    first_bytes = output.read_bytes()

    second_output = tmp_path / "out2.yaml"
    second = main(_base(tmp_path) + ["--output", str(second_output)])
    assert second == 0
    assert second_output.read_bytes() == first_bytes
    assert b"refSql" not in first_bytes


def test_missing_required_arguments_fail_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "out.yaml"
    for incomplete in (
        ["import", "--input", str(ACCEPTED), "--output", str(output)],
        [
            "import",
            "--input",
            str(ACCEPTED),
            "--output",
            str(output),
            "--source-id",
            "warehouse",
        ],
    ):
        with pytest.raises(SystemExit) as excinfo:
            main(incomplete)
        assert excinfo.value.code == 2
    assert not output.exists()


def test_unsupported_mdl_fails_without_partial_output(tmp_path: Path, capsys) -> None:
    bad_input = tmp_path / "bad.json"
    bad_input.write_bytes(b'{"schemaVersion": "9.9", "models": []}')
    output = tmp_path / "out.yaml"
    exit_code = main(
        [
            "import",
            "--input",
            str(bad_input),
            "--output",
            str(output),
            *_BASE_ARGS,
        ]
    )
    assert exit_code == 1
    assert not output.exists()
    captured = capsys.readouterr()
    assert "unsupported_version" in captured.err


def test_json_diagnostics_are_machine_readable(tmp_path: Path, capsys) -> None:
    bad_input = tmp_path / "bad.json"
    bad_input.write_bytes(b"not json at all")
    output = tmp_path / "out.yaml"
    exit_code = main(
        [
            "import",
            "--input",
            str(bad_input),
            "--output",
            str(output),
            "--diagnostics",
            "json",
            *_BASE_ARGS,
        ]
    )
    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostics"][0]["code"] == "invalid_input"
    assert isinstance(payload["diagnostics"][0]["mark"]["line"], int)
    assert not output.exists()


def test_invalid_mapping_manifest_fails_with_stable_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b'{"version": "1", "unknown": {}}')
    output = tmp_path / "out.yaml"
    exit_code = main(_base(tmp_path) + ["--output", str(output), "--mapping", str(manifest)])
    assert exit_code == 1
    assert "invalid_manifest" in capsys.readouterr().err
    assert not output.exists()


def test_mapping_manifest_resolves_collisions(tmp_path: Path) -> None:
    output = tmp_path / "out.yaml"
    exit_code = main(
        [
            "import",
            "--input",
            str(COLLIDING),
            "--output",
            str(output),
            "--mapping",
            str(MANIFEST),
            *_BASE_ARGS,
        ]
    )
    assert exit_code == 0
    assert b"order_customer_id" in output.read_bytes()


def test_existing_output_refuses_without_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "out.yaml"
    assert main(_base(tmp_path) + ["--output", str(output)]) == 0
    original = output.read_bytes()
    assert main(_base(tmp_path) + ["--output", str(output)]) == 1
    assert output.read_bytes() == original
    assert main(_base(tmp_path) + ["--output", str(output), "--overwrite"]) == 0
    assert output.read_bytes() == original  # deterministic replacement


def test_human_diagnostics_render_code_path_and_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "out.yaml"
    output.write_text("existing")
    exit_code = main(_base(tmp_path) + ["--output", str(output)])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error [output_exists] $" in err
    assert "the output path already exists" in err
    assert "{'code'" not in err


def test_success_message_goes_to_stderr(tmp_path: Path, capsys) -> None:
    output = tmp_path / "out.yaml"
    exit_code = main(_base(tmp_path) + ["--output", str(output)])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "imported" in captured.err


def test_input_and_output_must_differ_without_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "same.yaml"
    exit_code = main(["import", "--input", str(output), "--output", str(output), *_BASE_ARGS])
    assert exit_code == 1


def test_unreadable_input_fails_cleanly(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "import",
            "--input",
            str(tmp_path / "missing.json"),
            "--output",
            str(tmp_path / "out.yaml"),
            *_BASE_ARGS,
        ]
    )
    assert exit_code == 1
    assert "invalid_input" in capsys.readouterr().err


def test_cli_runs_without_network_or_database_services(tmp_path: Path) -> None:
    """The import path only touches local files; no service imports happen."""
    import sys

    output = tmp_path / "out.yaml"
    before = set(sys.modules)
    exit_code = main(_base(tmp_path) + ["--output", str(output)])
    assert exit_code == 0
    # No optional backend package is required by the importer.
    for module in list(sys.modules):
        if module not in before and module.startswith(("psycopg", "redis", "pymongo")):
            raise AssertionError(f"unexpected service dependency: {module}")
