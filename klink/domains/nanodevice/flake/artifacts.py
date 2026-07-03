"""Typed artifact contracts for nanodevice flake workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StageOutput:
    """One material stage output from an upstream or local detector."""

    material: str
    native_coordinate: str
    mask_path: str
    result_path: str | None = None
    contour_path: str | None = None
    cmd: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    cache_key: str | None = None
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageOutput":
        return cls(
            material=str(data["material"]),
            native_coordinate=str(data["native_coordinate"]),
            mask_path=str(data["mask_path"]),
            result_path=data.get("result_path"),
            contour_path=data.get("contour_path"),
            cmd=[str(item) for item in data.get("cmd", [])],
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            cache_key=data.get("cache_key"),
            cache_hit=bool(data.get("cache_hit", False)),
        )


@dataclass(frozen=True)
class DetectionsManifest:
    """Path and payload for a KlayoutClaw-compatible detections manifest."""

    path: str
    pixel_size_um: float
    materials: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DetectionsManifest":
        return cls(
            path=str(data["path"]),
            pixel_size_um=float(data["pixel_size_um"]),
            materials=dict(data.get("materials", {})),
        )


@dataclass(frozen=True)
class TransformOutput:
    """Output of detections-to-traces transform."""

    traces_path: str
    combine_report_path: str
    cmd: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransformOutput":
        return cls(
            traces_path=str(data["traces_path"]),
            combine_report_path=str(data["combine_report_path"]),
            cmd=[str(item) for item in data.get("cmd", [])],
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
        )


@dataclass(frozen=True)
class TraceBundle:
    """Parsed traces plus the layout-ready summary derived from them."""

    traces_path: str
    summary: dict[str, dict[str, Any]]
    shape_item_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceBundle":
        return cls(
            traces_path=str(data["traces_path"]),
            summary=dict(data.get("summary", {})),
            shape_item_count=int(data.get("shape_item_count", 0)),
        )


@dataclass(frozen=True)
class LayoutPayload:
    """Layout insertion payload for live or offline KLayout writeback."""

    source_path: str
    coordinate: str
    shape_items: list[dict[str, Any]]

    @property
    def shape_item_count(self) -> int:
        return len(self.shape_items)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["shape_item_count"] = self.shape_item_count
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LayoutPayload":
        return cls(
            source_path=str(data["source_path"]),
            coordinate=str(data["coordinate"]),
            shape_items=[dict(item) for item in data.get("shape_items", [])],
        )


def path_text(path: str | Path | None) -> str | None:
    """Return a normalized string path for artifact records."""

    if path is None:
        return None
    return str(Path(path))
