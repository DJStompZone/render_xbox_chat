from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from renderxboxchat import render_conversation as rc


def test_escape_html_handles_none_and_special_chars():
    assert rc.escape_html(None) == ""
    assert rc.escape_html("<tag>&'") == "&lt;tag&gt;&amp;&#x27;"


def test_load_all_messages_sorts_and_handles_bad_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    good = tmp_path / "page_good.json"
    bad = tmp_path / "page_bad.json"
    good.write_text(
        """{
  \"messages\": [
    {\"timestamp\": \"2023-01-01T00:00:00Z\", \"sender\": \"1\"},
    {\"timestamp\": \"invalid\", \"sender\": \"2\"}
  ]
}
""",
        encoding="utf-8",
    )
    bad.write_text("not json", encoding="utf-8")

    messages = rc.load_all_messages(tmp_path)

    captured = capsys.readouterr().out
    assert "Failed to parse" in captured
    assert [m["sender"] for m in messages] == ["1", "2"]
    assert messages[0]["_parsed_ts"] == datetime(2023, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert messages[1]["_parsed_ts"] == datetime.max.replace(tzinfo=timezone.utc)


def test_render_ctype_image_respects_media_map():
    part = {"downloadUri": "https://example/image.png"}
    html = rc.render_ctype_image(part, media_map={"image:https://example/image.png": "local.png"})
    assert "local.png" in html


def test_render_ctype_image_returns_none_when_missing_uri():
    assert rc.render_ctype_image({}) is None


@pytest.mark.parametrize(
    "locator, expected",
    [
        ("", "[feed item]"),
        ("hello", "hello"),
    ],
)
def test_render_ctype_feeditem_fallback(locator: str, expected: str):
    part = {"locator": locator}
    html = rc.render_ctype_feeditem(part)
    assert expected in html


def test_render_ctype_feeditem_prefers_video_template():
    part = {"locator": "clip", "contentType": "feedItem"}
    html = rc.render_ctype_feeditem(part, media_map={"feedItem:clip": "movie.MP4"})
    assert "video" in html
    assert "movie.MP4" in html


def test_render_ctype_weblink_and_title_and_voice():
    assert rc.render_ctype_weblink({"text": "https://example"})
    assert rc.render_ctype_weblink({"text": None}) is None
    title_html = rc.render_ctype_title({"productId": "pid", "titleIds": [1, 2]})
    assert "pid" in title_html and "1,2" in title_html
    voice_html = rc.render_ctype_voice({"duration": 100})
    assert "100" in voice_html


def test_render_ctype_unknown_handles_bad_json():
    class Unserializable:
        pass

    html = rc.render_ctype_unknown({"contentType": "weird", "obj": Unserializable()})
    assert "weird" in html


def test_render_message_html_builds_body_and_metadata():
    message = {
        "sender": "me",
        "timestamp": "2023-01-01T00:00:00Z",
        "contentPayload": {
            "content": {
                "parts": [
                    {"contentType": "text", "text": "hello"},
                    {"contentType": "directMention", "text": "ping"},
                    {"contentType": "unknown", "value": 1},
                ]
            }
        },
        "_parsed_ts": datetime(2023, 1, 1, 0, 0),
        "_raw_ts": "2023-01-01T00:00:00Z",
    }
    html = rc.render_message_html(1, message, self_xuid="me")
    assert "data-msg-idx=\"1\"" in html
    assert "justify-end" in html  # self message aligns right
    assert "hello" in html and "ping" in html and "unknown" in html


def test_render_message_html_falls_back_to_raw_ts():
    message = {
        "sender": "other",
        "timestamp": "2023-01-01T00:00:00Z",
        "contentPayload": {"content": {"parts": []}},
        "_parsed_ts": "not-a-datetime",
        "_raw_ts": "raw",
    }
    html = rc.render_message_html(2, message, self_xuid="self")
    assert "raw" in html


def test_render_message_html_skips_empty_image():
    message = {
        "sender": "other",
        "contentPayload": {"content": {"parts": [{"contentType": "image"}]}},
        "_parsed_ts": datetime.max,
        "_raw_ts": "",
    }
    html = rc.render_message_html(3, message, self_xuid="self")
    assert "empty" in html


def test_render_full_html_inserts_messages(monkeypatch: pytest.MonkeyPatch):
    # Simplify template to make assertions deterministic
    monkeypatch.setattr(rc, "HTML_TEMPLATE", "__MESSAGES__")
    html = rc.render_full_html([
        {"sender": "a", "_parsed_ts": datetime.max, "_raw_ts": "", "contentPayload": {"content": {"parts": []}}}
    ], self_xuid="a")
    assert "<div" in html
    assert "__MESSAGES__" not in html
