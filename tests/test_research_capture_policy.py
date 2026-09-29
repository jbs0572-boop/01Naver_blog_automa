from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.aside_browser import AsideCliConfig
from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.research_browser_capture import capture_research_browser
from tools.research_capture_policy import ResearchCapturePolicy, load_policy
from tools.research_capture_store import (
    append_capture,
    load_ledger,
    reserve_capture_attempt,
)
from tools.research_crawler_bridge import SourceProfile


def test_policy_loads_bounded_public_capture_defaults() -> None:
    policy = load_policy()
    assert policy.max_searches == 3
    assert policy.max_documents == 5
    assert policy.max_social_sources == 1
    assert policy.host_budget_seconds == 300
    assert policy.same_url_retry_limit == 1
    assert policy.read_timeout_seconds == 30


def test_capture_ledger_deduplicates_retry(tmp_path: Path) -> None:
    capture: JSONMap = {"capture_id": "RAW-1", "keyword": "카페", "observations": []}
    _ = append_capture(tmp_path, "run-1", capture)
    _ = append_capture(tmp_path, "run-1", capture)
    ledger = load_ledger(tmp_path, "run-1")
    assert len(ledger.entries) == 1
    assert json.loads((tmp_path / "metadata/research-capture/run-1.json").read_text())["run_id"] == "run-1"


def test_capture_reservation_rejects_a_malformed_persisted_counter(
    tmp_path: Path,
) -> None:
    binding: JSONMap = {"keyword": "카페"}
    malformed: JSONMap = {
        "capture_id": "ATTEMPT-1",
        "capture_state": "reserved",
        "capture_binding": binding,
        "attempt_counters": {"searches": "one"},
    }
    _ = append_capture(tmp_path, "run-1", malformed)

    with pytest.raises(ContractError, match="reservation is malformed"):
        _ = reserve_capture_attempt(tmp_path, "run-1", binding, 3)


def test_capture_retries_one_transient_original_with_the_configured_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an allowlisted original whose first open fails transiently.
    traces: list[JSONMap] = []

    class NodeAsideSession:
        def __init__(self, _config: AsideCliConfig) -> None:
            pass

        def run_json(self, operation: str) -> JSONMap:
            payload: JSONMap = {
                "operation": operation,
                "hrefs": ["https://official.example/original"],
                "outcomes": {
                    "https://official.example/original": ["transient", "success"]
                },
            }
            harness_prefix = """
const input = JSON.parse(process.argv.at(-1));
const attempts = [];
const timeouts = [];
let result = null;
let executionError = null;
let latestTimer = null;
globalThis.setTimeout = (handler, milliseconds) => {
  timeouts.push(milliseconds);
  latestTimer = handler;
  return timeouts.length;
};
globalThis.clearTimeout = () => {};
class Tab {
  constructor(url) { this.currentUrl = url; }
  url() { return this.currentUrl; }
  locator() { return { evaluateAll: async () => input.hrefs }; }
}
globalThis.listBrowserTabs = async () => [];
globalThis.getTabByTargetId = () => null;
globalThis.attachBrowserTab = async () => { throw new Error('unused'); };
globalThis.openTab = async (url) => {
  if (url.startsWith('https://search.naver.com/')) return new Tab(url);
  attempts.push(url);
  const outcome = (input.outcomes[url] ?? []).shift() ?? 'transient';
  if (outcome === 'transient') {
    const error = new Error('temporary network failure');
    error.name = 'NetworkError';
    throw error;
  }
  if (outcome === 'hang') {
    queueMicrotask(() => latestTimer?.());
    return new Promise(() => {});
  }
  return new Tab(url);
};
globalThis.snapshot = async (tab) => ({ tree: tab.url().startsWith('https://search.naver.com/') ? 'search' : 'original' });
globalThis.closeTab = async () => {};
console.log = (value) => {
  if (typeof value === 'string' && value.startsWith('__ASIDE_RESULT__')) {
    result = JSON.parse(value.slice('__ASIDE_RESULT__'.length));
  }
};
try {
  await (async () => {
"""
            harness_suffix = """
})();
} catch (error) {
  executionError = String(error?.message ?? error);
}
process.stdout.write(JSON.stringify({ attempts, timeouts, result, executionError }));
"""
            harness = harness_prefix + operation + harness_suffix
            completed = subprocess.run(
                ("node", "--input-type=module", "--eval", harness, json.dumps(payload)),
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
            trace_value: JSONValue = json.loads(completed.stdout)
            assert isinstance(trace_value, dict)
            traces.append(trace_value)
            if trace_value["executionError"] is not None:
                raise AssertionError(str(trace_value["executionError"]))
            result = trace_value["result"]
            assert isinstance(result, dict)
            return result

        def close(self) -> None:
            pass

    def profiles() -> tuple[SourceProfile, ...]:
        return (
            SourceProfile(
                "official-example", "official", "official.example", "aside", False
            ),
        )

    monkeypatch.setattr("tools.research_browser_capture.AsideReplSession", NodeAsideSession)
    monkeypatch.setattr("tools.research_browser_capture.load_source_profiles", profiles)
    monkeypatch.setattr("tools.research_browser_capture.resolve_aside_cli", lambda: Path("aside"))

    # When: the real generated Aside operation captures the original.
    observed = capture_research_browser("policy-bound-retry-keyword")

    # Then: it reads once successfully after exactly one transient retry at 30 seconds.
    documents = observed["document_observations"]
    assert isinstance(documents, list)
    assert len(documents) == 1
    assert traces == [
        {
            "attempts": [
                "https://official.example/original",
                "https://official.example/original",
            ],
            "timeouts": [30000, 30000, 30000],
            "result": observed,
            "executionError": None,
        }
    ]


def _capture_with_recording_adapter(
    monkeypatch: pytest.MonkeyPatch,
    hrefs: tuple[str, ...],
    profiles: tuple[SourceProfile, ...],
) -> tuple[JSONMap, JSONMap]:
    traces: list[JSONMap] = []

    class RecordingAsideSession:
        def __init__(self, _config: AsideCliConfig) -> None:
            pass

        def run_json(self, operation: str) -> JSONMap:
            payload: JSONMap = {"hrefs": list(hrefs)}
            harness = """
const input = JSON.parse(process.argv.at(-1));
const opens = [];
const completions = [];
let result = null;
let executionError = null;
class Tab {
  constructor(url) { this.currentUrl = url; }
  url() { return this.currentUrl; }
  locator() { return { evaluateAll: async () => input.hrefs }; }
}
globalThis.listBrowserTabs = async () => [];
globalThis.getTabByTargetId = () => null;
globalThis.attachBrowserTab = async () => { throw new Error('unused'); };
globalThis.openTab = async (url) => {
  if (url.startsWith('https://search.naver.com/')) return new Tab(url);
  opens.push(url);
  return new Tab(url);
};
globalThis.snapshot = async (tab) => {
  if (!tab.url().startsWith('https://search.naver.com/')) completions.push(tab.url());
  return { tree: tab.url().startsWith('https://search.naver.com/') ? 'search' : `original:${tab.url()}` };
};
globalThis.closeTab = async () => {};
console.log = (value) => {
  if (typeof value === 'string' && value.startsWith('__ASIDE_RESULT__')) {
    result = JSON.parse(value.slice('__ASIDE_RESULT__'.length));
  }
};
try {
  await (async () => {
""" + operation + """
})();
} catch (error) {
  executionError = String(error?.message ?? error);
}
process.stdout.write(JSON.stringify({ opens, completions, result, executionError }));
"""
            completed = subprocess.run(
                ("node", "--input-type=module", "--eval", harness, json.dumps(payload)),
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
            trace_value: JSONValue = json.loads(completed.stdout)
            assert isinstance(trace_value, dict)
            traces.append(trace_value)
            assert trace_value["executionError"] is None
            result = trace_value["result"]
            assert isinstance(result, dict)
            return result

        def close(self) -> None:
            pass

    monkeypatch.setattr("tools.research_browser_capture.AsideReplSession", RecordingAsideSession)
    monkeypatch.setattr(
        "tools.research_browser_capture.load_policy",
        lambda: ResearchCapturePolicy(default_documents=3),
    )
    monkeypatch.setattr("tools.research_browser_capture.load_source_profiles", lambda: profiles)
    monkeypatch.setattr(
        "tools.research_browser_capture.resolve_aside_cli", lambda: Path("aside")
    )

    result = capture_research_browser("source-profile-fixture")

    assert traces
    return result, traces[0]


def test_capture_binds_each_observed_original_to_its_source_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one official original followed by one supporting/visual original.
    official = "https://official.example/announcement"
    supporting = "https://supporting.example/visual"
    profiles = (
        SourceProfile("official-source", "official", "official.example", "aside", False),
        SourceProfile("supporting-source", "supporting", "supporting.example", "aside", False),
    )

    # When: the public browser capture emits the document observations.
    result, trace = _capture_with_recording_adapter(
        monkeypatch, (official, supporting), profiles
    )

    # Then: the observed source bindings preserve each profile and URL.
    documents = result["document_observations"]
    assert isinstance(documents, list)
    assert trace["opens"] == [official, supporting]
    assert [
        (
            document["source_kind"],
            document["source_profile_id"],
            document["requested_url"],
            document["source_url"],
        )
        for document in documents
        if isinstance(document, dict)
    ] == [
        ("official_document", "official-source", official, official),
        ("supporting_document", "supporting-source", supporting, supporting),
    ]


def test_capture_completes_official_lane_before_supporting_visual_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the DOM reports supporting links around two official originals.
    supporting_first = "https://supporting.example/first-visual"
    official_first = "https://official.example/first-announcement"
    supporting_second = "https://supporting.example/second-visual"
    official_second = "https://official.example/second-announcement"
    profiles = (
        SourceProfile("supporting-source", "supporting", "supporting.example", "aside", False),
        SourceProfile("official-source", "official", "official.example", "aside", False),
    )

    # When: the public capture reads its three-document budget through the adapter.
    result, trace = _capture_with_recording_adapter(
        monkeypatch,
        (supporting_first, official_first, supporting_second, official_second),
        profiles,
    )

    # Then: the official lane has completed before the first supporting/visual read.
    documents = result["document_observations"]
    assert isinstance(documents, list)
    assert trace["opens"] == [official_first, official_second, supporting_first]
    assert trace["completions"] == [official_first, official_second, supporting_first]
    assert [
        (document["source_kind"], document["source_profile_id"], document["source_url"])
        for document in documents
        if isinstance(document, dict)
    ] == [
        ("official_document", "official-source", official_first),
        ("official_document", "official-source", official_second),
        ("supporting_document", "supporting-source", supporting_first),
    ]
