"""Require intend.md for every declared job, pipeline stage, and CI workflow."""

from pathlib import Path

from tools.runner_types import JobName, STAGE_ORDER
from tools.blog_v2 import V2_WORKFLOWS


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SECTIONS = (
    "## 목적",
    "## 시작 조건",
    "## 입력",
    "## 처리 및 산출물",
    "## 검증·완료 조건",
    "## 금지·중단 조건",
    "## 현재 구현 상태",
    "## 관련 원본",
)


def test_each_declared_workflow_has_exactly_one_intend_doc():
    ci = {path.stem for extension in ("*.yml", "*.yaml")
          for path in (ROOT / ".github" / "workflows").glob(extension)}
    expected = (
        {Path("workflows/jobs") / job.value / "intend.md" for job in JobName}
        | {Path("workflows/stages") / stage / "intend.md" for stage in STAGE_ORDER}
        | {Path("workflows/ci") / name / "intend.md" for name in ci}
        | {Path("workflows/v2") / name / "intend.md" for name in V2_WORKFLOWS}
    )
    actual = {path.relative_to(ROOT) for path in (ROOT / "workflows").rglob("intend.md")}
    assert actual == expected, f"missing={sorted(expected - actual)} unexpected={sorted(actual - expected)}"


def test_intend_docs_are_complete_and_link_to_actual_sources():
    for path in (ROOT / "workflows").rglob("intend.md"):
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---\n"), path
        assert all(section in content for section in REQUIRED_SECTIONS), path
        assert "이 문서는 실행 명령이나 권한 승인서가 아니며" in content, path
        fields = dict(
            line.split(": ", 1)
            for line in content.split("---", 2)[1].strip().splitlines()
        )
        assert fields["workflow_id"] and fields["kind"] and fields["source"]
        assert (ROOT / fields["source"]).is_file(), (path, fields["source"])
        assert len(content) > 600, path


def test_intend_docs_are_discoverable_from_index():
    index = (ROOT / "workflows" / "README.md").read_text(encoding="utf-8")
    for path in (ROOT / "workflows").rglob("intend.md"):
        relative = path.relative_to(ROOT / "workflows").as_posix()
        assert f"({relative})" in index, relative
