from pathlib import Path

from tools.runner_types import RunnerResult, RunStatus


def test_runner_result_serializes_complete_public_contract():
    report = RunnerResult(
        run_id="job-1",
        status=RunStatus.BLOCKED,
        state_path=Path("state/job.json"),
        log_path=Path("runs/job.jsonl"),
        stages=("topic-selector", "researcher"),
        message="authorization required",
    )
    assert report.as_json() == {
        "run_id": "job-1",
        "status": "blocked",
        "state_path": "state/job.json",
        "log_path": "runs/job.jsonl",
        "stages": ["topic-selector", "researcher"],
        "message": "authorization required",
    }
