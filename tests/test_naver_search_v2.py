"""NAVER API HUB discovery adapter: offline safety and normalization tests."""
import json
import urllib.error
import pytest
from tools.blog_v2 import ContentError
from tools.naver_search_v2 import (
    MAX_BYTES, NoRedirect, fetch_search, main, normalize_response, search_request,
)


class FakeResponse:
    status = 200
    def __init__(self, data):
        self.data = data
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, maximum):
        return self.data[:maximum]


class FakeOpener:
    def __init__(self, data):
        self.data = data
        self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(self.data)


def api_data():
    return {"items": [
        {"title": "<b>커피</b> 팁", "description": "집에서 <b>원두</b> 선택", "link": "https://blog.naver.com/writer/123", "postdate": "20261021"},
        {"title": "나중에 올린 글", "description": "future", "link": "https://blog.naver.com/writer/456", "postdate": "20261023"},
        {"title": "비공개 링크", "description": "internal", "link": "http://localhost/secret", "postdate": "20261021"},
    ]}


def test_plan_mode_makes_no_network_requests(capsys):
    assert main(["--query", "한글 키워드", "--as-of", "2026-10-22"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["plan_only"] is True and result["external_calls"] == 0
    assert "naverapihub.apigw.ntruss.com" in result["url"]
    assert "%ED%95%9C%EA%B8%80" in result["url"]


@pytest.mark.parametrize("limit", [0, 11, True, -1])
def test_limit_is_hard_bounded(limit):
    with pytest.raises(ContentError):
        search_request("coffee", limit)


def test_explicit_authorization_and_credentials_required():
    with pytest.raises(ContentError, match="explicit"):
        fetch_search("커피", 2, "2026-10-22", network_authorized=False)
    with pytest.raises(ContentError, match="credentials"):
        fetch_search("커피", 2, "2026-10-22", network_authorized=True, env={})


def test_one_request_fixed_host_no_secrets_in_results():
    opener = FakeOpener(json.dumps(api_data()).encode())
    result = fetch_search("커피", 5, "2026-10-22", network_authorized=True,
                          env={"NAVER_API_HUB_CLIENT_ID": "TEST_ID", "NAVER_API_HUB_CLIENT_SECRET": "TEST_SECRET"}, opener=opener)
    assert len(opener.requests) == 1
    request, timeout = opener.requests[0]
    assert timeout == 8
    assert request.full_url.startswith("https://naverapihub.apigw.ntruss.com/search/v1/blog?")
    assert request.get_header("X-ncp-apigw-api-key-id") == "TEST_ID"
    assert request.get_header("X-ncp-apigw-api-key") == "TEST_SECRET"
    assert result["items"][0]["title"] == "커피 팁"
    assert result["items"][0]["snippet"] == "집에서 원두 선택"
    assert result["items"][0]["suitable_for_claims"] is False
    assert result["result_count"] == 1
    assert "TEST_SECRET" not in json.dumps(result)


def test_redirect_handler_refuses_all_redirects():
    with pytest.raises(ContentError, match="redirected"):
        NoRedirect().redirect_request(None, None, 302, "moved", {}, "http://127.0.0.1")


@pytest.mark.parametrize("payload", [[], {}, {"items": "wrong"}])
def test_invalid_response_is_rejected(payload):
    with pytest.raises(ContentError):
        normalize_response(payload, "coffee", "2026-10-22", 5)


def test_invalid_json_and_oversized_responses_are_rejected():
    for data, message in [(b"not json", "invalid JSON"), (b"x" * (MAX_BYTES + 2), "size limit")]:
        with pytest.raises(ContentError, match=message):
            fetch_search("coffee", 1, "2026-10-22", network_authorized=True,
                         env={"NAVER_API_HUB_CLIENT_ID": "id", "NAVER_API_HUB_CLIENT_SECRET": "secret"},
                         opener=FakeOpener(data))


def test_transport_failure_does_not_retry_or_leak_credentials():
    class FailingOpener:
        count = 0
        def open(self, *args, **kwargs):
            self.count += 1
            raise urllib.error.URLError("SECRET_DO_NOT_LOG")
    fake = FailingOpener()
    with pytest.raises(ContentError, match="request failed") as raised:
        fetch_search("coffee", 1, "2026-10-22", network_authorized=True,
                     env={"NAVER_API_HUB_CLIENT_ID": "id", "NAVER_API_HUB_CLIENT_SECRET": "secret"},
                     opener=fake)
    assert fake.count == 1
    assert "SECRET_DO_NOT_LOG" not in str(raised.value)


def test_invalid_search_items_are_skipped_without_creating_facts():
    result = normalize_response({"items": [12, {"title": "no date"},
                      {"title": "bad date", "link": "https://naver.com", "postdate": "20269999"}]},
                     "coffee", "2026-10-22", 5)
    assert result["result_count"] == 0


def test_execute_without_output_path_still_returns_search_candidates(monkeypatch, capsys):
    from tools import naver_search_v2
    expected = normalize_response(api_data(), "커피", "2026-10-22", 5)
    monkeypatch.setattr(naver_search_v2, "fetch_search", lambda *args, **kwargs: expected)
    assert naver_search_v2.main(["--query", "커피", "--as-of", "2026-10-22", "--execute"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["items"][0]["url"] == "https://blog.naver.com/writer/123"
    assert result["items"][0]["suitable_for_claims"] is False
