from __future__ import annotations

import pytest

from tools.runner_actions import naver_input_title


@pytest.mark.parametrize(
    ("body", "fallback", "expected"),
    [
        (
            "[TITLE]\n비짓재팬 웹 등록 방법｜입국 전 준비 총정리\n[/TITLE]\n",
            "비짓재팬",
            "비짓재팬 웹 등록 방법｜입국 전 준비 총정리",
        ),
        ("# Markdown 제목\n\n본문", "fallback", "Markdown 제목"),
        ("본문만 있습니다", "fallback", "fallback"),
    ],
)
def test_naver_input_title_prefers_canonical_title_then_markdown_then_fallback(
    body: str,
    fallback: str,
    expected: str,
) -> None:
    # Given: a canonical Naver input body, legacy Markdown, or no title marker.
    # When: the Naver confirmation title is extracted.
    actual = naver_input_title(body, fallback)

    # Then: the most specific available title is shown to the user.
    assert actual == expected
