from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Protocol

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from tests.weekly_outcome_fixtures import install_factual_evaluation
from tools.contract_types import JSONMap

SCHEMA = Path("schemas/topic-feedback-outcome-evidence.schema.json")


class _ValidatorProtocol(Protocol):
    def validate(self, instance: JSONMap) -> None: ...


def _validator() -> _ValidatorProtocol:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_factual_evaluation_and_every_referenced_record_match_strict_schema(
    tmp_path: Path,
) -> None:
    evaluation_path = install_factual_evaluation(tmp_path)
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    _validator().validate(evaluation)
    for outcome in evaluation["outcomes"]:
        for role in ("selection_ref", "seven_day_ref", "twenty_eight_day_ref"):
            path = tmp_path / outcome[role]["path"]
            _validator().validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("mutation", ["extra", "wrong_reference_schema"])
def test_factual_evidence_schema_rejects_untyped_or_cross_role_data(
    tmp_path: Path, mutation: str
) -> None:
    evaluation_path = install_factual_evaluation(tmp_path, count=1)
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    invalid = deepcopy(evaluation)
    if mutation == "extra":
        invalid["outcomes"][0]["surprise"] = True
    else:
        invalid["outcomes"][0]["selection_ref"]["schema_version"] = (
            "topic-feedback-outcome-observation-v1"
        )
    with pytest.raises(ValidationError):
        _validator().validate(invalid)
