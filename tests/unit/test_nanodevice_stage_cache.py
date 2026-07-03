from __future__ import annotations

import json
from pathlib import Path


def test_klayoutclaw_stage_cache_reuses_material_outputs(monkeypatch, tmp_path):
    import klink.domains.nanodevice.flake.klayoutclaw as kc

    root = tmp_path / "KlayoutClaw"
    script = root / "skills" / "nanodevice_flakedetect_detect" / "scripts" / "graphite.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('graphite')\n", encoding="utf-8")
    image = tmp_path / "bottom_part.jpg"
    image.write_bytes(b"image")
    cache_dir = tmp_path / "cache"
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0
        stdout = "stage stdout"
        stderr = ""

    class FakeCv:
        IMREAD_GRAYSCALE = 0

        @staticmethod
        def imread(path, _mode):
            return {"mask_path": str(path)}

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        out_dir = Path(cmd[cmd.index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "graphite_mask.png").write_bytes(b"mask")
        (out_dir / "graphite_result.json").write_text(json.dumps({"area_px": 4, "area_um2": 0.25}), encoding="utf-8")
        (out_dir / "graphite_contour.npy").write_bytes(b"contour")
        return FakeProc()

    monkeypatch.setattr(kc.subprocess, "run", fake_run)
    monkeypatch.setattr(kc, "_load_cv_for_stage", lambda: FakeCv)
    monkeypatch.setattr(
        kc,
        "normalize_detector_output",
        lambda _output, spec: {
            "regions": [],
            "shape_items": [],
            "ports": [],
            "report": {"engine": spec.engine, "material": spec.material},
            "raw": {},
        },
    )

    first = kc.run_klayoutclaw_stage_script(
        root,
        "graphite",
        image=image,
        pixel_size_um=0.087,
        output_dir=tmp_path / "first",
        cache_dir=cache_dir,
    )
    second = kc.run_klayoutclaw_stage_script(
        root,
        "graphite",
        image=image,
        pixel_size_um=0.087,
        output_dir=tmp_path / "second",
        cache_dir=cache_dir,
    )

    assert len(calls) == 1
    assert first["stage"]["cache_hit"] is False
    assert second["stage"]["cache_hit"] is True
    assert first["stage"]["cache_key"] == second["stage"]["cache_key"]
    assert (tmp_path / "second" / "graphite_mask.png").read_bytes() == b"mask"
    assert (tmp_path / "second" / "graphite_contour.npy").read_bytes() == b"contour"
    assert second["stage_artifact"]["cache_hit"] is True


def test_build_klayoutclaw_detections_manifest_is_typed_without_cv2(tmp_path):
    from klink.domains.nanodevice.flake import DetectionsManifest, build_klayoutclaw_detections_manifest

    detect_dir = tmp_path / "detect"
    detect_dir.mkdir()
    for material in ["graphite", "graphene", "bottom_hbn", "top_hbn"]:
        (detect_dir / f"{material}_mask.png").write_bytes(b"mask")
        (detect_dir / f"{material}_contour.npy").write_bytes(b"contour")
        (detect_dir / f"{material}_result.json").write_text(
            json.dumps({"area_px": 10, "area_um2": 0.07569}),
            encoding="utf-8",
        )

    manifest = build_klayoutclaw_detections_manifest(detect_dir, pixel_size_um=0.087)

    assert isinstance(manifest, DetectionsManifest)
    assert Path(manifest.path).name == "detections.json"
    assert manifest.pixel_size_um == 0.087
    assert manifest.materials["graphene"]["coordinate_system"] == "top_part"
    assert manifest.materials["bottom_hBN"]["area_px"] == 10
    assert DetectionsManifest.from_dict(manifest.to_dict()) == manifest
