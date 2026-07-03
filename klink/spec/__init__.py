"""klink.spec — the engineering-fact layer (main-lane contract).

v1 contract: klink/spec/v1.py; design doc: docs/STRUCTURE_AS_DEVICE_IR.md.
"""

from klink.spec.v1 import (
    SCHEMA_VERSION,
    SOURCE_USER,
    SpecError,
    build_spec,
    read_spec,
    validate_spec,
    write_spec,
)

__all__ = [
    "SCHEMA_VERSION",
    "SOURCE_USER",
    "SpecError",
    "build_spec",
    "read_spec",
    "validate_spec",
    "write_spec",
]
