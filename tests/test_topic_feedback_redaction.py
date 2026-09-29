from __future__ import annotations

import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools.contract_types import JSONMap
from tools.topic_feedback_redaction import (
    MAX_DEMOGRAPHIC_COUNT,
    ForbiddenFeedbackFieldError,
    InvalidDemographicCountError,
    sanitize_feedback_payload,
)


@pytest.mark.parametrize(
    ("secret_key", "canonical_key"),
    (
        ("authorization", "authorization"),
        ("X-Naver-Client-Secret", "xnaverclientsecret"),
        ("Ｃｏｏｋｉｅ", "cookie"),
        ("nested_session_token", "nestedsessiontoken"),
        ("user-profile-path", "userprofilepath"),
    ),
)
def test_rejects_secret_like_keys_at_every_nested_depth(
    secret_key: str, canonical_key: str
) -> None:
    # Given: a secret-like key nested in an otherwise valid feedback payload.
    payload: JSONMap = {
        "derived": [{"nested": {secret_key: "synthetic-opaque-value"}}]
    }

    # When/Then: the boundary fails closed without exposing the raw secret value.
    with pytest.raises(ForbiddenFeedbackFieldError) as captured:
        _ = sanitize_feedback_payload(payload)
    assert "synthetic-opaque-value" not in str(captured.value)
    assert captured.value.field_names == (canonical_key,)


@pytest.mark.parametrize(
    ("secret_key", "canonical_key"),
    (
        ("nidAut", "nidaut"),
        ("nid_ses", "nidses"),
        ("nid-aut", "nidaut"),
        ("NID AUT", "nidaut"),
        ("ＮＩＤ＿ＳＥＳ", "nidses"),
    ),
)
def test_rejects_nested_naver_session_cookie_key_variants(
    secret_key: str, canonical_key: str
) -> None:
    # Given: a Naver session-cookie identifier disguised by case or punctuation.
    payload: JSONMap = {"outer": [{"inner": {secret_key: "synthetic-opaque-value"}}]}

    # When/Then: the canonical secret policy fails closed without echoing its value.
    with pytest.raises(ForbiddenFeedbackFieldError) as captured:
        _ = sanitize_feedback_payload(payload)
    assert "synthetic-opaque-value" not in str(captured.value)
    assert captured.value.field_names == (canonical_key,)


@given(
    st.sampled_from(("nid", "NID", "ＮＩＤ")),
    st.sampled_from(("", "_", "-", " ", "＿", "\u200b")),
    st.sampled_from((("aut", "nidaut"), ("ses", "nidses"), ("ＡＵＴ", "nidaut"))),
)
def test_hypothesis_naver_session_cookie_variants_fail_closed(
    prefix: str, separator: str, suffix_pair: tuple[str, str]
) -> None:
    # Given: generated Unicode, case, and punctuation variants of NID session keys.
    suffix, canonical_key = suffix_pair
    payload: JSONMap = {"nested": {f"{prefix}{separator}{suffix}": "synthetic-value"}}

    # When/Then: all forms normalize to and match the same secret policy identifier.
    with pytest.raises(ForbiddenFeedbackFieldError) as captured:
        _ = sanitize_feedback_payload(payload)
    assert captured.value.field_names == (canonical_key,)


@pytest.mark.parametrize(
    "ordinary_key",
    (
        "api_keynote",
        "cookie_cutter",
        "profile_pathway",
        "session_count",
        "token_count",
        "visitor_count",
    ),
)
def test_allows_ordinary_keys_with_incidental_secret_policy_substrings(
    ordinary_key: str,
) -> None:
    # Given: an ordinary metric key that contains but is not a secret-policy identifier.
    payload: JSONMap = {ordinary_key: "synthetic-safe-value"}

    # When: it crosses the secret boundary.
    result = sanitize_feedback_payload(payload)

    # Then: the value remains intact because the policy token is not a key identifier.
    assert result.payload == payload


def test_removes_sparse_demographics_and_keeps_non_sensitive_audit_only() -> None:
    # Given: qualified, sparse, zero, blank, and absent demographic counts.
    payload: JSONMap = {
        "demographic_buckets": [
            {"label": "eligible-synthetic", "count": 5},
            {"label": "sparse-synthetic", "count": 4},
            {"label": "zero-is-not-missing", "count": 0},
            {"label": "blank-is-missing", "count": ""},
            {"label": "absent-is-missing"},
        ]
    }

    # When: the payload crosses the redaction boundary.
    result = sanitize_feedback_payload(payload)

    # Then: only the qualifying aggregate remains and the audit carries no raw labels or counts.
    assert result.payload == {
        "demographic_buckets": [{"label": "eligible-synthetic", "count": 5}]
    }
    assert result.audit.removed_field_names == ("demographicbuckets",)
    assert result.audit.removed_sparse_bucket_count == 2
    assert result.audit.removed_missing_bucket_count == 2
    assert "sparse-synthetic" not in repr(result.audit)
    assert "zero-is-not-missing" not in repr(result.audit)


@given(st.integers(min_value=-100, max_value=MAX_DEMOGRAPHIC_COUNT + 100))
def test_demographic_numeric_boundaries_are_never_coerced(count: int) -> None:
    # Given: an integer boundary supplied as a demographic count.
    payload: JSONMap = {"demographics": [{"count": count}]}

    # When/Then: negative and too-large values fail, sparse values drop, others stay numeric.
    if count < 0 or count > MAX_DEMOGRAPHIC_COUNT:
        with pytest.raises(InvalidDemographicCountError):
            _ = sanitize_feedback_payload(payload)
    else:
        result = sanitize_feedback_payload(payload)
        expected = [] if count < 5 else [{"count": count}]
        assert result.payload == {"demographics": expected}


@given(st.booleans())
def test_bool_is_not_accepted_as_a_demographic_integer(count: bool) -> None:
    # Given: Python's bool subclass of int in a count position.
    payload: JSONMap = {"age_buckets": [{"count": count}]}

    # When/Then: it is rejected rather than treated as one.
    with pytest.raises(InvalidDemographicCountError, match="integer"):
        _ = sanitize_feedback_payload(payload)


SAFE_KEYS = st.text(
    alphabet="xyz012",
    min_size=1,
    max_size=12,
)
SAFE_VALUES = st.one_of(st.none(), st.booleans(), st.integers(-10, 10), st.text(max_size=8))


@given(st.dictionaries(SAFE_KEYS, SAFE_VALUES, min_size=1, max_size=8))
def test_key_order_does_not_change_a_safe_payload(keyed_payload: dict[str, None | bool | int | str]) -> None:
    # Given: a safe payload and the same key/value pairs in reverse insertion order.
    payload: JSONMap = {}
    reversed_payload: JSONMap = {}
    for key, value in keyed_payload.items():
        payload[key] = value
    for key, value in reversed(tuple(keyed_payload.items())):
        reversed_payload[key] = value

    # When: both payloads cross the same boundary.
    first = sanitize_feedback_payload(payload)
    second = sanitize_feedback_payload(reversed_payload)

    # Then: their sanitized content and audit are equal regardless of input order.
    assert first == second


@given(st.sampled_from(("Ａｕｔｈｏｒｉｚａｔｉｏｎ", "Ｃｏｏｋｉｅ", "Ｓｅｓｓｉｏｎ＿Ｔｏｋｅｎ")))
def test_unicode_nfkc_secret_keys_fail_closed(secret_key: str) -> None:
    # Given: a Unicode compatibility form of a forbidden key.
    payload: JSONMap = {"nested": {secret_key: "synthetic-opaque-value"}}

    # When/Then: normalization does not let it bypass the secret boundary.
    with pytest.raises(ForbiddenFeedbackFieldError):
        _ = sanitize_feedback_payload(payload)
    assert unicodedata.normalize("NFKC", secret_key) != ""


@given(st.sampled_from(("missing", "delayed", "duplicate")))
def test_status_missing_delayed_and_duplicate_observations_stay_distinct(status: str) -> None:
    # Given: a non-secret source condition with intentionally duplicate observations.
    payload: JSONMap = {
        "status": status,
        "missing_fields": ["exposure"] if status == "missing" else [],
        "observations": [{"id": "synthetic-dup"}, {"id": "synthetic-dup"}],
    }

    # When: it crosses the redaction boundary.
    result = sanitize_feedback_payload(payload)

    # Then: redaction does not collapse non-sensitive provenance semantics.
    assert result.payload == payload
