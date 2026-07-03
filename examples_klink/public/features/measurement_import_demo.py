"""Synthetic measurement import demo.

Builds a tiny IV CSV and a resistance record, binds both to the synthetic
Inverter-shaped spec fixture, writes a deterministic result store, and prints
an explicit PASS line.
"""

from __future__ import annotations

import csv
from pathlib import Path

from klink.domains.measurement import bind_file, write_result_store


SPEC_PATH = Path("tests/fixtures/measurement/inverter_synthetic.klink.spec.json")


def main() -> None:
    out_dir = Path(".klink") / "measurement_demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "x1_drain_iv.csv"
    store_path = out_dir / "measurement_results.json"
    _write_iv_csv(csv_path)

    records = [
        {
            "result_id": "x1_drain_iv_001",
            "spec_ref": {"path": str(SPEC_PATH), "spec_id": "synthetic_inverter_v1"},
            "subject": {"kind": "terminal", "ref": "X1.D"},
            "kind": "iv_sweep",
            "data": {
                "columns": ["v_drain_V", "i_drain_A"],
                "file": str(csv_path),
            },
            "conditions": {"temperature_K": 295.0, "v_gate_V": 1.2},
            "limits": {"lo": -0.001, "hi": 0.001, "units": "A", "source": "STDF PTR LO_LIMIT/HI_LIMIT"},
            "outcome": {"value": "not_evaluated", "source": "demo_import_fact"},
            "source": {"instrument_id": "synthetic_smu", "operator": "demo", "script": "measurement_import_demo.py"},
            "timestamp": "2025-01-01T12:00:00Z",
        },
        {
            "result_id": "out_resistance_001",
            "spec_ref": {"path": str(SPEC_PATH), "spec_id": "synthetic_inverter_v1"},
            "subject": {"kind": "net", "ref": "OUT"},
            "kind": "resistance",
            "data": {
                "columns": ["resistance_ohm"],
                "inline": [123.4],
            },
            "conditions": {"temperature_K": 295.0},
            "source": {"instrument_id": "synthetic_dmm", "operator": "demo", "script": "measurement_import_demo.py"},
            "timestamp": "2025-01-01T12:05:00Z",
        },
    ]
    write_result_store(store_path, records)
    bound = [bind_file(record, SPEC_PATH) for record in records]

    print("result_id subject kind data")
    for record in bound:
        subject = record["subject"]
        print(f"{record['result_id']} {subject['kind']}:{subject['ref']} {record['kind']} {record['data']['columns']}")
    print(f"store={store_path}")
    print("PASS measurement_import_demo")


def _write_iv_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["v_drain_V", "i_drain_A"])
        writer.writerow([0.0, 0.0])
        writer.writerow([0.1, 1e-6])
        writer.writerow([0.2, 2e-6])


if __name__ == "__main__":
    main()
