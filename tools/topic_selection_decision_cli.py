from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.topic_metadata import read_snapshot
from tools.topic_selection_decision import reserve_topic_decision


def main() -> int:
    parser = argparse.ArgumentParser(prog="topic-selection-decision")
    _ = parser.add_argument("--root", type=Path, required=True)
    _ = parser.add_argument("--work-dir", type=Path, required=True)
    _ = parser.add_argument("--snapshot", type=Path, required=True)
    _ = parser.add_argument("--run-id", required=True)
    _ = parser.add_argument("--capture-id", required=True)
    _ = parser.add_argument("--as-of-date", required=True)
    _ = parser.add_argument("--created-at", required=True)
    parsed = parser.parse_args()
    snapshot = read_snapshot(
        parsed.snapshot,
        expected_capture_id=parsed.capture_id,
        expected_as_of_date=parsed.as_of_date,
    )
    decision = reserve_topic_decision(
        root=parsed.root,
        work_dir=parsed.work_dir,
        run_id=parsed.run_id,
        snapshot_path=parsed.snapshot,
        snapshot=snapshot,
        created_at=parsed.created_at,
    )
    _ = sys.stdout.write(
        json.dumps(decision.as_json(), ensure_ascii=False, sort_keys=True) + "\n"
    )
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
