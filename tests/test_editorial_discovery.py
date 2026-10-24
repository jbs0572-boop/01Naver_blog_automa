"""Naver Datalab topic discovery tests: all requests offline."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from editorial_studio import discovery


TOPICS = [
    {"query": "아파트 환기", "category": "life_health"},
    {"query": "실내 습도", "category": "life_health"},
]


class NaverTrendDiscoveryTests(unittest.TestCase):
    def test_plan_defaults_to_mobile_and_contains_no_credentials(self):
        plan = discovery.make_plan(TOPICS, "2026-08-01", "2026-10-24")
        self.assertEqual(plan["timeUnit"], "week")
        self.assertEqual(plan["device"], "mo")
        self.assertEqual(len(plan["keywordGroups"]), 2)
        self.assertNotIn("secret", json.dumps(plan).lower())

    def test_relative_ranking_not_absolute_search_volume(self):
        plan = discovery.make_plan(TOPICS, "2026-08-01", "2026-10-24")
        mock = {"results": [
            {"title": "아파트 환기", "data": [
                {"period": str(i), "ratio": v} for i, v in enumerate([10, 20, 40, 80])
            ]},
            {"title": "실내 습도", "data": [
                {"period": str(i), "ratio": v} for i, v in enumerate([80, 60, 20, 10])
            ]}
        ]}
        ranked = discovery.score_trends(plan, mock)
        self.assertEqual(ranked["ranked"][0]["query"], "아파트 환기")
        self.assertIsNone(ranked["absolute_volume"])
        self.assertIsNone(ranked["estimated_clicks"])
        self.assertEqual(ranked["comparability"], "RELATIVE_IN_THIS_RESPONSE_ONLY")

    def test_no_request_without_explicit_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            topics = folder / "topics.json"
            out = folder / "plan.json"
            topics.write_text(json.dumps({"topics": TOPICS}), encoding="utf-8")
            with patch.object(discovery, "_post",
                              side_effect=AssertionError("must not contact Naver")):
                code = discovery.main([
                    "--topics", str(topics), "--start", "2026-08-01",
                    "--end", "2026-10-24", "--out", str(out)
                ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text())["status"], "PLAN_ONLY")

    def test_opt_in_requires_credentials(self):
        plan = discovery.make_plan(TOPICS, "2026-08-01", "2026-10-24")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(discovery.DiscoveryError, "missing"):
                discovery._post(plan)

    def test_invalid_and_cross_cohort_rejected(self):
        with self.assertRaises(discovery.DiscoveryError):
            discovery.make_plan([TOPICS[0]] * 6, "2026-08-01", "2026-10-24")
        with self.assertRaises(discovery.DiscoveryError):
            discovery.make_plan(TOPICS, "2026-10-24", "2026-08-01")
        plan = discovery.make_plan(TOPICS, "2026-08-01", "2026-10-24")
        with self.assertRaises(discovery.DiscoveryError):
            discovery.score_trends(plan, {"results": [
                {"title": "아파트 환기", "data": [{"ratio": 50}]},
                {"title": "실내 습도", "data": [{"ratio": 40}]},
            ]})


if __name__ == "__main__":
    unittest.main()
