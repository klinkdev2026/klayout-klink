"""Offline Yosys-to-device-netlist helpers.

This module only orchestrates text generation, an optional Yosys subprocess,
and the already-existing logic mapper.  It does not require Yosys for tests
that consume a checked-in JSON fixture.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from .logic_map import map_logic_to_devices


class YosysFlowError(ValueError):
    """Yosys flow setup or execution failed with an instructive message."""


# Yosys is an EXTERNAL, decoupled tool (an escape hatch, like the SPICE reader):
# this module never imports a yosys Python package, it only shells out to a
# binary. Discovery order: explicit KLINK_YOSYS env, then a real `yosys` on
# PATH, then the pip-installable WASM build `yowasp-yosys`. Absence is an
# instruction, not a crash (P3 errors-are-instructions).
_YOSYS_CANDIDATES = ("yosys", "yowasp-yosys")


def discover_yosys() -> str:
    """Return a runnable yosys executable, or raise an instructive error."""

    env = os.environ.get("KLINK_YOSYS")
    if env:
        found = shutil.which(env) or (env if Path(env).exists() else None)
        if found:
            return found
        raise YosysFlowError(
            f"KLINK_YOSYS={env!r} does not resolve to a runnable yosys binary"
        )
    for cand in _YOSYS_CANDIDATES:
        found = shutil.which(cand)
        if found:
            return found
    # Pip-installed (e.g. yowasp-yosys) into the same interpreter that runs
    # this flow: its console script sits next to sys.executable (venv Scripts/
    # or bin/), which is not always on PATH. Probe there too.
    import sys

    script_dir = Path(sys.executable).parent
    for cand in _YOSYS_CANDIDATES:
        for name in (cand, cand + ".exe"):
            p = script_dir / name
            if p.exists():
                return str(p)
    raise YosysFlowError(
        "No yosys binary found. Yosys is an external, decoupled tool. Install "
        "the pip WASM build into the interpreter that runs this flow:\n"
        "    python -m pip install yowasp-yosys\n"
        "or put a native `yosys` on PATH, or set KLINK_YOSYS to its full path."
    )


# Boolean function (liberty syntax: * AND, + OR, ! NOT, ^ XOR) and inputs for
# each gate name we can synthesize to. This is OUR cell definition; yosys is
# only the external tool that maps Verilog onto these named cells via
# `abc -liberty`. `abc -g` cannot emit named multi-input cells, so it can never
# match the device gate library's cell types -- liberty mapping is required.
_GATE_FUNCTIONS = {
    "INV": ("!A", ("A",)),
    "BUF": ("A", ("A",)),
    "NAND2": ("!(A*B)", ("A", "B")),
    "NOR2": ("!(A+B)", ("A", "B")),
    "AND2": ("A*B", ("A", "B")),
    "OR2": ("A+B", ("A", "B")),
    "XOR2": ("A^B", ("A", "B")),
    "XNOR2": ("!(A^B)", ("A", "B")),
}


def write_liberty(gate_set: Sequence[str], out_lib: str | Path) -> str:
    """Write (and return) a minimal liberty describing the named gate cells.

    abc maps the synthesized logic onto exactly these cell names with pins
    A/B/Y; power pins are technology-implicit (the device gate library supplies
    VDD/GND), so they are intentionally absent here.
    """

    gates = [_nonempty(g, "gate_set entry") for g in gate_set]
    if not gates:
        raise YosysFlowError("gate_set must contain at least one gate name")
    if "INV" not in gates:
        # abc -liberty requires an inverter in the cell set.
        raise YosysFlowError("gate_set must include 'INV' (abc requires an inverter cell)")
    # abc also needs a buffer cell available (else its mapper aborts). BUF is
    # added to the liberty for abc's benefit; it is not normally instantiated,
    # and is appended only if the caller did not already ask for it.
    lib_gates = list(gates) + (["BUF"] if "BUF" not in gates else [])
    cells = []
    for g in lib_gates:
        spec = _GATE_FUNCTIONS.get(g)
        if spec is None:
            known = ", ".join(sorted(_GATE_FUNCTIONS))
            raise YosysFlowError(f"gate {g!r} has no liberty definition; known: {known}")
        func, inputs = spec
        pins = "".join(
            f'    pin({i}) {{ direction: input; }}\n' for i in inputs
        )
        cells.append(
            f"  cell({g}) {{\n"
            f"    area: {len(inputs)};\n"
            f"{pins}"
            f'    pin(Y) {{ direction: output; function: "{func}"; }}\n'
            f"  }}\n"
        )
    text = "library(klink_gates) {\n" + "".join(cells) + "}\n"
    lib_path = Path(_path_text(out_lib, "out_lib"))
    lib_path.write_text(text, encoding="utf-8")
    return text


def write_techmap_script(
    verilog: str | Path,
    top: str,
    out_json: str | Path,
    liberty: str | Path,
    *,
    gate_set: Sequence[str] = ("INV", "NAND2", "NOR2"),
) -> str:
    """Return a deterministic Yosys script that maps to named liberty cells."""

    verilog_path = _path_text(verilog, "verilog")
    out_path = _path_text(out_json, "out_json")
    lib_path = _path_text(liberty, "liberty")
    top_name = _nonempty(top, "top")
    gates = [_nonempty(gate, "gate_set entry") for gate in gate_set]
    if not gates:
        raise YosysFlowError("gate_set must contain at least one gate name")
    return "\n".join(
        [
            f"read_verilog {verilog_path}",
            f"hierarchy -check -top {top_name}",
            "proc",
            "flatten",
            "opt",
            "techmap",
            "opt",
            f"abc -liberty {lib_path}",
            "opt_clean",
            "splitnets -ports",
            f"write_json {out_path}",
            "",
        ]
    )


def run_yosys(
    script: str, *, yosys_bin: str | None = None, cwd: str | Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run Yosys with a generated script string.

    ``yosys_bin`` is optional: when omitted the external binary is discovered
    (see :func:`discover_yosys`), keeping the caller decoupled from where/how
    yosys is installed. ``cwd`` sets the process working directory; the WASM
    build (yowasp-yosys) only sees files relative to it, so callers that touch
    files should run from the staging directory and use basenames.
    """

    if not isinstance(script, str) or not script.strip():
        raise YosysFlowError("script must be a non-empty Yosys script string")
    binary = _nonempty(yosys_bin, "yosys_bin") if yosys_bin else discover_yosys()
    try:
        return subprocess.run(
            [binary, "-q", "-"],
            input=script,
            text=True,
            capture_output=True,
            check=True,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        raise YosysFlowError(f"Yosys binary {binary!r} was not found") from exc
    except subprocess.CalledProcessError as exc:
        raise YosysFlowError(
            f"Yosys failed with exit {exc.returncode}; stderr:\n{exc.stderr[-3000:]}"
        ) from exc


def verilog_to_device_netlist(
    verilog: str | Path,
    top: str,
    gate_library: Mapping[str, Any],
    *,
    out_json: str | Path | None = None,
    yosys_bin: str | None = None,
    gate_set: Sequence[str] = ("INV", "NAND2", "NOR2"),
) -> dict[str, list[dict[str, Any]]]:
    """Synthesize Verilog through Yosys JSON, then expand to devices."""

    import shutil as _shutil

    verilog_path = Path(verilog).resolve()
    json_path = (Path(out_json) if out_json is not None else verilog_path.with_suffix(".yosys.json")).resolve()
    # Stage every file in one working dir and reference by basename, so the
    # WASM yosys (which cannot open absolute host paths) works identically to a
    # native binary.
    workdir = json_path.parent
    workdir.mkdir(parents=True, exist_ok=True)
    v_local = workdir / verilog_path.name
    if verilog_path != v_local:
        _shutil.copyfile(verilog_path, v_local)
    lib_path = json_path.with_suffix(".lib")
    write_liberty(gate_set, lib_path)
    script = write_techmap_script(
        verilog_path.name, top, json_path.name, lib_path.name, gate_set=gate_set
    )
    run_yosys(script, yosys_bin=yosys_bin, cwd=workdir)
    try:
        netlist = json.loads(json_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise YosysFlowError(f"Yosys did not create expected JSON output {json_path}") from exc
    return map_logic_to_devices(netlist, gate_library)


def _path_text(value: str | Path, label: str) -> str:
    text = str(value)
    if not text.strip():
        raise YosysFlowError(f"{label} path must be non-empty")
    return text


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise YosysFlowError(f"{label} must be a non-empty string")
    return value
