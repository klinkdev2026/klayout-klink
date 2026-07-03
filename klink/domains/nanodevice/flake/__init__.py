"""Flake detection namespace.

Phase 0 keeps this namespace importable without cv2/numpy.  Detection
functions live behind call-time dependency loaders.
"""

from .detect import (
    FlakeDetectionSpec,
    detect_bright_flakes,
    load_priors,
    regions_to_contact_ports,
    regions_to_polygon_items,
)
from .artifacts import DetectionsManifest, LayoutPayload, StageOutput, TraceBundle, TransformOutput
from .combine import layout_payload_from_traces, trace_bundle_from_path, trace_material_summary, traces_to_polygon_items
from .detectors import (
    DetectorRunSpec,
    compare_detection_summary,
    load_detector_callable,
    normalize_detector_output,
    physical_kernel_diameter,
    run_detector_callable,
)
from .klayoutclaw import (
    build_klayoutclaw_detections_json,
    build_klayoutclaw_detections_manifest,
    get_klayoutclaw_engine,
    get_klayoutclaw_stage_script,
    klayoutclaw_detector_path,
    klayoutclaw_stage_script_path,
    list_klayoutclaw_engines,
    list_klayoutclaw_stage_scripts,
    load_klayoutclaw_detector,
    run_klayoutclaw_detector,
    run_klayoutclaw_ecc_and_overlay,
    run_klayoutclaw_stage_script,
    run_klayoutclaw_transform,
)

__all__ = [
    "DetectorRunSpec",
    "DetectionsManifest",
    "FlakeDetectionSpec",
    "LayoutPayload",
    "StageOutput",
    "TraceBundle",
    "TransformOutput",
    "build_klayoutclaw_detections_json",
    "build_klayoutclaw_detections_manifest",
    "compare_detection_summary",
    "detect_bright_flakes",
    "get_klayoutclaw_engine",
    "get_klayoutclaw_stage_script",
    "klayoutclaw_detector_path",
    "klayoutclaw_stage_script_path",
    "list_klayoutclaw_engines",
    "list_klayoutclaw_stage_scripts",
    "layout_payload_from_traces",
    "load_detector_callable",
    "load_klayoutclaw_detector",
    "load_priors",
    "normalize_detector_output",
    "physical_kernel_diameter",
    "regions_to_contact_ports",
    "regions_to_polygon_items",
    "run_detector_callable",
    "run_klayoutclaw_detector",
    "run_klayoutclaw_ecc_and_overlay",
    "run_klayoutclaw_stage_script",
    "run_klayoutclaw_transform",
    "trace_bundle_from_path",
    "trace_material_summary",
    "traces_to_polygon_items",
]
