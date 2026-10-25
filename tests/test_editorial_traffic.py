import tempfile
import unittest
from pathlib import Path

from editorial_studio import traffic


class OrganicTrafficTests(unittest.TestCase):
    def test_no_real_data_never_fabricates_traffic(self):
        self.assertIsNone(traffic.summarize([])["natural_search_visits"])
        self.assertEqual(traffic.summarize([])["optimization_decision"],
                         "INSUFFICIENT_REAL_DATA")

    def test_verified_input_descriptive_only(self):
        rows = [
            {"category": "life_health", "post_id": "post-1",
             "analytics_source": "manual Naver analytics export",
             "start_date": "2026-10-01", "end_date": "2026-10-08",
             "organic_impressions": 1000, "organic_clicks": 55,
             "organic_sessions": 50}
        ]
        stats = traffic.summarize(rows)
        self.assertEqual(stats["natural_search_visits"], 50)
        self.assertEqual(stats["ctr"], 0.055)
        self.assertEqual(stats["optimization_decision"], "INSUFFICIENT_COMPARABLE_POSTS")

    def test_rejects_invented_analytics_shapes(self):
        record = {"category": "economy", "post_id": "1",
                  "analytics_source": "export", "start_date": "2026-10-01",
                  "end_date": "2026-10-08",
                  "organic_impressions": 5, "organic_clicks": 9,
                  "organic_sessions": 9}
        with self.assertRaises(traffic.TrafficError):
            traffic.summarize([record])
        record["organic_clicks"] = 1
        with self.assertRaises(traffic.TrafficError):
            traffic.summarize([record, record])


if __name__ == "__main__":
    unittest.main()
