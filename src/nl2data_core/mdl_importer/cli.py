"""Offline ``nl2data-mdl`` command-line entry point.

The command converts one supported Wren MDL JSON document into
validated semantic assembly authoring YAML.  It reads only the given
input and optional bounded mapping manifest, performs no network or
database access, and writes the output atomically (temporary file plus
atomic replacement) only after the complete conversion and authoring
validation succeed.  Any failure exits non-zero and leaves no output
file behind.

Exit codes: ``0`` success, ``1`` import failure, ``2`` usage error.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import cast

from nl2data_core.mdl_importer import (
    MdlDiagnostic,
    MdlDiagnosticCode,
    MdlImportResult,
    import_wren_mdl,
)

_MAX_DIAGNOSTICS_SHOWN = 100


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nl2data-mdl",
        description="Offline Wren MDL to semantic assembly authoring importer.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser(
        "import",
        help="Convert a supported Wren MDL JSON document to authoring YAML.",
    )
    import_parser.add_argument("--input", required=True, help="MDL JSON input path")
    import_parser.add_argument("--output", required=True, help="authoring YAML output path")
    import_parser.add_argument("--source-id", required=True, help="semantic source identity")
    import_parser.add_argument("--bundle-id", required=True, help="target bundle identity")
    import_parser.add_argument(
        "--model-version", required=True, help="target semantic model version"
    )
    import_parser.add_argument(
        "--mapping",
        help="optional bounded JSON identifier-override manifest path",
    )
    import_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow replacing an existing output file",
    )
    import_parser.add_argument(
        "--diagnostics",
        choices=("human", "json"),
        default="human",
        help="diagnostic output format",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    manifest_payload: bytes | None = None

    try:
        payload = input_path.read_bytes()
    except OSError:
        return _fail(
            args.diagnostics,
            _diagnostic("invalid_input", "$", "the MDL input file cannot be read"),
        )

    if args.mapping is not None:
        try:
            manifest_payload = Path(args.mapping).read_bytes()
        except OSError:
            return _fail(
                args.diagnostics,
                _diagnostic("invalid_manifest", "$", "the mapping manifest cannot be read"),
            )

    if not args.overwrite and _same_file(input_path, output_path):
        return _fail(
            args.diagnostics,
            _diagnostic(
                "invalid_input",
                "$",
                "the input and output paths must differ unless --overwrite is used",
            ),
        )
    if output_path.exists() and not args.overwrite:
        return _fail(
            args.diagnostics,
            _diagnostic("output_exists", "$", "the output path already exists"),
        )

    result = import_wren_mdl(
        payload,
        source_id=args.source_id,
        bundle_id=args.bundle_id,
        model_version=args.model_version,
        manifest_payload=manifest_payload,
    )
    if not result.successful or result.document is None:
        return _fail(args.diagnostics, None, result)

    if not _atomic_write(output_path, result.document):
        return _fail(
            args.diagnostics,
            _diagnostic("invalid_input", "$", "the output file cannot be written"),
        )
    print(f"imported {input_path} -> {output_path}", file=sys.stderr)
    return 0


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return str(left) == str(right)


def _atomic_write(output_path: Path, document: str) -> bool:
    """Write the document through a temporary file and atomic replace."""
    directory = output_path.parent if str(output_path.parent) else Path(".")
    temp_name: str | None = None

    def _discard() -> None:
        if temp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)

    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=directory,
            prefix=output_path.name,
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(document)
            temp_name = handle.name
        # Windows requires the handle closed before replacement.
        os.replace(temp_name, output_path)
    except OSError:
        _discard()
        return False
    except BaseException:
        _discard()
        raise
    return True


def _diagnostic(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _fail(
    diagnostic_format: str,
    diagnostic: dict[str, str] | None = None,
    result: MdlImportResult | None = None,
) -> int:
    if result is not None:
        entries = list(result.diagnostics[:_MAX_DIAGNOSTICS_SHOWN])
        truncated = result.truncated
    else:
        entries = (
            [
                MdlDiagnostic(
                    code=cast(MdlDiagnosticCode, diagnostic["code"]),
                    path=diagnostic["path"],
                    message=diagnostic["message"],
                )
            ]
            if diagnostic is not None
            else []
        )
        truncated = False
    if diagnostic_format == "json":
        print(
            json.dumps(
                {
                    "diagnostics": [entry.model_dump() for entry in entries],
                    "truncated": truncated,
                },
                indent=2,
            )
        )
    else:
        for item in entries:
            location = (
                f" ({item.mark.line}:{item.mark.column})"
                if item.mark is not None
                else ""
            )
            print(
                f"error [{item.code}] {item.path}{location}: {item.message}",
                file=sys.stderr,
            )
        if truncated:
            print("error: additional diagnostics were truncated", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
