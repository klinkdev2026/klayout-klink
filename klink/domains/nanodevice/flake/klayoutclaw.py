"""Adapters for KlayoutClaw detector engines.

The functions in this module are optional reference adapters.  They do not
import KlayoutClaw or its heavy detector dependencies until a caller explicitly
loads or runs a detector.
"""

from __future__ import annotations

import os
import json
import hashlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .artifacts import DetectionsManifest, StageOutput, TransformOutput
from .detectors import DetectorRunSpec, load_detector_callable, normalize_detector_output


@dataclass(frozen=True)
class KlayoutClawEngine:
    """Description of an upstream KlayoutClaw detector entry point."""

    engine: str
    material: str
    relative_path: str
    function_name: str
    required_masks: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class KlayoutClawStageScript:
    """Description of an upstream material-stage detector script."""

    material: str
    relative_path: str
    output_mask: str
    output_result: str
    native_coordinate: str
    notes: str


KLAYOUTCLAW_ENGINES: dict[tuple[str, str], KlayoutClawEngine] = {
    ("b1", "graphene"): KlayoutClawEngine(
        engine="b1",
        material="graphene",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/detectors/b1_classifier.py",
        function_name="detect",
        required_masks=("host_mask", "flake_mask"),
        notes="per-image online logistic regression",
    ),
    ("b2", "graphite"): KlayoutClawEngine(
        engine="b2",
        material="graphite",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/detectors/b2_multik.py",
        function_name="detect_graphite",
        required_masks=("host_mask",),
        notes="adaptive multi-K clustering and Mahalanobis growth",
    ),
    ("b2", "graphene"): KlayoutClawEngine(
        engine="b2",
        material="graphene",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/detectors/b2_multik.py",
        function_name="detect_graphene",
        required_masks=("flake_mask",),
        notes="adaptive multi-K clustering and Mahalanobis growth",
    ),
    ("b3", "graphite"): KlayoutClawEngine(
        engine="b3",
        material="graphite",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/detectors/b3_shapetemplate.py",
        function_name="detect_graphite",
        required_masks=("host_mask",),
        notes="shape-template threshold sweep with inferred contrast polarity",
    ),
    ("b3", "graphene"): KlayoutClawEngine(
        engine="b3",
        material="graphene",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/detectors/b3_shapetemplate.py",
        function_name="detect_graphene",
        required_masks=("flake_mask",),
        notes="shape-template threshold sweep with inferred contrast polarity",
    ),
}


KLAYOUTCLAW_STAGE_SCRIPTS: dict[str, KlayoutClawStageScript] = {
    "graphite": KlayoutClawStageScript(
        material="graphite",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/graphite.py",
        output_mask="graphite_mask.png",
        output_result="graphite_result.json",
        native_coordinate="bottom_part",
        notes="full graphite stage detector on bottom_part",
    ),
    "graphene": KlayoutClawStageScript(
        material="graphene",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/graphene.py",
        output_mask="graphene_mask.png",
        output_result="graphene_result.json",
        native_coordinate="top_part",
        notes="full graphene stage detector on top_part, optional mirror/footprint",
    ),
    "bottom_hbn": KlayoutClawStageScript(
        material="bottom_hbn",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/bottom_hbn.py",
        output_mask="bottom_hbn_mask.png",
        output_result="bottom_hbn_result.json",
        native_coordinate="full_stack",
        notes="bottom hBN stage detector warped into full_stack",
    ),
    "top_hbn": KlayoutClawStageScript(
        material="top_hbn",
        relative_path="skills/nanodevice_flakedetect_detect/scripts/top_hbn.py",
        output_mask="top_hbn_mask.png",
        output_result="top_hbn_result.json",
        native_coordinate="full_stack",
        notes="top hBN stage detector using footprint mask",
    ),
}


def list_klayoutclaw_engines() -> list[dict]:
    """Return supported KlayoutClaw detector descriptors."""

    return [
        {
            "engine": desc.engine,
            "material": desc.material,
            "relative_path": desc.relative_path,
            "function_name": desc.function_name,
            "required_masks": list(desc.required_masks),
            "notes": desc.notes,
        }
        for desc in KLAYOUTCLAW_ENGINES.values()
    ]


def list_klayoutclaw_stage_scripts() -> list[dict]:
    """Return supported upstream material-stage detector scripts."""

    return [
        {
            "material": desc.material,
            "relative_path": desc.relative_path,
            "output_mask": desc.output_mask,
            "output_result": desc.output_result,
            "native_coordinate": desc.native_coordinate,
            "notes": desc.notes,
        }
        for desc in KLAYOUTCLAW_STAGE_SCRIPTS.values()
    ]


def get_klayoutclaw_engine(engine: str, material: str) -> KlayoutClawEngine:
    """Return a supported KlayoutClaw engine descriptor."""

    key = (engine.lower(), material)
    try:
        return KLAYOUTCLAW_ENGINES[key]
    except KeyError as exc:
        supported = ", ".join(f"{e}/{m}" for e, m in sorted(KLAYOUTCLAW_ENGINES))
        raise ValueError(f"unsupported KlayoutClaw detector {engine!r}/{material!r}; supported: {supported}") from exc


def get_klayoutclaw_stage_script(material: str) -> KlayoutClawStageScript:
    """Return an upstream material-stage script descriptor."""

    key = material.lower()
    try:
        return KLAYOUTCLAW_STAGE_SCRIPTS[key]
    except KeyError as exc:
        supported = ", ".join(sorted(KLAYOUTCLAW_STAGE_SCRIPTS))
        raise ValueError(f"unsupported KlayoutClaw stage material {material!r}; supported: {supported}") from exc


def klayoutclaw_detector_path(root: str | Path, engine: str, material: str) -> Path:
    """Return the Python file path for a KlayoutClaw detector."""

    desc = get_klayoutclaw_engine(engine, material)
    return Path(root) / Path(desc.relative_path)


def klayoutclaw_stage_script_path(root: str | Path, material: str) -> Path:
    """Return the Python file path for an upstream material-stage script."""

    desc = get_klayoutclaw_stage_script(material)
    return Path(root) / Path(desc.relative_path)


def load_klayoutclaw_detector(root: str | Path, engine: str, material: str) -> Callable[..., dict]:
    """Load a KlayoutClaw detector callable lazily."""

    desc = get_klayoutclaw_engine(engine, material)
    return load_detector_callable(Path(root) / desc.relative_path, desc.function_name)


def run_klayoutclaw_detector(
    root: str | Path,
    engine: str,
    material: str,
    image,
    *,
    pixel_size_um: float,
    host_mask=None,
    flake_mask=None,
    layer: str = "30/0",
    target_layer: str = "12/0",
    min_area_um2: float = 0.0,
    port_prefix: str | None = None,
) -> dict:
    """Run a KlayoutClaw detector and normalize it to nanodevice output."""

    desc = get_klayoutclaw_engine(engine, material)
    missing = [
        name for name in desc.required_masks
        if (name == "host_mask" and host_mask is None) or (name == "flake_mask" and flake_mask is None)
    ]
    if missing:
        raise ValueError(f"{desc.engine}/{desc.material} requires mask argument(s): {', '.join(missing)}")

    detector = load_klayoutclaw_detector(root, engine, material)
    if desc.engine == "b1":
        raw = detector(
            image=image,
            host_mask=host_mask,
            flake_mask=flake_mask,
            pixel_size_um=pixel_size_um,
        )
    elif desc.material == "graphite":
        raw = detector(image, host_mask, pixel_size_um)
    else:
        raw = detector(image, flake_mask, pixel_size_um)

    spec = DetectorRunSpec(
        material=material,
        pixel_size_um=pixel_size_um,
        layer=layer,
        target_layer=target_layer,
        port_prefix=port_prefix,
        min_area_um2=min_area_um2,
        engine=f"klayoutclaw:{desc.engine}",
        metadata={
            "source": "KlayoutClaw",
            "function": desc.function_name,
            "relative_path": desc.relative_path,
            "notes": desc.notes,
        },
    )
    return normalize_detector_output(raw, spec)


def run_klayoutclaw_stage_script(
    root: str | Path,
    material: str,
    *,
    image: str | Path,
    pixel_size_um: float,
    output_dir: str | Path,
    python_executable: str | Path | None = None,
    mirror: bool = False,
    footprint_mask: str | Path | None = None,
    target_image: str | Path | None = None,
    warp_matrix: str | Path | None = None,
    timeout_sec: int = 300,
    cache_dir: str | Path | None = None,
    force: bool = False,
) -> dict:
    """Run an upstream material-stage detector script and normalize its mask."""

    desc = get_klayoutclaw_stage_script(material)
    root_path = Path(root).resolve()
    script = (root_path / desc.relative_path).resolve()
    if not script.exists():
        raise FileNotFoundError(script)
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    py = str(python_executable or sys.executable)
    cmd = [
        py,
        str(script),
        "--image",
        str(Path(image).resolve()),
        "--pixel-size",
        str(pixel_size_um),
        "--output-dir",
        str(out_dir),
    ]
    if desc.material == "graphene" and mirror:
        cmd.append("--mirror")
    if desc.material in {"graphene", "top_hbn"} and footprint_mask is not None:
        cmd.extend(["--footprint-mask", str(Path(footprint_mask).resolve())])
    if desc.material == "bottom_hbn":
        if target_image is None or warp_matrix is None:
            raise ValueError("bottom_hbn stage requires target_image and warp_matrix")
        cmd.extend([
            "--target-image",
            str(Path(target_image).resolve()),
            "--warp-matrix",
            str(Path(warp_matrix).resolve()),
        ])

    mask_path = out_dir / desc.output_mask
    result_path = out_dir / desc.output_result
    cache_key = None
    cache_hit = False
    proc_stdout = ""
    proc_stderr = ""
    cache_entry = None
    if cache_dir is not None:
        cache_key = _stage_cache_key(
            desc,
            script=script,
            image=Path(image),
            pixel_size_um=pixel_size_um,
            mirror=mirror,
            footprint_mask=Path(footprint_mask) if footprint_mask is not None else None,
            target_image=Path(target_image) if target_image is not None else None,
            warp_matrix=Path(warp_matrix) if warp_matrix is not None else None,
        )
        cache_entry = Path(cache_dir).resolve() / cache_key
        if not force and _restore_stage_cache(cache_entry, out_dir, desc):
            cache_hit = True
            manifest = _load_stage_cache_manifest(cache_entry)
            proc_stdout = str(manifest.get("stdout", ""))
            proc_stderr = str(manifest.get("stderr", ""))

    if not cache_hit:
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("LOKY_MAX_CPU_COUNT", "1")
        proc = subprocess.run(
            cmd,
            cwd=str(script.parent),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
        proc_stdout = proc.stdout
        proc_stderr = proc.stderr
        if proc.returncode != 0:
            raise RuntimeError(
                f"KlayoutClaw stage {material!r} failed with exit {proc.returncode}\n"
                f"cmd: {' '.join(cmd)}\n"
                f"stderr:\n{proc.stderr[-3000:]}"
            )
        if cache_entry is not None:
            _store_stage_cache(
                cache_entry,
                out_dir,
                desc,
                {
                    "cache_key": cache_key,
                    "material": desc.material,
                    "cmd": cmd,
                    "stdout": proc_stdout,
                    "stderr": proc_stderr,
                    "native_coordinate": desc.native_coordinate,
                },
            )
    cv2 = _load_cv_for_stage()
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(mask_path)
    layer = {
        "top_hbn": "10/0",
        "graphene": "11/0",
        "bottom_hbn": "12/0",
        "graphite": "13/0",
    }.get(desc.material, "30/0")
    normalized = normalize_detector_output(
        {"mask": mask},
        DetectorRunSpec(
            material=desc.material,
            pixel_size_um=pixel_size_um,
            layer=layer,
            engine=f"klayoutclaw:stage:{desc.material}",
            metadata={
                "source": "KlayoutClaw",
                "relative_path": desc.relative_path,
                "native_coordinate": desc.native_coordinate,
                "notes": desc.notes,
            },
        ),
    )
    normalized["stage"] = {
        "cmd": cmd,
        "stdout": proc_stdout,
        "stderr": proc_stderr,
        "mask_path": str(mask_path),
        "result_path": str(result_path) if result_path.exists() else None,
        "contour_path": str(out_dir / _stage_contour_file(desc)) if (out_dir / _stage_contour_file(desc)).exists() else None,
        "native_coordinate": desc.native_coordinate,
        "cache_key": cache_key,
        "cache_hit": cache_hit,
    }
    normalized["stage_artifact"] = StageOutput(
        material=desc.material,
        native_coordinate=desc.native_coordinate,
        mask_path=str(mask_path),
        result_path=str(result_path) if result_path.exists() else None,
        contour_path=str(out_dir / _stage_contour_file(desc)) if (out_dir / _stage_contour_file(desc)).exists() else None,
        cmd=cmd,
        stdout=proc_stdout,
        stderr=proc_stderr,
        cache_key=cache_key,
        cache_hit=cache_hit,
    ).to_dict()
    return normalized


def build_klayoutclaw_detections_json(
    detect_dir: str | Path,
    *,
    pixel_size_um: float,
    output_path: str | Path | None = None,
) -> Path:
    """Assemble KlayoutClaw ``detections.json`` from stage outputs."""

    d = Path(detect_dir).resolve()
    configs = [
        ("graphite", "graphite_result.json", "graphite_mask.png", "graphite_contour.npy", "bottom_part", False),
        ("graphene", "graphene_result.json", "graphene_mask.png", "graphene_contour.npy", "top_part", True),
        ("bottom_hBN", "bottom_hbn_result.json", "bottom_hbn_mask.png", "bottom_hbn_contour.npy", "full_stack", False),
        ("top_hBN", "top_hbn_result.json", "top_hbn_mask.png", "top_hbn_contour.npy", "full_stack", False),
    ]
    materials = {}
    for material, result_file, mask_file, contour_file, coord_sys, mirrored in configs:
        result_path = d / result_file
        mask_path = d / mask_file
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            area_px = int(result.get("area_px", 0))
            area_um2 = float(result.get("area_um2", 0.0))
        else:
            area_px, area_um2 = _mask_area(mask_path, pixel_size_um)
        materials[material] = {
            "mask_file": mask_file,
            "contour_file": contour_file,
            "area_px": area_px,
            "area_um2": area_um2,
            "coordinate_system": coord_sys,
            "mirrored": mirrored,
        }
    detections = {"pixel_size_um": float(pixel_size_um), "materials": materials}
    out = Path(output_path).resolve() if output_path is not None else d / "detections.json"
    out.write_text(json.dumps(detections, indent=2), encoding="utf-8")
    return out


def build_klayoutclaw_detections_manifest(
    detect_dir: str | Path,
    *,
    pixel_size_um: float,
    output_path: str | Path | None = None,
) -> DetectionsManifest:
    """Assemble detections JSON and return a typed artifact descriptor."""

    path = build_klayoutclaw_detections_json(detect_dir, pixel_size_um=pixel_size_um, output_path=output_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return DetectionsManifest(path=str(path), pixel_size_um=float(payload["pixel_size_um"]), materials=payload["materials"])


def run_klayoutclaw_transform(
    root: str | Path,
    *,
    detections: str | Path,
    align_dir: str | Path,
    image: str | Path,
    pixel_size_um: float,
    output_dir: str | Path,
    python_executable: str | Path | None = None,
    timeout_sec: int = 300,
) -> dict:
    """Run upstream ``combine/transform.py`` and return traces/report paths."""

    script = (Path(root).resolve() / "skills/nanodevice_flakedetect_combine/scripts/transform.py").resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(python_executable or sys.executable),
        str(script),
        "--detections",
        str(Path(detections).resolve()),
        "--align-dir",
        str(Path(align_dir).resolve()),
        "--image",
        str(Path(image).resolve()),
        "--pixel-size",
        str(pixel_size_um),
        "--output-dir",
        str(out_dir),
    ]
    proc = _run_klayoutclaw_subprocess(cmd, script.parent, timeout_sec=timeout_sec)
    traces_path = out_dir / "traces.json"
    if not traces_path.exists():
        raise FileNotFoundError(traces_path)
    return {
        "cmd": cmd,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "traces_path": str(traces_path),
        "combine_report_path": str(out_dir / "combine_report.json"),
        "transform_artifact": TransformOutput(
            traces_path=str(traces_path),
            combine_report_path=str(out_dir / "combine_report.json"),
            cmd=cmd,
            stdout=proc.stdout,
            stderr=proc.stderr,
        ).to_dict(),
    }


def run_klayoutclaw_ecc_and_overlay(
    root: str | Path,
    *,
    raw: str | Path,
    lut: str | Path,
    traces: str | Path,
    output_dir: str | Path,
    python_executable: str | Path | None = None,
    timeout_sec: int = 300,
) -> dict:
    """Run upstream ECC registration and overlay generation."""

    root_path = Path(root).resolve()
    scripts = root_path / "skills/nanodevice_flakedetect_combine/scripts"
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    py = str(python_executable or sys.executable)
    ecc_script = (scripts / "ecc_register.py").resolve()
    overlay_script = (scripts / "overlay.py").resolve()
    ecc_cmd = [
        py,
        str(ecc_script),
        "--raw",
        str(Path(raw).resolve()),
        "--lut",
        str(Path(lut).resolve()),
        "--output-dir",
        str(out_dir),
    ]
    ecc_proc = _run_klayoutclaw_subprocess(ecc_cmd, ecc_script.parent, timeout_sec=timeout_sec)
    report_path = out_dir / "combine_report.json"
    overlay_cmd = [
        py,
        str(overlay_script),
        "--traces",
        str(Path(traces).resolve()),
        "--raw",
        str(Path(raw).resolve()),
        "--lut",
        str(Path(lut).resolve()),
        "--combine-report",
        str(report_path),
        "--output-dir",
        str(out_dir),
    ]
    overlay_proc = _run_klayoutclaw_subprocess(overlay_cmd, overlay_script.parent, timeout_sec=timeout_sec)
    return {
        "ecc_cmd": ecc_cmd,
        "overlay_cmd": overlay_cmd,
        "ecc_stdout": ecc_proc.stdout,
        "overlay_stdout": overlay_proc.stdout,
        "combine_report_path": str(report_path),
        "overlay_raw_path": str(out_dir / "overlay_raw.png"),
        "overlay_lut_path": str(out_dir / "overlay_lut.png"),
        "mask_composite_path": str(out_dir / "mask_composite.png"),
    }


def _load_cv_for_stage():
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError("KlayoutClaw stage script normalization requires cv2") from exc
    return cv2


def _mask_area(mask_path: Path, pixel_size_um: float) -> tuple[int, float]:
    cv2 = _load_cv_for_stage()
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        return 0, 0.0
    area_px = int((mask > 0).sum())
    return area_px, round(area_px * float(pixel_size_um) * float(pixel_size_um), 2)


def _stage_contour_file(desc: KlayoutClawStageScript) -> str:
    return desc.output_result.replace("_result.json", "_contour.npy")


def _stage_cache_files(desc: KlayoutClawStageScript) -> list[str]:
    return [desc.output_mask, desc.output_result, _stage_contour_file(desc)]


def _file_digest(path: Path) -> dict:
    p = path.resolve()
    if not p.exists():
        raise FileNotFoundError(p)
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    stat = p.stat()
    return {
        "path": str(p),
        "size": stat.st_size,
        "sha256": h.hexdigest(),
    }


def _optional_file_digest(path: Path | None) -> dict | None:
    if path is None:
        return None
    return _file_digest(path)


def _stage_cache_key(
    desc: KlayoutClawStageScript,
    *,
    script: Path,
    image: Path,
    pixel_size_um: float,
    mirror: bool,
    footprint_mask: Path | None,
    target_image: Path | None,
    warp_matrix: Path | None,
) -> str:
    payload = {
        "kind": "klayoutclaw_stage_v1",
        "material": desc.material,
        "relative_path": desc.relative_path,
        "output_mask": desc.output_mask,
        "output_result": desc.output_result,
        "native_coordinate": desc.native_coordinate,
        "pixel_size_um": float(pixel_size_um),
        "mirror": bool(mirror),
        "script": _file_digest(script),
        "image": _file_digest(image),
        "footprint_mask": _optional_file_digest(footprint_mask),
        "target_image": _optional_file_digest(target_image),
        "warp_matrix": _optional_file_digest(warp_matrix),
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_stage_cache_manifest(cache_entry: Path) -> dict:
    manifest_path = cache_entry / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _restore_stage_cache(cache_entry: Path, out_dir: Path, desc: KlayoutClawStageScript) -> bool:
    manifest = _load_stage_cache_manifest(cache_entry)
    if not manifest:
        return False
    for name in _stage_cache_files(desc):
        cached = cache_entry / name
        if not cached.exists():
            return False
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in _stage_cache_files(desc):
        shutil.copy2(cache_entry / name, out_dir / name)
    (out_dir / "stage_cache_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return True


def _store_stage_cache(cache_entry: Path, out_dir: Path, desc: KlayoutClawStageScript, manifest: dict) -> None:
    cache_entry.mkdir(parents=True, exist_ok=True)
    files = []
    for name in _stage_cache_files(desc):
        src = out_dir / name
        if src.exists():
            shutil.copy2(src, cache_entry / name)
            files.append(name)
    payload = dict(manifest)
    payload["files"] = files
    (cache_entry / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _run_klayoutclaw_subprocess(cmd: list[str], cwd: Path, *, timeout_sec: int):
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("LOKY_MAX_CPU_COUNT", "1")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"KlayoutClaw subprocess failed with exit {proc.returncode}\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr[-3000:]}"
        )
    return proc
