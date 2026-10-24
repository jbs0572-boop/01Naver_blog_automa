"""Security-relevant CLI/API protocol cases, no real HTTP or billed operations."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from editorial_studio import benchmark, discovery, engine, evolution, studio, traffic
from editorial_studio import quality


def feedback():
    categories = list(studio.PROFILE_IDS)
    weights = quality.policy("v2")["weights"]
    records = []
    for i in range(15):
        bad = i < 6 or i in (10, 11, 12)
        scores = {name: 1.0 for name in weights}
        if bad:
            scores["image_quality"] = 0.25
        records.append({
            "case_id": "independent-review-" + str(i),
            "category": categories[i % 5],
            "set": "train" if i < 10 else "holdout",
            "human_review": "fail" if bad else "pass",
            "failure_reasons": ["image_offtopic"] if bad else [],
            "dimensions": scores,
        })
    return records


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "editorial_studio" / "benchmarks" / "cases.json"


class FakeHttp:
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, maximum=None):
        return self.data


class ProtocolAndCLITests(unittest.TestCase):
    def test_real_text_endpoint_protocol_mocked(self):
        captures = []
        def fake(req, timeout=None):
            captures.append(req)
            return FakeHttp({"output_text": "{}"})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "TEST-NOT-REAL"}, clear=True):
            with patch.object(engine, "urlopen", side_effect=fake):
                result = engine._api_post("/responses", {"model": "unit", "store": False})
        self.assertEqual(result["output_text"], "{}")
        self.assertEqual(captures[0].get_method(), "POST")
        self.assertEqual(captures[0].full_url, "https://api.openai.com/v1/responses")
        self.assertIn(b'"store":false', captures[0].data)
        with self.assertRaises(engine.EngineError):
            engine._api_post("/unexpected", {"model": "bad"})

    def test_api_fails_without_credentials_and_does_not_echo(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(engine.EngineError, "OPENAI_API_KEY missing"):
                engine._api_post("/responses", {"model": "gpt"})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "SECRET-NEVER-LOG"}, clear=True):
            with patch.object(engine, "urlopen", side_effect=OSError("SECRET-NEVER-LOG")):
                with self.assertRaises(engine.EngineError) as caught:
                    engine._api_post("/responses", {})
            self.assertNotIn("SECRET-NEVER-LOG", str(caught.exception))

    def test_naver_endpoint_mock_uses_relative_trend_only(self):
        captures = []
        def fake(req, timeout=None):
            captures.append(req)
            return FakeHttp({"results": []})
        plan = discovery.make_plan([
            {"query": "아파트 환기", "category": "life_health"},
            {"query": "실내 습도", "category": "life_health"}
        ], "2026-08-01", "2026-10-24")
        with patch.dict(os.environ, {"NAVER_CLIENT_ID": "FAKE", "NAVER_CLIENT_SECRET": "FAKESECRET"}, clear=True):
            with patch.object(discovery, "urlopen", side_effect=fake):
                self.assertEqual(discovery._post(plan)["results"], [])
        self.assertEqual(captures[0].full_url, discovery.ENDPOINT)
        self.assertEqual(captures[0].get_method(), "POST")
        self.assertEqual(json.loads(captures[0].data)["device"], "mo")

    def test_engine_cli_generates_draft_not_fake_publishing(self):
        with tempfile.TemporaryDirectory() as temp:
            case = json.loads(DATA.read_text(encoding="utf-8"))["cases"][0]
            source = Path(temp) / "case.json"
            source.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
            result = engine.main(["--case", str(source), "--output-root",
                                  str(Path(temp) / "bundles")])
            self.assertEqual(result, 0)
            outputs = list((Path(temp) / "bundles").glob("*/result.json"))
            self.assertEqual(len(outputs), 1)
            status = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertEqual(status["state"], "DRAFT_ONLY")
            self.assertFalse(status["external_service_writes"])
            self.assertFalse(status["score"]["ready_for_publication"])
            self.assertEqual(engine.main(["--case", str(source), "--output-root",
                                          str(Path(temp) / "bundles")]), 2)

    def test_benchmark_cli_outputs_frozen_score(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "regression"
            ret = benchmark.main(["--cases", str(DATA), "--out-dir", str(output)])
            self.assertEqual(ret, 0)
            report = json.loads((output / "benchmark-results.json").read_text())
            self.assertEqual(report["article_count"], 10)
            self.assertFalse(report["traffic_metrics_available"])
            self.assertGreater(report["rubric_comparison"]["v1_mean"],
                               report["rubric_comparison"]["v2_mean"])

    def test_studio_cli_returns_closed_on_bad_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            ret = studio.main(["build", "--topic", "아파트 환기",
                               "--output-root", temp])
            self.assertEqual(ret, 0)
            self.assertEqual(studio.main(["build", "--topic", "아파트 환기",
                                          "--output-root", temp]), 2)

    def test_trend_cli_mock_vs_live_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            topics = folder / "topics.json"
            topics.write_text(json.dumps({"topics": [
                {"query": "아파트 환기", "category": "life_health"},
                {"query": "실내 습도", "category": "life_health"},
            ]}), encoding="utf-8")
            response = folder / "trend.json"
            response.write_text(json.dumps({"results": [
                {"title": topic, "data": [{"ratio": n} for n in nums]}
                for topic, nums in [("아파트 환기", [2, 7, 20, 40]),
                                    ("실내 습도", [35, 20, 12, 8])]
            ]}), encoding="utf-8")
            out = folder / "mock-rank.json"
            args = ["--topics", str(topics), "--start", "2026-08-01",
                    "--end", "2026-10-24", "--mock-response", str(response),
                    "--out", str(out)]
            self.assertEqual(discovery.main(args), 0)
            result = json.loads(out.read_text())
            self.assertEqual(result["data_provenance"], "MOCK_NOT_NAVER_DATA")
            self.assertFalse(result["live_request_executed"])
            self.assertEqual(discovery.main(args), 2)

    def test_evolution_command_not_silent_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            src = folder / "human-feedback.json"
            src.write_text(json.dumps(feedback()), encoding="utf-8")
            out = folder / "candidate.json"
            self.assertEqual(evolution.main(["--feedback", str(src),
                                              "--out", str(out)]), 0)
            item = json.loads(out.read_text())
            self.assertEqual(item["decision"], "HOLDOUT_SUPPORTED_PROPOSAL")
            self.assertEqual(item["modified_rubric"]["status"],
                             "NOT_ACTIVE_HUMAN_APPROVAL_REQUIRED")
            self.assertEqual(evolution.main(["--feedback", str(src),
                                              "--out", str(out)]), 2)

    def test_traffic_cli_never_infers_visits(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "metrics.json"
            source.write_text("[]")
            output = folder / "report.json"
            self.assertEqual(traffic.main(["--analytics", str(source),
                                            "--out", str(output)]), 0)
            item = json.loads(output.read_text())
            self.assertIsNone(item["natural_search_visits"])


if __name__ == "__main__":
    unittest.main()
