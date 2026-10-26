"""Isolation, routing, and style-lock regression checks; no external services."""
import hashlib
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from editorial_studio import studio, topic_intents


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

    def test_topic_specific_headings_are_not_literal_template(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = studio.build_bundle("한국은행 기준금리와 물가", Path(temp))
            headings = [
                "기준금리와 물가가 함께 움직일까?",
                "먼저 금리와 물가의 뜻",
                "변화를 만드는 세 가지 조건",
                "가계에 미치는 영향과 반례",
                "핵심 요약과 공식 출처",
            ]
            draft = Path(temp) / "economy.md"
            draft.write_text("# 경제 설명\n\n" + "\n\n".join(
                f"## {heading}\n\n확인한 근거와 조건을 설명합니다." for heading in headings
            ), encoding="utf-8")
            self.assertEqual(studio.check_bundle(bundle, draft)["draft_structure"], "passed")
            draft.write_text(draft.read_text().replace("## 핵심 요약과 공식 출처",
                                  "## 가계에 미치는 영향과 반례"), encoding="utf-8")
            with self.assertRaisesRegex(studio.StyleError, "outline mismatch"):
                studio.check_bundle(bundle, draft)

    def test_repeated_topics_reuse_same_profile(self):
        a, _ = studio.choose("아파트 환기 방법")
        b, _ = studio.choose("겨울 실내 습도 관리")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["sha256"], b["sha256"])

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


    def test_five_distinct_purpose_intents_are_machine_and_human_readable(self):
        catalog = topic_intents.load_all()
        self.assertEqual(set(catalog), set(studio.PROFILE_IDS))
        self.assertEqual(len({x["search_intent"] for x in catalog.values()}), 5)
        self.assertEqual(len({x["answer_promise"] for x in catalog.values()}), 5)
        for category, intent in catalog.items():
            with self.subTest(category=category):
                self.assertEqual(intent["id"], category)
                self.assertEqual(intent["schema_version"], 1)
                self.assertEqual(len(intent["story_flow"]), 5)
                self.assertEqual(set(intent["image_roles"]), set(topic_intents.IMAGE_ROLES))
                self.assertEqual(len(intent["sha256"]), 64)
                self.assertGreaterEqual(len(intent["source_hierarchy"]), 2)
                self.assertGreaterEqual(len(intent["fact_checks"]), 2)

    def test_build_writes_individual_intend_and_locks_canonical_purpose(self):
        with tempfile.TemporaryDirectory() as root:
            for category in studio.PROFILE_IDS:
                with self.subTest(category=category):
                    intent = topic_intents.load(category)
                    topic = "이 카테고리에서 확인할 새 사례: " + category
                    bundle = studio.build_bundle(
                        topic, Path(root), explicit=category
                    )
                    lock = json.loads((bundle / "style-lock.json").read_text(encoding="utf-8"))
                    article = (bundle / "intend.md").read_bytes()
                    self.assertEqual(lock["intend_sha256"], intent["sha256"])
                    self.assertEqual(lock["intend_file"], intent["source_path"])
                    self.assertEqual(
                        lock["artifacts_sha256"]["intend.md"],
                        hashlib.sha256(article).hexdigest(),
                    )
                    self.assertIn(topic, article.decode("utf-8"))
                    self.assertIn(intent["search_intent"], article.decode("utf-8"))
                    self.assertIn("네이버·Notion 미저장", article.decode("utf-8"))
                    self.assertEqual(studio.check_bundle(bundle)["intent_lock"], "passed")
                    writer = (bundle / "writer-brief.md").read_text(encoding="utf-8")
                    image = (bundle / "image-brief.md").read_text(encoding="utf-8")
                    self.assertIn(intent["answer_promise"], writer)
                    self.assertIn(intent["original_visual_target"], image)
                    for role in topic_intents.IMAGE_ROLES:
                        self.assertIn(intent["image_roles"][role], image)

    def test_per_article_intend_does_not_claim_source_is_verified(self):
        text = topic_intents.render_for_topic(
            "economy", "마트 물가를 보는 이유",
            case={"topic": "소비자물가지수", "as_of": "2026-10-26",
                  "sources": [{"id": "S1"}],
                  "facts": [{"id": "F1"}]},
        )
        self.assertIn("마트 물가를 보는 이유", text)
        self.assertIn("근거 기준일: 2026-10-26", text)
        self.assertIn("수록 출처: 1", text)
        self.assertIn("아직 사람 확인 필요", text)
        self.assertIn("95/100", text)

    def test_tampered_per_article_intend_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            bundle = studio.build_bundle("영화 해석", Path(root), "culture_people")
            with (bundle / "intend.md").open("a", encoding="utf-8") as handle:
                handle.write("\n주제 의도 위조")
            with self.assertRaisesRegex(studio.StyleError, "modified or corrupt"):
                studio.check_bundle(bundle)

    def test_canonical_intend_drift_revokes_existing_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            bundle = studio.build_bundle("전세 계약", base, "real_estate")
            cloned = base / "topic-intents"
            shutil.copytree(topic_intents.TOPIC_DIR, cloned)
            altered = cloned / "real_estate" / "intend.md"
            altered.write_text(altered.read_text(encoding="utf-8") + "\n개정 문구\n",
                               encoding="utf-8")
            with patch.object(topic_intents, "TOPIC_DIR", cloned):
                with self.assertRaisesRegex(studio.StyleError, "intend.md drift"):
                    studio.check_bundle(bundle)

    def test_broken_or_wrong_category_intend_never_silently_falls_back(self):
        with tempfile.TemporaryDirectory() as root:
            cloned = Path(root) / "topics"
            shutil.copytree(topic_intents.TOPIC_DIR, cloned)
            selected = cloned / "life_health" / "intend.md"
            raw = selected.read_text(encoding="utf-8")
            selected.write_text(raw.replace('"id": "life_health"',
                                            '"id": "economy"'), encoding="utf-8")
            with patch.object(topic_intents, "TOPIC_DIR", cloned):
                with self.assertRaisesRegex(topic_intents.IntentError, "category ID mismatch"):
                    topic_intents.load("life_health")
            selected.unlink()
            with patch.object(topic_intents, "TOPIC_DIR", cloned):
                with self.assertRaisesRegex(topic_intents.IntentError, "missing category intend.md"):
                    topic_intents.load_all()



if __name__ == "__main__":
    unittest.main()
