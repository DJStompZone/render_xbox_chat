from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from renderxboxchat import fetch
from tests.conftest import FakeResponse, SequenceSession


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch.time, "sleep", lambda *_: None)


def test_request_with_backoff_handles_429_then_success():
    responses = [
        FakeResponse(status_code=429, headers={"Retry-After": "0.1"}),
        FakeResponse(status_code=200),
    ]
    session = SequenceSession(responses)
    resp = fetch._request_with_backoff(session, "GET", "https://example.test")
    assert resp.status_code == 200
    assert len(session.calls) == 2


def test_request_with_backoff_raises_after_exhausting_retries():
    responses = [FakeResponse(status_code=503) for _ in range(fetch.MAX_RETRIES + 1)]
    session = SequenceSession(responses)
    with pytest.raises(Exception):
        fetch._request_with_backoff(session, "GET", "https://example.test")


def test_request_with_backoff_raises_runtime_after_429s():
    responses = [FakeResponse(status_code=429, headers={"Retry-After": "not-a-number"}) for _ in range(4)]
    session = SequenceSession(responses)
    with pytest.raises(RuntimeError):
        fetch._request_with_backoff(session, "GET", "https://example.test", max_retries=3)


def test_get_auth_header_returns_explicit_value():
    assert fetch._get_auth_header("token") == "token"


def test_get_auth_header_uses_xbl3auth(monkeypatch: pytest.MonkeyPatch):
    class DummyService:
        def __init__(self, *_, **__):
            pass

        def get_xbl3_token(self) -> str:
            return "auto-token"

    monkeypatch.setattr(fetch, "Xbl3AuthService", DummyService)
    monkeypatch.setattr(fetch, "XblAuthConfig", lambda: object())
    assert fetch._get_auth_header(None) == "auto-token"


def test_get_auth_header_raises_without_dependency(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fetch, "Xbl3AuthService", None)
    monkeypatch.setattr(fetch, "XblAuthConfig", None)
    with pytest.raises(RuntimeError):
        fetch._get_auth_header(None)


def test_sleep_with_jitter(monkeypatch: pytest.MonkeyPatch):
    recorded: list[float] = []
    monkeypatch.setattr(fetch.random, "uniform", lambda *_: 0.0)
    monkeypatch.setattr(fetch.time, "sleep", lambda x: recorded.append(x))
    fetch._sleep_with_jitter(0.5)
    assert recorded and recorded[0] == 0.5


def test_history_url_for_xuid_and_pages_have_messages():
    assert "xuid(123)" in fetch._history_url_for_xuid("123")
    assert fetch._pages_have_messages([{"messages": [1]}]) is True
    assert fetch._pages_have_messages([{"messages": []}]) is False


def test_fetch_conversation_pages_for_xuid_handles_continuation(monkeypatch: pytest.MonkeyPatch):
    pages = [
        FakeResponse(
            status_code=200,
            json_data={"messages": [1], "continuationToken": "next"},
        ),
        FakeResponse(
            status_code=200,
            json_data={"messages": [2]},
        ),
    ]
    session = SequenceSession(pages)
    monkeypatch.setattr(fetch, "_request_with_backoff", lambda *args, **kwargs: session.request(*args[1:], **kwargs))
    result = fetch.fetch_conversation_pages_for_xuid(
        session=session,
        token="token",
        xuid="42",
        max_items=10,
        max_pages=5,
    )
    assert len(result) == 2
    assert result[0]["messages"] == [1]
    assert result[1]["messages"] == [2]


def test_fetch_conversation_pages_handles_non_json(monkeypatch: pytest.MonkeyPatch):
    session = SequenceSession([
        FakeResponse(status_code=200, json_data=None, text="not json"),
    ])
    monkeypatch.setattr(fetch, "_request_with_backoff", lambda *args, **kwargs: session.request(*args[1:], **kwargs))
    result = fetch.fetch_conversation_pages_for_xuid(
        session=session,
        token="token",
        xuid="42",
        max_items=10,
        max_pages=1,
    )
    assert result[0]["_non_json_body"] == "not json"


def test_fetch_conversation_pages_raises_for_http_error(monkeypatch: pytest.MonkeyPatch):
    resp = FakeResponse(status_code=500, text="boom")
    monkeypatch.setattr(fetch, "_request_with_backoff", lambda *args, **kwargs: resp)
    session = SequenceSession([])
    with pytest.raises(fetch.requests.HTTPError):
        fetch.fetch_conversation_pages_for_xuid(session, token="t", xuid="x", max_items=1, max_pages=1)


def test_load_conversations_from_inbox_parses_entries(monkeypatch: pytest.MonkeyPatch):
    inbox_response: dict[str, Any] = {
        "primary": {
            "conversations": [
                {"conversationId": "abc", "participants": [1, 2]},
                {"conversationId": None},
            ]
        }
    }
    monkeypatch.setattr(fetch, "_fetch_inbox", lambda *_, **__: inbox_response)
    metas = fetch._load_conversations_from_inbox(session=None, token="token", max_items=10)
    assert len(metas) == 1
    assert metas[0].conversation_id == "abc"
    assert metas[0].participants == ["1", "2"]


def test_fetch_own_xuid_success_and_missing_profile(monkeypatch: pytest.MonkeyPatch):
    good = FakeResponse(json_data={"profileUsers": [{"id": "123"}]})
    monkeypatch.setattr(fetch.requests, "get", lambda *args, **kwargs: good)
    assert fetch.fetch_own_xuid("token") == "123"

    missing = FakeResponse(json_data={"profileUsers": []})
    monkeypatch.setattr(fetch.requests, "get", lambda *args, **kwargs: missing)
    with pytest.raises(RuntimeError):
        fetch.fetch_own_xuid("token")

    missing_id = FakeResponse(json_data={"profileUsers": [{}]})
    monkeypatch.setattr(fetch.requests, "get", lambda *args, **kwargs: missing_id)
    with pytest.raises(RuntimeError):
        fetch.fetch_own_xuid("token")


def test_fetch_inbox_error_logging(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    resp = FakeResponse(status_code=500, text="boom")

    class FailingSession(SequenceSession):
        def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:  # pragma: no cover - behavior tested via _fetch_inbox
            return resp

    monkeypatch.setattr(fetch, "_request_with_backoff", lambda *args, **kwargs: resp)
    with pytest.raises(fetch.requests.HTTPError):
        fetch._fetch_inbox(FailingSession([]), token="tok", max_items=1)
    out = capsys.readouterr().out
    assert "Error calling inbox endpoint" in out


def test_fetch_all_conversations_writes_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    meta = fetch.ConversationMeta(conversation_id="cid", participants=["user", "self"], raw={})
    monkeypatch.setattr(fetch, "_get_auth_header", lambda header=None: "token")
    monkeypatch.setattr(fetch, "fetch_own_xuid", lambda *_: "self")
    monkeypatch.setattr(fetch, "_load_conversations_from_inbox", lambda **_: [meta])

    def fake_fetch_pages(**_: Any):
        return [{"messages": [1]}]

    monkeypatch.setattr(fetch, "fetch_conversation_pages_for_xuid", fake_fetch_pages)

    results = fetch.fetch_all_conversations(tmp_path, self_xuid="self", max_items_history=10)
    assert results == [("user", tmp_path / "full_conv_user")]
    stored = (tmp_path / "full_conv_user" / "page_000.json").read_text(encoding="utf-8")
    assert "messages" in stored


def test_fetch_all_conversations_handles_missing_participants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    meta = fetch.ConversationMeta(conversation_id="cid", participants=[], raw={})
    monkeypatch.setattr(fetch, "_get_auth_header", lambda header=None: "token")
    monkeypatch.setattr(fetch, "fetch_own_xuid", lambda *_: "self")
    monkeypatch.setattr(fetch, "_load_conversations_from_inbox", lambda **_: [meta])
    monkeypatch.setattr(fetch, "fetch_conversation_pages_for_xuid", lambda **_: [])

    results = fetch.fetch_all_conversations(tmp_path, self_xuid="self", max_items_history=10)
    assert results == []


def test_fetch_all_conversations_handles_404_then_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    meta = fetch.ConversationMeta(conversation_id="cid", participants=["p1", "p2"], raw={})
    monkeypatch.setattr(fetch, "_get_auth_header", lambda header=None: "token")
    monkeypatch.setattr(fetch, "fetch_own_xuid", lambda *_: "self")
    monkeypatch.setattr(fetch, "_load_conversations_from_inbox", lambda **_: [meta])

    class HTTP404(fetch.requests.HTTPError):
        def __init__(self):
            super().__init__(response=FakeResponse(status_code=404))

    def fake_fetch_pages(**kwargs: Any):
        xuid = kwargs.get("xuid")
        if xuid == "p1":
            raise HTTP404()
        return [{"messages": ["ok"]}]

    monkeypatch.setattr(fetch, "fetch_conversation_pages_for_xuid", fake_fetch_pages)

    results = fetch.fetch_all_conversations(tmp_path, self_xuid="self", max_items_history=10)
    assert results == [("p2", tmp_path / "full_conv_p2")]
