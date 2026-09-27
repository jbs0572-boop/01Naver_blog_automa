from pathlib import Path

from tools.codex_stage_command import stage_prompt
from tools.runner_types import StageExecutionContext


def test_stage_prompt_reads_project_and_execution_contracts(tmp_path: Path) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage="writer",
        run_id="RUN-contract",
        topic_id="TOPIC-contract",
        keyword="contract test",
        work_dir=tmp_path / ".automation" / "work",
    )

    prompt = stage_prompt(context, tmp_path / "writer.md", tmp_path / "result.json")

    assert str(tmp_path / "AGENTS.md") in prompt
    assert str(tmp_path / "EXECUTION_AGENT.md") in prompt
    assert "only those three files as project instructions" in prompt
