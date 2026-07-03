"""ResultRecord validation and deterministic JSON storage."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


STORE_SCHEMA_VERSION = "klink.measurement.results/1"
SUBJECT_KINDS = {"device", "instance", "terminal", "net", "site"}
REQUIRED_FIELDS = (
    "result_id",
    "spec_ref",
    "subject",
    "kind",
    "data",
    "conditions",
    "source",
    "timestamp",
)
OPTIONAL_FIELDS = ("limits", "outcome", "note_for_main_lane")
ALLOWED_FIELDS = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)


class ResultValidationError(ValueError):
    """Instruction-grade measurement result validation failure."""


@dataclass(frozen=True)
class ResultRecord:
    result_id: str
    spec_ref: dict[str, Any]
    subject: dict[str, Any]
    kind: str
    data: dict[str, Any]
    conditions: dict[str, Any]
    source: dict[str, Any]
    timestamp: str
    limits: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    note_for_main_lane: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, [], {}) or key in REQUIRED_FIELDS}


def validate_record(record: Mapping[str, Any] | ResultRecord) -> dict[str, Any]:
    data = _record_dict(record)
    problems: list[str] = []

    unknown = sorted(set(data) - ALLOWED_FIELDS)
    if unknown:
        problems.append(f"unknown top-level field(s): {', '.join(unknown)}; remove typo fields before import.")
    for field_name in REQUIRED_FIELDS:
        if field_name not in data:
            problems.append(f"missing required field {field_name!r}.")

    if problems:
        raise ResultValidationError(_message(problems))

    _require_string(data, "result_id", problems)
    _require_string(data, "kind", problems)
    _validate_spec_ref(data.get("spec_ref"), problems)
    _validate_subject(data.get("subject"), problems)
    _validate_result_data(data.get("data"), problems)
    _validate_flat_dict(data.get("conditions"), "conditions", problems)
    _validate_source(data.get("source"), problems)
    _validate_timestamp(data.get("timestamp"), problems)
    _validate_limits_or_outcome(data.get("limits"), "limits", problems)
    _validate_limits_or_outcome(data.get("outcome"), "outcome", problems)
    if "note_for_main_lane" in data and not isinstance(data["note_for_main_lane"], list):
        problems.append("note_for_main_lane must be a list of strings.")
    if isinstance(data.get("note_for_main_lane"), list):
        for i, note in enumerate(data["note_for_main_lane"]):
            if not isinstance(note, str):
                problems.append(f"note_for_main_lane[{i}] must be a string.")

    if problems:
        raise ResultValidationError(_message(problems))
    return _canonical(data)


def write_result_store(path: str | Path, records: Sequence[Mapping[str, Any] | ResultRecord]) -> dict[str, Any]:
    canonical = [validate_record(record) for record in records]
    ids = [record["result_id"] for record in canonical]
    duplicates = sorted({result_id for result_id in ids if ids.count(result_id) > 1})
    if duplicates:
        raise ResultValidationError(_message([f"duplicate result_id(s): {', '.join(duplicates)}."]))

    payload = {
        "schema_version": STORE_SCHEMA_VERSION,
        "records": sorted(canonical, key=lambda item: item["result_id"]),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return payload


def read_result_store(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != STORE_SCHEMA_VERSION:
        raise ResultValidationError(
            f"result store schema_version must be {STORE_SCHEMA_VERSION!r}, got {payload.get('schema_version')!r}."
        )
    records = payload.get("records")
    if not isinstance(records, list):
        raise ResultValidationError("result store records must be a list.")
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "records": [validate_record(record) for record in records],
    }


def _record_dict(record: Mapping[str, Any] | ResultRecord) -> dict[str, Any]:
    if isinstance(record, ResultRecord):
        return record.to_dict()
    if not isinstance(record, Mapping):
        raise ResultValidationError("record must be a mapping or ResultRecord.")
    return dict(record)


def _canonical(data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "conditions": dict(sorted(data["conditions"].items())),
        "data": _canonical_data(data["data"]),
        "kind": str(data["kind"]),
        "result_id": str(data["result_id"]),
        "source": dict(sorted(data["source"].items())),
        "spec_ref": dict(sorted(data["spec_ref"].items())),
        "subject": {"kind": str(data["subject"]["kind"]), "ref": str(data["subject"]["ref"])},
        "timestamp": str(data["timestamp"]),
    }
    if data.get("limits") is not None:
        out["limits"] = dict(sorted(data["limits"].items()))
    if data.get("outcome") is not None:
        out["outcome"] = dict(sorted(data["outcome"].items()))
    if data.get("note_for_main_lane"):
        out["note_for_main_lane"] = list(data["note_for_main_lane"])
    return out


def _canonical_data(data: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(sorted(data.items()))
    out["columns"] = [str(column) for column in data["columns"]]
    return out


def _validate_spec_ref(value: Any, problems: list[str]) -> None:
    if not isinstance(value, Mapping):
        problems.append("spec_ref must be an object with path and gds_sha256 or spec_id.")
        return
    if not isinstance(value.get("path"), str) or not value.get("path"):
        problems.append("spec_ref.path must be a non-empty string.")
    has_identity = bool(value.get("gds_sha256") or value.get("spec_id"))
    if not has_identity:
        problems.append("spec_ref must include gds_sha256 or spec_id.")


def _validate_subject(value: Any, problems: list[str]) -> None:
    if not isinstance(value, Mapping):
        problems.append("subject must be an object with kind and ref.")
        return
    kind = value.get("kind")
    if kind not in SUBJECT_KINDS:
        problems.append(f"subject.kind must be one of {', '.join(sorted(SUBJECT_KINDS))}.")
    if not isinstance(value.get("ref"), str) or not value.get("ref"):
        problems.append("subject.ref must be a non-empty string.")
    if kind == "terminal" and isinstance(value.get("ref"), str) and "." not in value["ref"]:
        problems.append("terminal subject.ref must use 'instance_id.terminal'.")


def _validate_result_data(value: Any, problems: list[str]) -> None:
    if not isinstance(value, Mapping):
        problems.append("data must be an object.")
        return
    columns = value.get("columns")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) and item for item in columns):
        problems.append("data.columns must be a non-empty list of strings.")
    has_file = isinstance(value.get("file"), str) and bool(value.get("file"))
    has_inline = "inline" in value
    if not has_file and not has_inline:
        problems.append("data must include data.file for CSV/JSON data or data.inline for small inline values.")


def _validate_source(value: Any, problems: list[str]) -> None:
    if not isinstance(value, Mapping):
        problems.append("source must be an object.")
        return
    if not any(value.get(key) for key in ("instrument_id", "operator", "script")):
        problems.append("source must include at least one of instrument_id, operator, or script.")


def _validate_flat_dict(value: Any, name: str, problems: list[str]) -> None:
    if not isinstance(value, Mapping):
        problems.append(f"{name} must be a flat object.")
        return
    for key, item in value.items():
        if not isinstance(key, str):
            problems.append(f"{name} keys must be strings.")
        if isinstance(item, (dict, list)):
            problems.append(f"{name}.{key} must be flat; nested objects/lists are not accepted in v1.")


def _validate_limits_or_outcome(value: Any, name: str, problems: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        problems.append(f"{name} must be an object when present.")
        return
    if not value.get("source"):
        problems.append(f"{name}.source is required; limits/outcome are recorded facts with a source.")


def _validate_timestamp(value: Any, problems: list[str]) -> None:
    if not isinstance(value, str) or not value:
        problems.append("timestamp must be an ISO 8601 string.")
        return
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(raw)
    except ValueError:
        problems.append("timestamp must parse as ISO 8601, e.g. 2025-01-01T12:00:00Z.")


def _require_string(data: Mapping[str, Any], key: str, problems: list[str]) -> None:
    if not isinstance(data.get(key), str) or not data.get(key):
        problems.append(f"{key} must be a non-empty string.")


def _message(problems: Sequence[str]) -> str:
    return "measurement result is invalid:\n- " + "\n- ".join(problems)
