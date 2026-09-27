from __future__ import annotations

from pathlib import Path

from tools.codex_stage_command import stage_prompt
from tools.runner_types import StageExecutionContext


def test_active_q1_preflight_guidance_is_added_to_content_prompt(
    tmp_path: Path,
) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage="content-assembler",
        run_id="RUN-1",
        topic_id="TOPIC-1",
        keyword="topic",
        work_dir=tmp_path / "work",
        q1_preflight_codes=("q1_contract_failure",),
    )

    prompt = stage_prompt(
        context, tmp_path / "content-assembler.md", tmp_path / "result.json"
    )

    assert "Active Q1 preflight guidance" in prompt
    assert "canonical manifest" in prompt


def test_researcher_prompt_forbids_isolated_browser_and_keeps_lane_order(
    tmp_path: Path,
) -> None:
    context = StageExecutionContext(
        root=tmp_path,
        stage="researcher",
        run_id="RUN-1",
        topic_id="TOPIC-1",
        keyword="literal keyword",
        work_dir=tmp_path / "work",
    )

    prompt = stage_prompt(context, tmp_path / "researcher.md", tmp_path / "result.json")

    assert "Do not invoke browser, network, or Aside CLI tools" in prompt
    assert "Run `aside guide`" not in prompt
    assert prompt.index("official lane") < prompt.index("supporting-visual lane")


def test_content_assembler_contract_never_returns_to_writer() -> None:
    text = (Path(__file__).parents[1] / "content-assembler.md").read_text(
        encoding="utf-8"
    )

    assert "writer로 되돌" not in text
    assert "writer 재호출" not in text
    assert "이전 producer를 자동 재호출하지 않는다" in text
