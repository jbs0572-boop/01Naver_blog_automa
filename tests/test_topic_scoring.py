from __future__ import annotations

from tools.topic_scoring import TopicObservation, rank_topics


def test_rank_topics_prioritizes_a_recent_surge() -> None:
    observations = (
        TopicObservation("steady", 1, 70.0, (60.0, 65.0, 70.0)),
        TopicObservation("spike", 2, 100.0, (20.0, 25.0, 100.0)),
    )

    ranked = rank_topics(observations)

    assert ranked[0].keyword == "spike"
    assert ranked[0].score > ranked[1].score


def test_rank_topics_uses_rank_only_without_history() -> None:
    observations = (
        TopicObservation("first", 1, None, ()),
        TopicObservation("second", 2, None, ()),
    )

    ranked = rank_topics(observations)

    assert [item.keyword for item in ranked] == ["first", "second"]
    assert ranked[0].stage == "cold-start"


def test_rank_topics_excludes_empty_keyword() -> None:
    ranked = rank_topics((TopicObservation("", 1, 100.0, (100.0,)),))

    assert ranked == ()
