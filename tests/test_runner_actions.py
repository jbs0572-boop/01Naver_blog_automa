from __future__ import annotations

import pytest

from tools.runner_actions import naver_input_title


@pytest.mark.parametrize(
    ("body", "fallback", "expected"),
    [
        (
            "[TITLE]유니클로 후드립T 가격·색상·사이즈｜핏과 세탁 총정리[/TITLE]",
            "유니클로 후드립T",
            "유니클로 후드립T 가격·색상·사이즈｜핏과 세탁 총정리",
        ),
        (
            (
                "[TITLE]\n"
                "비짓재팬 웹 등록 방법｜입국 전 준비 총정리\n\n"
                "[IMAGE file=\"thumbnail.png\" representative=true]\n"
            ),
            "비짓재팬",
            "비짓재팬 웹 등록 방법｜입국 전 준비 총정리",
        ),
        (
            "[TITLE]\n닫는 태그 호환 제목\n[/TITLE]\n",
            "fallback",
            "닫는 태그 호환 제목",
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
