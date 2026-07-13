"""Cross-window collaboration tutorial capture (tutorial-multiwindow.html).

Drives TWO live klink sessions at once -- a "source" KLayout window (default
port 8765) and a "destination" KLayout window (default port 8767) -- through
the exact sequence the tutorial narrates: each window self-identifies via
its klink toolbar's `K876x` port badge, a selection gets SEND'd to durable
agent memory, one window is marked the gdsfactory/klive target via GFTGT,
and a flat-selection geometry transfer moves objects from the source window
into the destination window across the RPC boundary. Every screenshot is a
REAL desktop capture of a REAL live window state -- there is no synthetic
mockup step.

Per CLAUDE.md's "Starter demo / tutorial parity" rule (a tutorial is a
step-by-step expansion of its demo, not a hand-authored artifact): this
script is that regenerable expansion for tutorial-multiwindow.html. It never
writes or touches any HTML -- only PNGs under --out-dir and --out-dir/en.

Pipeline
--------
1.  RPC side (this script, both sessions): build MW_SRC on the source
    session, MW_DST on the destination session, fire a real
    `selection.send_context`, and perform a real cross-session
    `build_flat_selection_package` -> dry-run -> commit transfer -- the same
    functions `klink.transfer_prepare` / `klink.transfer_commit` (the MCP
    tools the tutorial page's code block actually shows) call internally.
2.  Desktop side (PowerShell, Windows-only): after each RPC stage, shell out
    to capture_window.ps1 to grab a raw full-window screenshot of whichever
    session's window needs it, into a temporary Raw directory, under the
    filenames annotate3.ps1 expects (src-clean.png / src-send.png /
    dst-empty.png / dst-after.png).
3.  Annotation (PowerShell): run annotate3.ps1 twice over that Raw
    directory -- once with labels-cn.json into --out-dir, once with
    labels-en.json into --out-dir/en -- producing all 6 final PNGs per
    language in one pass each (it also composites the step-2 two-window
    montage from src-clean.png + dst-empty.png).

Windows-only, and only for these window-chrome screenshots (toolbar + cell
tree, not just the layout canvas view.screenshot renders): capturing that
needs a real desktop screenshot. Both target KLayout windows must be VISIBLE
(not minimized) and maximized to EXACTLY 1550x838px on the Windows session
running this script -- capture_window.ps1's annotation overlay is pixel
coordinates calibrated against that one size and refuses to proceed (no
output file, nonzero exit) at any other size or if no matching window is
found for the session's OS process id. See README.md for the full
precondition list.

Window selection is by OS PID, not title/size alone -- see
capture_window.ps1's own docstring and README.md. Each session's PID is
read with `client.exec_python("import os\\nos.getpid()")` (a read-only
diagnostic call, the one narrow exec.python escape hatch this script uses,
same justification pattern as gf_ports/draw_gf_ports_tutorial.py's).

Run against two live KLayout processes (klink plugin loaded), both windows
visible and maximized to 1550x838:

    python tools/tutorial_capture/multiwindow/draw_multiwindow_tutorial.py \\
        --src-port 8765 --dst-port 8767 \\
        --out-dir "D:\\klink_website\\assets\\tutorials\\multiwindow"
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# tools/tutorial_capture/multiwindow/draw_multiwindow_tutorial.py -> repo root
# is 3 parents up (multiwindow/ -> tutorial_capture/ -> tools/ -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from klink import KLinkClient  # noqa: E402
from klink.transfer import (  # noqa: E402
    build_flat_selection_package,
    commit_flat_selection_package,
)

SCRIPT_DIR = Path(__file__).resolve().parent
CAPTURE_WINDOW_SCRIPT = SCRIPT_DIR / "capture_window.ps1"
ANNOTATE_SCRIPT = SCRIPT_DIR / "annotate3.ps1"
LABELS_CN = SCRIPT_DIR / "labels-cn.json"
LABELS_EN = SCRIPT_DIR / "labels-en.json"

DEFAULT_OUT = Path(r"D:\klink_website\assets\tutorials\multiwindow")

EXPECTED_W = 1550
EXPECTED_H = 838

SRC_CELL = "MW_SRC"
DST_CELL = "MW_DST"

SRC_ZOOM_UM = (-15.0, -5.0, 15.0, 7.0)
DST_ZOOM_UM = (-16.0, -8.0, 18.0, 8.0)
# selection.set_box's bbox_dbu (integer DBU at the tab's dbu=0.001, i.e.
# um * 1000) -- covers all 5 MW_SRC objects (3x 10/0 + 2x 20/0), all within
# x in [-12, 12], y in [-2, 2].
SRC_SELECTION_BBOX_DBU = [-12000, -2000, 12000, 2000]


def _parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--src-port", type=int, default=8765,
                         help="klink RPC port of the SOURCE live KLayout session (default: 8765)")
    parser.add_argument("--dst-port", type=int, default=8767,
                         help="klink RPC port of the DESTINATION live KLayout session (default: 8767)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT),
                         help="Directory to write the 6 CN PNGs into; EN twins go to "
                              "<out-dir>/en (default: %(default)s)")
    parser.add_argument("--keep-raw", action="store_true",
                         help="Keep the temporary Raw capture directory instead of "
                              "deleting it after a successful run (useful for debugging "
                              "annotation coordinates).")
    return parser.parse_args()


def _get_pid(client: KLinkClient, label: str) -> int:
    result = client.exec_python("import os\nos.getpid()")
    if result.get("exception") is not None:
        raise RuntimeError(f"{label}: could not read process id: {result['exception']}")
    pid = int(result["return_value"])
    print(f"    {label} process id: {pid}")
    return pid


def _capture_window(target_pid: int, out_path: Path) -> None:
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(CAPTURE_WINDOW_SCRIPT),
        "-TargetPid", str(target_pid),
        "-Out", str(out_path),
        "-ExpectedWidth", str(EXPECTED_W),
        "-ExpectedHeight", str(EXPECTED_H),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print("    " + proc.stdout.strip())
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        raise RuntimeError(
            f"window capture FAILED for pid={target_pid} -> {out_path} "
            f"(exit {proc.returncode}); refusing to continue -- see README.md's "
            f"1550x838-maximized precondition."
        )
    if not out_path.exists():
        raise RuntimeError(f"capture_window.ps1 reported success but {out_path} is missing")


def _run_annotate(raw_dir: Path, labels_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(ANNOTATE_SCRIPT),
        "-JsonPath", str(labels_path),
        "-Raw", str(raw_dir),
        "-OutDir", str(out_dir),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        raise RuntimeError(f"annotate3.ps1 FAILED for {labels_path.name} -> {out_dir} (exit {proc.returncode})")


def verify_tab(client: KLinkClient, index: int, label: str) -> dict:
    """Screenshot iron rule: verify the CURRENT tab is the disposable one we
    created, right before every screenshot -- never assume the active tab is
    still ours. Any pre-existing tab is the user's own session."""
    tabs = client.call("view.list_tabs", {})
    cur = tabs["tabs"][tabs["current_index"]]
    assert tabs["current_index"] == index, (
        f"{label}: current tab index is {tabs['current_index']!r}, expected our "
        f"disposable tab at index {index!r} ({cur!r}) -- refusing to capture a "
        f"tab we did not create"
    )
    return cur


# ---------------------------------------------------------------------------
# Stage 1 -- src-clean: build MW_SRC on the source session
# ---------------------------------------------------------------------------
def build_src_clean(src: KLinkClient) -> tuple[int, int]:
    print("\n[1/4] src-clean: build MW_SRC on the source session")
    tab = src.new_tab(cell_name=SRC_CELL)
    index = tab["index"]
    previous_index = tab["previous_current_index"]
    print("    opened disposable tab:", tab.get("title"), "index", index)

    wl_li = src.layer_ensure(10, 0)["layer_index"]
    pad_li = src.layer_ensure(20, 0)["layer_index"]
    text_li = src.layer_ensure(6, 0)["layer_index"]

    src.shape_insert_boxes(SRC_CELL, layer_index=wl_li, boxes_um=[
        [-6, -1.5, 6, 1.5],
        [-9, -0.5, -6, 0.5],
        [6, -0.5, 9, 0.5],
    ])
    src.shape_insert_boxes(SRC_CELL, layer_index=pad_li, boxes_um=[
        [-12, -2, -9, 2],
        [9, -2, 12, 2],
    ])
    src.shape_insert_text(
        SRC_CELL, "SRC . K8765", layer_index=text_li,
        position_um=[-12, 3.5], size_um=1.4,
    )
    src.zoom_box(bbox_um=list(SRC_ZOOM_UM))
    src.selection_clear()
    return index, previous_index


# ---------------------------------------------------------------------------
# Stage 2 -- dst-empty: build MW_DST on the destination session, GFTGT it
# ---------------------------------------------------------------------------
def build_dst_empty(dst: KLinkClient) -> tuple[int, int]:
    print("\n[2/4] dst-empty: build MW_DST on the destination session, mark klive target")
    tab = dst.new_tab(cell_name=DST_CELL)
    index = tab["index"]
    previous_index = tab["previous_current_index"]
    print("    opened disposable tab:", tab.get("title"), "index", index)

    frame_li = dst.layer_ensure(1, 0)["layer_index"]
    text_li = dst.layer_ensure(6, 0)["layer_index"]

    dst.shape_insert_boxes(DST_CELL, layer_index=frame_li, boxes_um=[
        [-14, -6, 14, -5.5],
        [-14, 5.5, 14, 6],
        [-14, -6, -13.5, 6],
        [13.5, -6, 14, 6],
    ])
    dst.shape_insert_text(
        DST_CELL, "DST . K8767", layer_index=text_li,
        position_um=[-12, 3.5], size_um=1.4,
    )
    dst.zoom_box(bbox_um=list(DST_ZOOM_UM))

    klive_result = dst.call("session.mark_klive_target", {})
    print("    session.mark_klive_target ->", klive_result)
    return index, previous_index


# ---------------------------------------------------------------------------
# Stage 3 -- src-send: select the 5 MW_SRC objects, fire a real SEND
# ---------------------------------------------------------------------------
def do_src_send(src: KLinkClient) -> dict:
    print("\n[3/4] src-send: select 5 MW_SRC objects, selection.send_context")
    src.selection_set_box(SRC_CELL, list(SRC_SELECTION_BBOX_DBU))
    send_result = src.call("selection.send_context", {"source": "tutorial_capture"})
    print("    selection.send_context ->", send_result)
    count = send_result.get("count")
    if count != 5:
        raise RuntimeError(f"expected selection.send_context count=5, got {count!r}: {send_result}")
    return send_result


# ---------------------------------------------------------------------------
# Stage 4 -- dst-after: cross-session flat-selection transfer, src -> dst
# ---------------------------------------------------------------------------
def do_transfer(src: KLinkClient, dst: KLinkClient, src_port: int, dst_port: int) -> dict:
    print("\n[4/4] dst-after: cross-session flat-selection transfer (src -> dst)")
    selection = src.selection_get(limit=5000)
    layers = src.layer_list()
    info = src.layout_info(verbosity="normal")
    dbu = info.get("dbu", 0.001) if isinstance(info, dict) else 0.001

    package = build_flat_selection_package(
        selection,
        source_layers=layers,
        source_dbu_um=dbu,
        source_session=str(src_port),
        target_session=str(dst_port),
        target_cell=DST_CELL,
        layer_map=None,
        translate_um=[0, 0],
    )
    print("    package_id:", package["package_id"], "review:", package["review"])

    # Two-phase, same as the klink.transfer_prepare / klink.transfer_commit
    # MCP tools the tutorial page's code block shows: dry-run first (must be
    # inserted=0 -- proves "validate before mutate"), THEN a real commit.
    dry_run_result = commit_flat_selection_package(dst, package, dry_run=True)
    dry_inserted = dry_run_result["write"].get("inserted")
    print("    prepare dry-run -> inserted:", dry_inserted, "by_layer:", dry_run_result["write"].get("by_layer"))
    if dry_inserted != 0:
        raise RuntimeError(f"expected dry-run inserted=0, got {dry_inserted!r}: {dry_run_result}")

    dst.transfer_pending_set(package)

    commit_result = commit_flat_selection_package(dst, package, dry_run=False)
    inserted = commit_result["write"].get("inserted")
    print("    commit -> inserted:", inserted, "by_layer:", commit_result["write"].get("by_layer"))
    if inserted != 5:
        raise RuntimeError(f"expected commit inserted=5, got {inserted!r}: {commit_result}")

    dst.call("view.zoom_box", {"bbox_um": list(DST_ZOOM_UM)})
    return commit_result


def main() -> int:
    args = _parse_args()
    out_dir = Path(args.out_dir)
    en_out_dir = out_dir / "en"
    raw_dir = Path(tempfile.mkdtemp(prefix="klink_mw_capture_"))
    print("raw capture staging dir:", raw_dir)

    src_index = None
    dst_index = None
    src_previous = -1
    dst_previous = -1
    ok = False

    with KLinkClient(port=args.src_port).connect() as src, \
         KLinkClient(port=args.dst_port).connect() as dst:
        try:
            src_pid = _get_pid(src, f"source (port {args.src_port})")
            dst_pid = _get_pid(dst, f"destination (port {args.dst_port})")

            # ---- Stage 1: src-clean ----
            src_index, src_previous = build_src_clean(src)
            verify_tab(src, src_index, "src-clean")
            _capture_window(src_pid, raw_dir / "src-clean.png")

            # ---- Stage 2: dst-empty ----
            dst_index, dst_previous = build_dst_empty(dst)
            verify_tab(dst, dst_index, "dst-empty")
            _capture_window(dst_pid, raw_dir / "dst-empty.png")

            # ---- Stage 3: src-send ----
            send_result = do_src_send(src)
            verify_tab(src, src_index, "src-send")
            _capture_window(src_pid, raw_dir / "src-send.png")

            # ---- Stage 4: dst-after ----
            commit_result = do_transfer(src, dst, args.src_port, args.dst_port)
            verify_tab(dst, dst_index, "dst-after")
            _capture_window(dst_pid, raw_dir / "dst-after.png")

            # ---- Annotate: CN + EN, 6 PNGs each, one annotate3.ps1 run per language ----
            print("\n[annotate] CN ->", out_dir)
            _run_annotate(raw_dir, LABELS_CN, out_dir)
            print("\n[annotate] EN ->", en_out_dir)
            _run_annotate(raw_dir, LABELS_EN, en_out_dir)

            ok = True

            print("\n==================== summary ====================")
            print("source process id:", src_pid, " destination process id:", dst_pid)
            print("selection.send_context count:", send_result.get("count"))
            print("transfer commit inserted:", commit_result["write"].get("inserted"),
                  "by_layer:", commit_result["write"].get("by_layer"))
            print("wrote 6 CN PNGs to", out_dir)
            print("wrote 6 EN PNGs to", en_out_dir)
        finally:
            # Close only the tabs WE created, restore whatever was current
            # before, on each session independently. Never touch a
            # pre-existing tab (e.g. an old MW_SRC left over from a prior
            # failed run) -- see README.md.
            if src_index is not None:
                try:
                    src.call("view.close_tab", {"view_index": src_index})
                    if src_previous != -1:
                        src.call("view.activate_tab", {"index": src_previous})
                except Exception as exc:
                    print(f"    WARNING: could not clean up source tab: {exc}", file=sys.stderr)
            if dst_index is not None:
                try:
                    dst.call("view.close_tab", {"view_index": dst_index})
                    if dst_previous != -1:
                        dst.call("view.activate_tab", {"index": dst_previous})
                except Exception as exc:
                    print(f"    WARNING: could not clean up destination tab: {exc}", file=sys.stderr)

    if ok and not args.keep_raw:
        shutil.rmtree(raw_dir, ignore_errors=True)
    elif not ok:
        print(f"\nFAILED -- raw captures (if any) left in {raw_dir} for inspection", file=sys.stderr)
    elif args.keep_raw:
        print("\n--keep-raw: leaving raw captures in", raw_dir)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
