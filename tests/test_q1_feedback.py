from __future__ import annotations

import json
from pathlib import Path

from tools.q1_feedback import active_q1_codes, guidance_for


def _write_config(root: Path, *, mode: str = "active") -> None:
    config = root / "config" / "q1-feedback-rollout.json"
    _ = config.parent.mkdir(parents=True, exist_ok=True)
    _ = config.write_text(
        json.dumps(
            {
                "schema_version": "q1-feedback-rollout-v1",
                "mode": mode,
                "lookback_days": 90,
                "minimum_repaired_runs": 3,
                "max_active_codes": 10,
            }
        ),
        encoding="utf-8",
    )


def _write_run(root: Path, run_id: str, status: str) -> None:
    path = root / "runs" / f"{run_id}.jsonl"
    _ = path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "event_type": "stage",
            "pipeline_version": "workflow-optimized-v1",
            "run_id": run_id,
            "stage": "content-assembler",
            "status": "failed" if status == "repaired" else "passed",
            "attempt": 1,
            "error_type": "q1_contract_failure" if status == "repaired" else None,
            "ended_at": "2026-09-01T09:00:00+09:00",
        }
    ]
    if status == "repaired":
        events.append(
            {
                **events[0],
                "status": "passed",
                "attempt": 2,
                "error_type": None,
            }
        )
    _ = path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )


def test_active_q1_codes_promote_only_repaired_distinct_runs(tmp_path: Path) -> None:
    _write_config(tmp_path)
    for run_id in ("RUN-1", "RUN-2", "RUN-3"):
        _write_run(tmp_path, run_id, "repaired")
    _write_run(tmp_path, "RUN-4", "failed")

    assert active_q1_codes(tmp_path) == ("q1_contract_failure",)


def test_shadow_mode_does_not_activate_codes(tmp_path: Path) -> None:
    _write_config(tmp_path, mode="shadow")
    for run_id in ("RUN-1", "RUN-2", "RUN-3"):
        _write_run(tmp_path, run_id, "repaired")

    assert active_q1_codes(tmp_path) == ()


def test_guidance_is_static_and_known() -> None:
    assert "Q1" in guidance_for("q1_contract_failure")
    assert guidance_for("unknown") == ""
