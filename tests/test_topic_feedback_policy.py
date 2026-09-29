from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.contract_types import JSONValue
from tools.topic_feedback_policy import SourcePolicyError, load_registry
from tools.topic_feedback_source_rules import AccessMode, SourceConfidence

CONFIG_PATH = Path("config/topic-feedback-sources.json")


def test_registry_represents_the_nine_named_tools_and_disabled_placeholder() -> None:
    # Given: the checked-in conservative source registry.
    registry = load_registry(CONFIG_PATH)

    # When: the source identities and default decisions are read.
    sources = {source.source_id: source for source in registry.sources}

    # Then: exactly the nine named tools and one non-product placeholder exist.
    assert len(sources) == 10
    assert set(sources) == {
        "naver-datalab",
        "naver-search-ads-keyword-tool",
        "blackkiwi",
        "ecommerce-ai-extension",
        "datalab-tools-helper",
        "n-supporter",
        "daglo",
        "chatgpt-codex-structuring",
        "naver-blog-statistics",
        "missing-tenth-tool",
    }
    assert sources["naver-datalab"].access_mode is AccessMode.OFFICIAL_API
    assert sources["naver-blog-statistics"].access_mode is AccessMode.MANUAL_IMPORT
    assert sources["ecommerce-ai-extension"].access_mode is AccessMode.DISABLED
    assert sources["n-supporter"].access_mode is AccessMode.DISABLED
    assert sources["missing-tenth-tool"].product_identified is False
    assert registry.candidate_source == "creator_advisor_only"
    assert registry.kpi_v1 == "search_inflow"
    assert registry.terms_url == "https://policy.naver.com/rules/service.html"
    assert registry.terms_checked_at == "2026-09-08"
    assert registry.terms_effective_date == "2025-07-10"


def test_registry_uses_closed_confidence_and_access_mode_sets() -> None:
    # Given: the source-policy closed sets.
    access_modes = {mode.value for mode in AccessMode}
    confidence_classes = {confidence.value for confidence in SourceConfidence}

    # When: their serializable values are inspected.
    # Then: config cannot invent a connection or trust class.
    assert access_modes == {
        "official_api",
        "manual_import",
        "authenticated_read_only",
        "disabled",
    }
    assert confidence_classes == {"A", "B", "C", "D"}


def test_registry_rejects_secret_like_key_or_value(tmp_path: Path) -> None:
    # Given: an otherwise valid registry whose disabled placeholder carries a secret.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources_value = raw_value["sources"]
    assert isinstance(sources_value, list)
    placeholder = next(
        source
        for source in sources_value
        if isinstance(source, dict) and source.get("id") == "missing-tenth-tool"
    )
    placeholder["client_secret"] = "not-a-real-secret"
    path = tmp_path / "secret.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: config parsing fails before records become trusted policy values.
    with pytest.raises(SourcePolicyError, match="secret-like"):
        _ = load_registry(path)


def test_registry_rejects_enabled_extension_without_identified_product_and_terms(
    tmp_path: Path,
) -> None:
    # Given: an extension source is force-enabled without an identified product or review.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources_value = raw_value["sources"]
    assert isinstance(sources_value, list)
    extension = next(
        source
        for source in sources_value
        if isinstance(source, dict) and source.get("id") == "ecommerce-ai-extension"
    )
    extension["enabled"] = True
    extension["official_url"] = None
    extension["terms_checked_at"] = None
    path = tmp_path / "extension.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: activation fails closed with the safe public error.
    with pytest.raises(
        SourcePolicyError,
        match="^source activation requires identified product and reviewed terms$",
    ):
        _ = load_registry(path)


@pytest.mark.parametrize("section", ["registry", "terms_policy"])
def test_registry_rejects_unknown_top_level_and_terms_policy_fields(
    tmp_path: Path, section: str
) -> None:
    # Given: an otherwise valid registry gains a field outside its closed contract.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    target = raw_value if section == "registry" else raw_value["terms_policy"]
    assert isinstance(target, dict)
    target["unrecognized_field"] = "blocked"
    path = tmp_path / f"{section}-unknown.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: parsing stops before configuration becomes policy.
    with pytest.raises(SourcePolicyError):
        _ = load_registry(path)


def test_registry_rejects_source_role_and_default_mutations(tmp_path: Path) -> None:
    # Given: DataLab is changed from its trusted non-candidate default role.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources_value = raw_value["sources"]
    assert isinstance(sources_value, list)
    datalab = next(
        source
        for source in sources_value
        if isinstance(source, dict) and source.get("id") == "naver-datalab"
    )
    datalab["automatic_candidate_provider"] = True
    path = tmp_path / "datalab-role.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: the trusted per-ID policy rejects the drift.
    with pytest.raises(SourcePolicyError, match="source policy"):
        _ = load_registry(path)


def test_registry_rejects_source_specific_purpose_mutation(tmp_path: Path) -> None:
    # Given: DataLab is changed from a same-candidate signal to expansion input.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources_value = raw_value["sources"]
    assert isinstance(sources_value, list)
    datalab = next(
        source
        for source in sources_value
        if isinstance(source, dict) and source.get("id") == "naver-datalab"
    )
    datalab["allowed_purposes"] = ["candidate_expansion"]
    path = tmp_path / "datalab-purpose.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: the trusted per-ID policy refuses role drift.
    with pytest.raises(SourcePolicyError, match="source policy"):
        _ = load_registry(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_name", "NAVER DataLab Mirror"),
        ("official_url", "https://evil.invalid/datalab"),
        ("terms_checked_at", "2026-09-07"),
    ],
)
def test_registry_rejects_untrusted_source_identity_mutation(
    tmp_path: Path, field: str, value: str
) -> None:
    # Given: DataLab has a plausible but untrusted identity field change.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources_value = raw_value["sources"]
    assert isinstance(sources_value, list)
    datalab = next(
        source
        for source in sources_value
        if isinstance(source, dict) and source.get("id") == "naver-datalab"
    )
    datalab[field] = value
    path = tmp_path / "untrusted-source-identity.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: untrusted config cannot redefine the known source identity.
    with pytest.raises(SourcePolicyError, match="source identity") as error:
        _ = load_registry(path)
    assert value not in str(error.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("official_url", "https://policy.naver.com/rules/service.html?copy=1"),
        ("terms_checked_at", "2026-09-07"),
        ("effective_date", "2024-03-29"),
    ],
)
def test_registry_rejects_untrusted_current_terms_identity_mutation(
    tmp_path: Path, field: str, value: str
) -> None:
    # Given: the current official terms identity is changed in local config.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    terms_policy = raw_value["terms_policy"]
    assert isinstance(terms_policy, dict)
    terms_policy[field] = value
    path = tmp_path / "untrusted-terms-identity.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: self-asserted terms changes cannot redefine the trusted snapshot.
    with pytest.raises(SourcePolicyError, match="terms policy") as error:
        _ = load_registry(path)
    assert value not in str(error.value)


def test_registry_rejects_duplicate_display_name(tmp_path: Path) -> None:
    # Given: two otherwise valid sources claim the same display identity.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources_value = raw_value["sources"]
    assert isinstance(sources_value, list)
    first_source = sources_value[0]
    second_source = sources_value[1]
    assert isinstance(first_source, dict)
    assert isinstance(second_source, dict)
    display_name = first_source["display_name"]
    assert isinstance(display_name, str)
    second_source["display_name"] = display_name
    path = tmp_path / "duplicate-display-name.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: the registry remains closed to display-name aliasing.
    with pytest.raises(SourcePolicyError, match="source identity"):
        _ = load_registry(path)


def test_registry_rejects_missing_tenth_placeholder_product_drift(tmp_path: Path) -> None:
    # Given: the explicit unknown-product placeholder is marked as identified.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    placeholder = raw_value["missing_tenth_tool"]
    assert isinstance(placeholder, dict)
    placeholder["identified_product"] = True
    path = tmp_path / "placeholder-product.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: the absent tenth product cannot become a known source.
    with pytest.raises(SourcePolicyError, match="placeholder"):
        _ = load_registry(path)


def test_registry_rejects_search_ads_enable_without_opt_in_license_proof(
    tmp_path: Path,
) -> None:
    # Given: Search Ads is enabled without the required reviewed opt-in proof.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources_value = raw_value["sources"]
    assert isinstance(sources_value, list)
    search_ads = next(
        source
        for source in sources_value
        if isinstance(source, dict)
        and source.get("id") == "naver-search-ads-keyword-tool"
    )
    search_ads["enabled"] = True
    search_ads["activation_proof"] = "reviewed_opt_in_license"
    path = tmp_path / "search-ads-enable.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: activation is denied before a provider can run.
    with pytest.raises(
        SourcePolicyError,
        match="^source activation requires identified product and reviewed terms$",
    ):
        _ = load_registry(path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("profile-dir", "~/Library/Application Support/Google/Chrome/Profile 1"),
        ("browser_path", "/Users/person/Library/Application Support/Google/Chrome"),
        ("nested", "~/Library/Application Support/Google/Chrome/Profile 1"),
        (
            "nested",
            r"C:\\Users\\person\\AppData\\Local\\Google\\Chrome\\User Data\\Profile 1",
        ),
        ("nested", "/home/person/.config/google-chrome/Profile 1"),
        ("nested", "Ｃｈｒｏｍｅ Ｐｒｏｆｉｌｅ １"),
        ("nested", "/var/lib/browser/.mozilla/firefox/abcd.default-release"),
        ("nested", "Mozilla Profile 2"),
        ("nested", "--user-data-dir=/var/tmp/browser"),
    ],
)
def test_registry_rejects_browser_profile_keys_and_values_without_echoing_them(
    tmp_path: Path, key: str, value: str
) -> None:
    # Given: a nested browser profile key or platform profile location is supplied.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    terms_policy = raw_value["terms_policy"]
    assert isinstance(terms_policy, dict)
    if key == "nested":
        terms_policy["conclusion"] = value
    else:
        terms_policy[key] = value
    path = tmp_path / "profile.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When: the untrusted config boundary is parsed.
    with pytest.raises(SourcePolicyError, match="secret-like") as error:
        _ = load_registry(path)

    # Then: it is rejected without reflecting the sensitive path.
    assert value not in str(error.value)


def test_registry_allows_ordinary_profile_prose(tmp_path: Path) -> None:
    # Given: a non-path policy conclusion uses the ordinary word profile.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    terms_policy = raw_value["terms_policy"]
    assert isinstance(terms_policy, dict)
    terms_policy["conclusion"] = "Read the public profile overview before import."
    path = tmp_path / "ordinary-prose.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: normal prose remains valid rather than being treated as a path.
    assert load_registry(path).candidate_source == "creator_advisor_only"


def test_registry_rejects_enabled_unknown_source_before_any_adapter_can_run(
    tmp_path: Path,
) -> None:
    # Given: an unknown product is inserted as an enabled source.
    raw_value: JSONValue = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw_value, dict)
    sources_value = raw_value["sources"]
    assert isinstance(sources_value, list)
    sources_value.append(
        {
            "id": "unverified-extension",
            "display_name": "Unverified extension",
            "product_identified": False,
            "official_url": None,
            "terms_checked_at": None,
            "confidence": "D",
                "access_mode": "disabled",
                "enabled": True,
                "activation_proof": None,
            "automatic_candidate_provider": False,
            "manual_import_allowed": False,
            "external_data_source": True,
            "allowed_purposes": ["shadow_only"],
            "limitations": ["unverified"],
        }
    )
    path = tmp_path / "unknown.json"
    _ = path.write_text(json.dumps(raw_value), encoding="utf-8")

    # When/Then: no unknown enabled source can reach an adapter.
    with pytest.raises(
        SourcePolicyError,
        match="^source activation requires identified product and reviewed terms$",
    ):
        _ = load_registry(path)
