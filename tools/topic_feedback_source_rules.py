from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from tools.contract_types import JSONMap

EXPECTED_SOURCE_IDS: Final = frozenset(
    {
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
)
REGISTRY_FIELDS: Final = frozenset(
    {
        "schema_version",
        "scope",
        "candidate_source",
        "kpi_v1",
        "missing_tenth_tool",
        "terms_policy",
        "sources",
    }
)
TERMS_POLICY_FIELDS: Final = frozenset(
    {"official_url", "terms_checked_at", "effective_date", "conclusion"}
)
MISSING_TENTH_FIELDS: Final = frozenset({"id", "status", "identified_product"})
SOURCE_FIELDS: Final = frozenset(
    {
        "id",
        "display_name",
        "product_identified",
        "official_url",
        "terms_checked_at",
        "confidence",
        "access_mode",
        "enabled",
        "activation_proof",
        "automatic_candidate_provider",
        "manual_import_allowed",
        "external_data_source",
        "allowed_purposes",
        "limitations",
    }
)
class AccessMode(StrEnum):
    OFFICIAL_API = "official_api"
    MANUAL_IMPORT = "manual_import"
    AUTHENTICATED_READ_ONLY = "authenticated_read_only"
    DISABLED = "disabled"

class SourceConfidence(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"

@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    display_name: str
    product_identified: bool
    official_url: str | None
    terms_checked_at: str | None
    confidence: SourceConfidence
    access_mode: AccessMode
    enabled: bool
    activation_proof: str | None
    automatic_candidate_provider: bool
    manual_import_allowed: bool
    external_data_source: bool
    allowed_purposes: tuple[str, ...]
    limitations: tuple[str, ...]

    def as_json(self) -> JSONMap:
        return {
            "id": self.source_id,
            "display_name": self.display_name,
            "product_identified": self.product_identified,
            "official_url": self.official_url,
            "terms_checked_at": self.terms_checked_at,
            "confidence": self.confidence.value,
            "access_mode": self.access_mode.value,
            "enabled": self.enabled,
            "activation_proof": self.activation_proof,
            "automatic_candidate_provider": self.automatic_candidate_provider,
            "manual_import_allowed": self.manual_import_allowed,
            "external_data_source": self.external_data_source,
            "allowed_purposes": list(self.allowed_purposes),
            "limitations": list(self.limitations),
        }

@dataclass(frozen=True, slots=True)
class SourceRegistry:
    scope: str
    candidate_source: str
    kpi_v1: str
    terms_url: str
    terms_checked_at: str
    terms_effective_date: str
    terms_conclusion: str
    sources: tuple[SourceRecord, ...]

    def as_json(self) -> JSONMap:
        return {
            "schema_version": "topic-feedback-source-registry-v1",
            "scope": self.scope,
            "candidate_source": self.candidate_source,
            "kpi_v1": self.kpi_v1,
            "terms_policy": {
                "official_url": self.terms_url,
                "terms_checked_at": self.terms_checked_at,
                "effective_date": self.terms_effective_date,
                "conclusion": self.terms_conclusion,
            },
            "sources": [source.as_json() for source in self.sources],
        }

@dataclass(frozen=True, slots=True)
class SourceRule:
    product_identified: bool
    confidence: str
    access_mode: str
    default_enabled: bool
    manual_import_allowed: bool
    external_data_source: bool
    allowed_purposes: tuple[str, ...]
    limitations: tuple[str, ...]

_RULES: Final = {
    "naver-datalab": SourceRule(
        True, "A", "official_api", True, False, True,
        ("same_candidate_signal",), ("relative_index_only", "no_candidate_expansion"),
    ),
    "naver-search-ads-keyword-tool": SourceRule(
        True, "A", "official_api", False, True, True,
        ("same_candidate_signal",),
        ("opt_in_only", "license_required", "no_candidate_expansion"),
    ),
    "blackkiwi": SourceRule(
        True, "B", "manual_import", False, True, True,
        ("quarantined_shadow_signal",),
        ("terms_review_required", "no_automatic_collection", "no_candidate_expansion"),
    ),
    "ecommerce-ai-extension": SourceRule(
        False, "C", "disabled", False, False, True,
        ("disabled_pending_identification",),
        ("exact_product_unverified", "terms_review_required", "no_extension_access"),
    ),
    "datalab-tools-helper": SourceRule(
        True, "C", "disabled", False, False, False,
        ("writing_aid_only",),
        ("not_a_data_source", "no_candidate_expansion", "no_extension_access"),
    ),
    "n-supporter": SourceRule(
        True, "C", "disabled", False, False, True,
        ("disabled_pending_terms",),
        ("terms_review_required", "no_extension_access", "no_candidate_expansion"),
    ),
    "daglo": SourceRule(
        True, "B", "manual_import", False, True, True,
        ("researcher_input_only",),
        ("rights_holder_export_only", "no_automatic_upload", "not_a_fact_source"),
    ),
    "chatgpt-codex-structuring": SourceRule(
        True, "C", "disabled", False, False, False,
        ("current_codex_structuring_only",),
        ("not_an_external_data_source", "no_additional_api_call", "source_material_required"),
    ),
    "naver-blog-statistics": SourceRule(
        True, "A", "manual_import", True, True, True,
        ("post_publication_measurement",),
        ("owner_export_only", "no_login_automation", "no_third_party_statistics"),
    ),
    "missing-tenth-tool": SourceRule(
        False, "D", "disabled", False, False, False,
        ("disabled_placeholder",), ("not_an_identified_product",),
    ),
}

def follows_source_rule(raw: JSONMap) -> bool:
    source_id = raw.get("id")
    if not isinstance(source_id, str):
        return False
    rule = _RULES.get(source_id)
    if rule is None or raw.get("automatic_candidate_provider") is not False:
        return False
    matches = (
        raw.get("product_identified") is rule.product_identified
        and raw.get("confidence") == rule.confidence
        and raw.get("access_mode") == rule.access_mode
        and raw.get("manual_import_allowed") is rule.manual_import_allowed
        and raw.get("external_data_source") is rule.external_data_source
        and raw.get("allowed_purposes") == list(rule.allowed_purposes)
        and raw.get("limitations") == list(rule.limitations)
    )
    if not matches:
        return False
    enabled = raw.get("enabled")
    proof = raw.get("activation_proof")
    return enabled is rule.default_enabled and proof is None
