from __future__ import annotations

from dataclasses import dataclass

from tools.contract_types import ContractError, JSONMap


@dataclass(frozen=True, slots=True)
class CandidateFields:
    keyword: str
    rank: int
    category_key: str | None
    category_rank: int | None
    candidate_id: str | None
    keyword_text: str | None
    raw_observation: JSONMap | None


def parse_candidate_fields(raw: JSONMap) -> CandidateFields:
    keyword_text = raw.get("keyword_text")
    keyword = keyword_text if keyword_text is not None else raw.get("keyword")
    rank = raw.get("rank")
    observation = raw.get("raw_observation")
    if not isinstance(keyword, str) or not keyword.strip():
        raise ContractError("Creator Advisor snapshot candidate keyword is invalid")
    if keyword_text is not None and not isinstance(keyword_text, str):
        raise ContractError("Creator Advisor snapshot candidate keyword_text is invalid")
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        raise ContractError("Creator Advisor snapshot candidate rank is invalid")
    if observation is not None and not isinstance(observation, dict):
        raise ContractError("Creator Advisor snapshot candidate raw_observation is invalid")
    category = raw.get("category_key")
    if category is None and isinstance(observation, dict):
        category = observation.get("category_key", observation.get("category"))
    if category is not None and (not isinstance(category, str) or not category.strip()):
        raise ContractError("Creator Advisor snapshot candidate category_key is invalid")
    category_rank = raw.get("category_rank")
    if category_rank is None and category is not None:
        category_rank = rank
    if category_rank is not None and (
        not isinstance(category_rank, int)
        or isinstance(category_rank, bool)
        or category_rank < 1
    ):
        raise ContractError("Creator Advisor snapshot candidate category_rank is invalid")
    candidate_id = raw.get("candidate_id")
    if candidate_id is not None and (
        not isinstance(candidate_id, str) or not candidate_id.strip()
    ):
        raise ContractError("Creator Advisor snapshot candidate candidate_id is invalid")
    return CandidateFields(
        keyword,
        rank,
        category,
        category_rank,
        candidate_id,
        keyword_text,
        observation,
    )


__all__ = ["CandidateFields", "parse_candidate_fields"]
