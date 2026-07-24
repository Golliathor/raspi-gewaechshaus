from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from greenhouse.config import load_config
from greenhouse.controllers import create_controller
from greenhouse.metrics import calculate_metrics
from greenhouse.models import CycleResult
from greenhouse.records import DECISION_FIELDS, decision_to_row, read_snapshots
from greenhouse.runtime import ControlEngine


def run_replay(
    input_path: Path,
    output_dir: Path,
    *,
    controller_id: str,
    config: dict[str, Any],
    run_id: str,
) -> tuple[list[CycleResult], dict[str, Any]]:
    snapshots = list(read_snapshots(input_path))
    controller = create_controller(controller_id)
    engine = ControlEngine(controller, config)
    results: list[CycleResult] = []
    last_watering_check = None
    interval = float(config.get("watering_check_interval_seconds", 300))

    for snapshot in snapshots:
        due = (
            last_watering_check is None
            or (snapshot.timestamp - last_watering_check).total_seconds() >= interval
        )
        if due:
            last_watering_check = snapshot.timestamp
        results.append(engine.step(snapshot, now=snapshot.timestamp, watering_check_due=due))

    output_dir.mkdir(parents=True, exist_ok=True)
    decision_path = output_dir / "decisions.csv"
    with decision_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DECISION_FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerow(decision_to_row(result, run_id))

    metrics = calculate_metrics(results, config)
    summary = {
        "run_id": run_id,
        "controller_id": controller_id,
        "input_file": input_path.name,
        "metrics": metrics,
    }
    with (output_dir / "metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    return results, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gewächshaus-Regler deterministisch abspielen")
    parser.add_argument("--controller", required=True, help="Controller-ID")
    parser.add_argument("--config", type=Path, required=True, help="config.json")
    parser.add_argument("--input", type=Path, required=True, help="Snapshot-CSV")
    parser.add_argument("--run-id", required=True, help="Eindeutige Versuchs-ID")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config, validate=True)
    run_replay(
        args.input,
        args.output_dir,
        controller_id=args.controller,
        config=config,
        run_id=args.run_id,
    )


if __name__ == "__main__":
    main()
