"""Offline, fail-closed Wren MDL importer for semantic assembly authoring.

Public surface of the ``wren-mdl-importer`` capability.  The importer
is a format adapter only: it never executes MDL SQL, never touches the
network, never infers physical deployment bindings, and never creates
lifecycle state.  Generated YAML is validated by the existing
semantic assembly authoring loader before it is returned.
"""

from .convert import ConvertOutcome, convert_mdl, normalize_identifier
from .diagnostics import (
    MAX_MDL_DIAGNOSTICS,
    MdlDiagnostic,
    MdlDiagnosticCode,
    MdlImportResult,
    MdlSourceMark,
)
from .importer import import_wren_mdl
from .manifest import (
    MAX_MANIFEST_BYTES,
    MAX_MANIFEST_ENTRIES,
    MappingManifest,
    load_manifest,
)
from .parser import (
    MAX_MDL_BYTES,
    MAX_MDL_DEPTH,
    SUPPORTED_MDL_VERSIONS,
    MdlParseOutcome,
    parse_mdl,
)

__all__ = [
    "MAX_MANIFEST_BYTES",
    "MAX_MANIFEST_ENTRIES",
    "MAX_MDL_BYTES",
    "MAX_MDL_DEPTH",
    "MAX_MDL_DIAGNOSTICS",
    "ConvertOutcome",
    "MdlDiagnostic",
    "MdlDiagnosticCode",
    "MdlImportResult",
    "MdlParseOutcome",
    "MdlSourceMark",
    "MappingManifest",
    "SUPPORTED_MDL_VERSIONS",
    "convert_mdl",
    "import_wren_mdl",
    "load_manifest",
    "normalize_identifier",
    "parse_mdl",
]
