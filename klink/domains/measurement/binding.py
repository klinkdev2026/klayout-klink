"""Resolve measurement ResultRecord subjects against klink.spec v1."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Mapping

from klink.spec import read_spec

from .results import validate_record


class BindingError(ValueError):
    """Instruction-grade subject binding failure."""


def load_spec(path: str | Path) -> dict[str, Any]:
    """Load a spec through the public klink.spec reader only."""

    return read_spec(str(path))


def bind_file(record: Mapping[str, Any], spec_path: str | Path) -> dict[str, Any]:
    return bind(record, load_spec(spec_path))


def bind(record: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    canonical = validate_record(record)
    index = _index_spec(spec)
    subject = canonical["subject"]
    kind = subject["kind"]
    ref = subject["ref"]
    valid = index[kind]
    if ref not in valid:
        raise BindingError(_unknown_message(kind, ref, valid))
    bound = dict(canonical)
    bound["bound_subject"] = {
        "kind": kind,
        "ref": ref,
        "spec_entry": valid[ref],
    }
    notes = list(bound.get("note_for_main_lane", []))
    if not spec.get("layout", {}).get("gds_sha256") and "gds_sha256" not in canonical["spec_ref"]:
        notes.append("note_for_main_lane: spec v1 layout has no gds_sha256 field; result binding used spec_ref identity only.")
    if notes:
        bound["note_for_main_lane"] = notes
    return bound


def _index_spec(spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    devices = {str(device["device_id"]): dict(device) for device in spec.get("devices", [])}
    instances = {str(instance["instance_id"]): dict(instance) for instance in spec.get("instances", [])}

    terminal_names_by_device = {
        str(device["device_id"]): {str(term["name"]): dict(term) for term in device.get("terminals", [])}
        for device in spec.get("devices", [])
    }
    terminals: dict[str, Any] = {}
    for inst in spec.get("instances", []):
        instance_id = str(inst["instance_id"])
        device_id = str(inst["device_id"])
        for terminal_name, terminal in terminal_names_by_device.get(device_id, {}).items():
            ref = f"{instance_id}.{terminal_name}"
            terminals[ref] = {"instance_id": instance_id, "device_id": device_id, "terminal": terminal}

    nets: dict[str, Any] = {}
    for net in (spec.get("nets") or {}).get("declared", []):
        nets[str(net["net"])] = dict(net)
    for net in (spec.get("nets") or {}).get("derived", []):
        nets[str(net["net_id"])] = dict(net)

    sites = {}
    for site in spec.get("sites", []):
        site_id = site.get("site_id") or site.get("id") or site.get("name")
        if site_id is not None:
            sites[str(site_id)] = dict(site)

    return {
        "device": devices,
        "instance": instances,
        "terminal": terminals,
        "net": nets,
        "site": sites,
    }


def _unknown_message(kind: str, ref: str, valid: Mapping[str, Any]) -> str:
    ids = sorted(valid)
    nearest = difflib.get_close_matches(ref, ids, n=5, cutoff=0.0)
    if not ids:
        return f"unknown {kind} ref {ref!r}; the spec contains no {kind} ids to bind."
    return (
        f"unknown {kind} ref {ref!r}; nearest valid {kind} id(s): "
        f"{', '.join(nearest)}. Use exactly one id from the spec."
    )
