"""Isolation, routing, and style-lock regression checks; no external services."""
import json
import tempfile
import unittest
from pathlib import Path

from editorial_studio import studio


class IndependentStyleTests(unittest.TestCase):
    def test_five_distinct_styles(self):
        catalog = studio.profiles()
        self.assertEqual(len(catalog), 5)
        for attribute in ("tone", "stance", "image_style"):
            self.assertEqual(len({p[attribute] for p in catalog.values()}), 5)
        self.assertEqual(len({tuple(p["sections"]) for p in catalog.values()}), 5)

    def test_routing_by_category(self):
        examples = {
            "아파트 창문 환기 방법": "life_health",
            "인공지능 기본법 시행일과 정책": "news_policy",
            "영화 기생충 감독과 작품 해석": "culture_people",
            "전세계약 전 등기부 확인": "real_estate",
            "한국은행 기준금리와 소비자물가": "economy",
        }
        for topic, expected in examples.items():
            with self.subTest(topic=topic):
                selected, scores = studio.choose(topic)
                self.assertEqual(selected["id"], expected)
                self.assertGreater(scores[expected], 0)

    def test_ambiguous_requires_explicit_category(self):
        with self.assertRaisesRegex(studio.StyleError, "ambiguous"):
            studio.choose("전세 기준금리")
        selected, _ = studio.choose("전세 기준금리", "real_estate")
        self.assertEqual(selected["id"], "real_estate")

    def test_unknown_category_and_topic(self):
        with self.assertRaises(studio.StyleError):
            studio.choose("아무런 키워드가 없는 소재")
        with self.assertRaises(studio.StyleError):
            studio.choose(" ", "economy")
        with self.assertRaises(studio.StyleError):
            studio.choose("환기", "whatever")

    def test_bundle_is_style_locked_and_not_published(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = studio.build_bundle("아파트 환기 원리", Path(temp))
            meta = json.loads((bundle / "style-lock.json").read_text())
            self.assertEqual(meta["category"], "life_health")
            self.assertEqual(meta["status"], "BRIEF_ONLY")
            self.assertFalse(meta["images_generated"])
            self.assertFalse(meta["draft_generated"])
            self.assertFalse(meta["notion_or_naver_write"])
            self.assertEqual(studio.check_bundle(bundle)["style_lock"], "passed")
            writer = (bundle / "writer-brief.md").read_text()
            self.assertIn("부드러운 해요체", writer)
            self.assertNotIn("분석적이고 간결한 합니다체", writer)
            with self.assertRaisesRegex(studio.StyleError, "already exists"):
                studio.build_bundle("아파트 환기 원리", Path(temp))

    def test_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = studio.build_bundle("등기부 확인", Path(temp))
            with (bundle / "image-brief.md").open("a", encoding="utf-8") as stream:
                stream.write("\n몰래 바꾼 이미지 지침")
            with self.assertRaisesRegex(studio.StyleError, "modified or corrupt"):
                studio.check_bundle(bundle)

    def test_draft_outline_and_fabricated_experience(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = studio.build_bundle("영화 기생충", Path(temp))
            headings = studio.profiles()["culture_people"]["sections"]
            draft = Path(temp) / "draft.md"
            draft.write_text("# 기생충 해설\n\n" + "\n\n".join(
                f"## {heading}\n\n자료를 근거로 정리했습니다." for heading in headings
            ), encoding="utf-8")
            self.assertEqual(studio.check_bundle(bundle, draft)["draft_structure"], "passed")
            draft.write_text(draft.read_text() + "\n제가 관람했는데 좋았어요.", encoding="utf-8")
            with self.assertRaisesRegex(studio.StyleError, "forbidden"):
                studio.check_bundle(bundle, draft)
            draft.write_text("## 엉뚱한 구조\n\n본문", encoding="utf-8")
            with self.assertRaisesRegex(studio.StyleError, "outline mismatch"):
                studio.check_bundle(bundle, draft)


if __name__ == "__main__":
    unittest.main()
