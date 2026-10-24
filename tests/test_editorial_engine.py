"""Executable, offline-only contract tests of real pipeline adapters and scoring.

No API calls occur: adapters are mocked; image pixels are decoded with Pillow.
"""
from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from editorial_studio import benchmark, engine, quality, studio


BENCH = Path(__file__).resolve().parents[1] / "editorial_studio" / "benchmarks" / "cases.json"


def cases():
    return json.loads(BENCH.read_text(encoding="utf-8"))["cases"]


def image_bytes(*, width=1536, height=1024, constant=False):
    if constant:
        image = Image.new("RGB", (width, height), (125, 125, 125))
    else:
        # Broad shapes + texture: distinguish real visual content from random-noise placeholders.
        red = Image.linear_gradient("L").resize((width, height))
        green = Image.radial_gradient("L").resize((width, height))
        blue = Image.effect_noise((width, height), 80)
        image = Image.merge("RGB", (red, green, blue))
    handle = io.BytesIO()
    image.save(handle, format="PNG")
    return handle.getvalue()


class FiveStyleEngineTests(unittest.TestCase):
    def test_evidence_linked_case_shapes(self):
        for case in cases():
            with self.subTest(case=case["case_id"]):
                normalized = engine.load_case(case)
                self.assertEqual({x["role"] for x in normalized["facts"]}, set(range(5)))
                self.assertEqual(normalized["category"], case["category"])
                self.assertTrue(all(x["url"].startswith("https://")
                                    for x in normalized["sources"]))

    def test_ten_distinct_articles_two_per_style(self):
        with tempfile.TemporaryDirectory() as temp:
            result = benchmark.run_benchmark(BENCH, Path(temp))
            self.assertEqual(result["article_count"], 10)
            self.assertEqual(result["pair_count"], 5)
            self.assertTrue(result["no_near_duplicates"])
            self.assertFalse(result["all_images_present"])
            self.assertFalse(result["traffic_metrics_available"])
            self.assertTrue(all(x["style_hash_equal"] for x in result["pairs"]))
            self.assertTrue(all(x["image_prompts_distinct"] for x in result["pairs"]))
            self.assertFalse(any(
                (Path(temp) / "articles").rglob("cover.png")
            ))

    def test_image_quality_uses_decoded_pixels_not_labels(self):
        with self.assertRaises(engine.EngineError):
            engine.inspect_cover(b"high resolution illustration")
        with self.assertRaises(engine.EngineError):
            engine.inspect_cover(image_bytes(width=10, height=10))
        with self.assertRaises(engine.EngineError):
            engine.inspect_cover(image_bytes(constant=True))
        data = image_bytes()
        result = engine.inspect_cover(data)
        self.assertEqual((result["width"], result["height"]), (1536, 1024))
        self.assertGreater(result["color_bins"], 30)
        self.assertEqual(result["visual_review"], "REQUIRED")

    def test_import_image_real_preview_and_pending_review(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            case = folder / "topic.json"
            case.write_text(json.dumps(cases()[0], ensure_ascii=False), encoding="utf-8")
            image = folder / "image.png"
            image.write_bytes(image_bytes())
            output = engine.run(case, folder / "runs", text_backend="fixture",
                                image_backend="import", imported_image=image)
            status = json.loads((output / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "IMAGE_READY_FOR_REVIEW")
            self.assertFalse(status["images_created_by_this_run"])
            self.assertEqual(status["review"], "PENDING")
            self.assertFalse(status["score"]["ready_for_publication"])
            self.assertTrue((output / "cover.png").exists())
            self.assertIn("viewport", (output / "preview.html").read_text())
            self.assertIn("cover.png", (output / "draft.md").read_text())

    def test_image_generation_adapter_real_endpoint_contract(self):
        payload = image_bytes()
        def pretend(path, settings, timeout=0):
            self.assertEqual(path, "/images/generations")
            self.assertEqual(settings["model"], engine.IMAGE_MODEL)
            self.assertEqual(settings["size"], "1536x1024")
            self.assertEqual(settings["quality"], "xhigh")
            return {"data": [{"b64_json": base64.b64encode(payload).decode("ascii")}]}
        with patch.object(engine, "_api_post", side_effect=pretend):
            output = engine.generate_cover("a topic-specific, premium image")
        self.assertEqual(output, payload)

    def test_text_generation_adapter_receives_exact_style(self):
        case = engine.load_case(cases()[0])
        profile = studio.profiles()[case["category"]]
        obj = engine.fixture_article(case, profile)
        def pretend(path, settings, timeout=0):
            self.assertEqual(path, "/responses")
            self.assertIn(profile["tone"], settings["input"])
            self.assertNotIn(studio.profiles()["economy"]["tone"], settings["input"])
            self.assertFalse(settings["store"])
            return {"output": [{"content": [
                {"type": "output_text", "text": json.dumps(obj, ensure_ascii=False)}
            ]}]}
        with patch.object(engine, "_api_post", side_effect=pretend):
            response = engine.llm_article(case, profile)
        self.assertEqual(engine.validate_article(response, case, profile)["title"], case["title"])

    def test_paid_calls_blocked_without_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            item = Path(temp) / "case.json"
            item.write_text(json.dumps(cases()[0], ensure_ascii=False), encoding="utf-8")
            with patch.object(engine, "_api_post", side_effect=AssertionError("paid request ran")):
                with self.assertRaisesRegex(engine.EngineError, "paid model calls disabled"):
                    engine.run(item, Path(temp) / "runs", text_backend="openai", image_backend="none")

    def test_untrusted_sources_and_facts_rejected(self):
        obj = cases()[0].copy()
        obj["sources"] = [{"id": "S1", "title": "malicious",
                           "url": "http://localhost/"}]
        with self.assertRaises(engine.EngineError):
            engine.load_case(obj)
        obj = cases()[0].copy()
        obj["facts"] = [dict(x) for x in obj["facts"]]
        obj["facts"][2]["source_id"] = "S99"
        with self.assertRaises(engine.EngineError):
            engine.load_case(obj)

    def test_unknown_fact_id_and_style_prohibited_fails(self):
        sample = engine.load_case(cases()[2])
        profile = studio.profiles()[sample["category"]]
        article = engine.fixture_article(sample, profile)
        article["sections"][2]["fact_ids"] = ["F999"]
        with self.assertRaisesRegex(engine.EngineError, "unsupported fact IDs"):
            engine.validate_article(article, sample, profile)
        article = engine.fixture_article(sample, profile)
        article["intro"] = "제가 직접 취재했습니다."
        with self.assertRaisesRegex(engine.EngineError, "excluded phrase"):
            engine.validate_article(article, sample, profile)

    def test_profile_image_prompts_are_topic_distinct(self):
        case_a, case_b = [engine.load_case(cases()[i]) for i in (8, 9)]
        profile = studio.profiles()["economy"]
        prompts = [
            engine.image_prompt(case, profile, engine.fixture_article(case, profile))
            for case in [case_a, case_b]
        ]
        self.assertNotEqual(prompts[0], prompts[1])
        for p in prompts:
            self.assertIn(profile["image_style"], p)
            self.assertIn(profile["image_plan"], p)
            self.assertIn(profile["visual_dna"], p)
            self.assertNotIn(studio.profiles()["life_health"]["image_style"], p)

    def test_duplicate_covers_across_two_articles_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.png"
            image.write_bytes(image_bytes())
            with self.assertRaisesRegex(benchmark.BenchmarkError, "duplicate cover"):
                benchmark.run_benchmark(BENCH, Path(temp) / "out", {
                    "life_health-1": str(image), "life_health-2": str(image),
                })

    def test_rubric_versions_frozen_and_strict(self):
        self.assertEqual(sum(quality.policy("v1")["weights"].values()), 100)
        self.assertEqual(sum(quality.policy("v2")["weights"].values()), 100)
        self.assertGreater(quality.policy("v2")["weights"]["image_quality"],
                           quality.policy("v1")["weights"]["image_quality"])
        with tempfile.TemporaryDirectory() as temp:
            tempdir = Path(temp)
            bad = quality.policy("v2").copy()
            bad["weights"] = bad["weights"].copy()
            bad["weights"]["source_integrity"] = 20
            bad["weights"]["mobile_layout"] = 10
            (tempdir / "v2.json").write_text(json.dumps(bad))
            with patch.object(quality, "RUBRICS", tempdir):
                with self.assertRaisesRegex(quality.QualityError, "safety/accuracy floor"):
                    quality.policy("v2")

    def test_proxies_not_organic_traffic_and_post_metrics_only(self):
        self.assertIsNone(quality.measured_outcomes([])["organic_clicks"])
        with self.assertRaises(quality.QualityError):
            quality.measured_outcomes([
                {"category": "economy", "organic_impressions": 3, "organic_clicks": 9}
            ])
        measured = quality.measured_outcomes([
            {"category": "economy", "organic_impressions": 500, "organic_clicks": 21}
        ])
        self.assertEqual(measured["by_category"]["economy"]["ctr"], 0.042)


if __name__ == "__main__":
    unittest.main()
