"""Never tune quality scoring on the same cases without a human-labelled holdout."""
import json
import tempfile
import unittest
from pathlib import Path

from editorial_studio import evolution, quality, studio


def feedback(image_fails=True):
    names = list(studio.PROFILE_IDS)
    dims = list(quality.policy("v2")["weights"])
    rows = []
    for index in range(15):
        is_train = index < 10
        fail = index < 6 if is_train else index in (10, 11, 12)
        scores = {name: 1.0 for name in dims}
        if fail:
            scores["image_quality"] = 0.25 if image_fails else 0.0
        rows.append({
            "case_id": f"reviewed-{index}",
            "category": names[index % 5],
            "set": "train" if is_train else "holdout",
            "human_review": "fail" if fail else "pass",
            "failure_reasons": ["image_offtopic"] if fail else [],
            "dimensions": scores,
        })
    return rows


class EvaluationEvolutionTests(unittest.TestCase):
    def test_new_proposal_supported_only_by_holdout(self):
        result = evolution.propose(feedback())
        self.assertEqual(result["decision"], "HOLDOUT_SUPPORTED_PROPOSAL")
        self.assertEqual(result["train_count"], 10)
        self.assertEqual(result["holdout_count"], 5)
        self.assertLess(result["after"]["false_positive"],
                        result["before"]["false_positive"])
        proposed = result["modified_rubric"]
        self.assertEqual(sum(proposed["weights"].values()), 100)
        self.assertGreater(proposed["weights"]["image_quality"],
                           quality.policy("v2")["weights"]["image_quality"])
        self.assertGreaterEqual(proposed["weights"]["source_integrity"], 25)
        self.assertEqual(proposed["status"], "NOT_ACTIVE_HUMAN_APPROVAL_REQUIRED")

    def test_rejected_when_holdout_not_improved(self):
        result = evolution.propose(feedback(image_fails=False))
        self.assertEqual(result["decision"], "REJECTED_HOLDOUT")
        self.assertIsNone(result["modified_rubric"])

    def test_no_upgrade_without_diverse_human_evidence(self):
        with self.assertRaises(evolution.EvolutionError):
            evolution.propose(feedback()[:14])
        data = feedback()
        data[4]["case_id"] = data[3]["case_id"]
        with self.assertRaises(evolution.EvolutionError):
            evolution.propose(data)
        data = feedback()
        data[0]["dimensions"]["image_quality"] = -1
        with self.assertRaises(evolution.EvolutionError):
            evolution.propose(data)

    def test_proposal_does_not_change_frozen_policies(self):
        original = quality.policy("v2")["weights"].copy()
        evolution.propose(feedback())
        self.assertEqual(quality.policy("v2")["weights"], original)


if __name__ == "__main__":
    unittest.main()
