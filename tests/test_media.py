from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from renderxboxchat import media


def test_parse_locator_and_grouping():
    locator = "screenshotsmetadata.xboxlive.com/users/xuid(1)/scids/abc/screenshots/123"
    info = media._parse_locator(locator)
    assert info and info.kind == "screenshot" and info.item_id == "123"
    grouped = media._group_locators_by_xuid([info])
    assert "1" in grouped[0] and grouped[0]["1"][0].scid == "abc"
    assert media._parse_locator("") is None
    assert media._parse_locator("bad/locator") is None


def test_sas_expiry_detection():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert media._sas_is_expired(f"https://example?se={past}") is True
    assert media._sas_is_expired(f"https://example?se={future}") is False
    assert media._sas_is_expired("https://example") is False


def test_get_auth_header_media(monkeypatch: pytest.MonkeyPatch):
    class DummyService:
        def __init__(self, *_, **__):
            pass

        def get_xbl3_token(self) -> str:
            return "media-token"

    monkeypatch.setattr(media, "Xbl3AuthService", DummyService)
    monkeypatch.setattr(media, "XblAuthConfig", lambda: object())
    assert media._get_auth_header(None) == "media-token"

    monkeypatch.setattr(media, "Xbl3AuthService", None)
    monkeypatch.setattr(media, "XblAuthConfig", None)
    with pytest.raises(RuntimeError):
        media._get_auth_header(None)


def test_collect_helpers_and_extensions():
    messages = [
        {
            "contentPayload": {
                "content": {
                    "parts": [
                        {"contentType": "feedItem", "locator": "screenshotsmetadata.xboxlive.com/users/xuid(1)/scids/abc/screenshots/1"},
                        {"contentType": "image", "downloadUri": "https://img.test/file.JPG?query=1"},
                    ]
                }
            }
        }
    ]
    locators = media._collect_locators(messages)
    assert len(locators) == 1
    imgs = media._collect_direct_images(messages)
    assert imgs == ["https://img.test/file.JPG?query=1"]
    assert media._infer_extension_from_url(imgs[0], default="png") == "jpg"


def test_prefetch_media_for_messages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    messages = [
        {
            "contentPayload": {
                "content": {
                    "parts": [
                        {"contentType": "image", "downloadUri": "https://img.test/file.png"},
                        {"contentType": "feedItem", "locator": "screenshotsmetadata.xboxlive.com/users/xuid(1)/scids/abc/screenshots/1"},
                        {"contentType": "feedItem", "locator": "gameclipsmetadata.xboxlive.com/users/xuid(2)/scids/abc/clips/2"},
                    ]
                }
            }
        }
    ]

    monkeypatch.setattr(media, "_get_auth_header", lambda header=None: "token")
    monkeypatch.setattr(media, "_sas_is_expired", lambda url: False)

    def fake_download(session, url, dest, headers=None):
        dest.write_bytes(b"data")

    monkeypatch.setattr(media, "_download_file", fake_download)
    monkeypatch.setattr(
        media,
        "_build_screenshot_index_for_xuid",
        lambda session, xuid, auth_header: {"1": {"screenshotUris": [{"uri": "https://shot"}]}}
    )
    monkeypatch.setattr(
        media,
        "_build_gameclip_index_for_xuid",
        lambda session, xuid, auth_header: {"2": {"gameClipUris": [{"uri": "https://clip", "uriType": "Download"}]}}
    )

    result = media.prefetch_media_for_messages(messages, tmp_path)

    assert result["image:https://img.test/file.png"].startswith(tmp_path.name)
    assert any(key.startswith("feedItem:") for key in result)
    # ensure index file written
    assert (tmp_path / "media_index.json").exists()


def test_download_file_streams_content(tmp_path: Path):
    from tests.conftest import FakeResponse, SequenceSession

    content = b"abc123"
    dest = tmp_path / "file.bin"
    resp = FakeResponse(status_code=200, json_data=None, text="")
    resp._content = content

    class DownloadSession(SequenceSession):
        def get(self, url: str, **kwargs):  # pragma: no cover - simple forwarding
            return resp

    media._download_file(DownloadSession([]), "https://example", dest)
    assert dest.read_bytes() == content


def test_prefetch_media_skips_expired_and_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    img_path = tmp_path / "image_0001.jpg"
    img_path.write_bytes(b"existing")
    messages = [
        {
            "contentPayload": {"content": {"parts": [{"contentType": "image", "downloadUri": "https://expired"}]}}
        }
    ]
    monkeypatch.setattr(media, "_get_auth_header", lambda header=None: "token")
    monkeypatch.setattr(media, "_sas_is_expired", lambda url: True)

    result = media.prefetch_media_for_messages(messages, tmp_path)
    # expired url ignored, no new entries
    assert result == {}


def test_prefetch_media_handles_missing_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    shot = media.LocatorInfo(kind="screenshot", raw="loc1", xuid="1", scid="s", item_id="a")
    clip = media.LocatorInfo(kind="gameclip", raw="loc2", xuid="2", scid="s", item_id="b")
    monkeypatch.setattr(media, "_get_auth_header", lambda header=None: "token")
    monkeypatch.setattr(media, "_collect_locators", lambda messages: [shot, clip])
    monkeypatch.setattr(media, "_collect_direct_images", lambda messages: [])
    monkeypatch.setattr(media, "_build_screenshot_index_for_xuid", lambda *args, **kwargs: {})
    monkeypatch.setattr(media, "_build_gameclip_index_for_xuid", lambda *args, **kwargs: {"b": {"gameClipUris": []}})
    result = media.prefetch_media_for_messages([], tmp_path)
    out = capsys.readouterr().out
    assert result == {}
    assert "No screenshot metadata" in out or "Clip has no URIs" in out


def test_build_index_helpers(monkeypatch: pytest.MonkeyPatch):
    from tests.conftest import FakeResponse, SequenceSession

    shot_resp = FakeResponse(json_data={"screenshots": [{"screenshotId": "1"}]})
    clip_resp = FakeResponse(json_data={"gameClips": [{"gameClipId": "2"}]})
    session = SequenceSession([shot_resp])
    idx = media._build_screenshot_index_for_xuid(session, "1", "hdr")
    assert idx == {"1": {"screenshotId": "1"}}

    session2 = SequenceSession([clip_resp])
    idx2 = media._build_gameclip_index_for_xuid(session2, "1", "hdr")
    assert idx2 == {"2": {"gameClipId": "2"}}


def test_prefetch_media_uses_existing_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    shot_info = media.LocatorInfo(kind="screenshot", raw="loc1", xuid="1", scid="s", item_id="shot")
    clip_info = media.LocatorInfo(kind="gameclip", raw="loc2", xuid="2", scid="s", item_id="clip")
    monkeypatch.setattr(media, "_get_auth_header", lambda header=None: "token")
    monkeypatch.setattr(media, "_collect_locators", lambda messages: [shot_info, clip_info])
    monkeypatch.setattr(media, "_collect_direct_images", lambda messages: [])
    shot_file = tmp_path / "screenshot_shot.jpg"
    shot_file.write_bytes(b"data")
    clip_file = tmp_path / "clip_clip.mp4"
    clip_file.write_bytes(b"data")

    monkeypatch.setattr(
        media,
        "_build_screenshot_index_for_xuid",
        lambda *args, **kwargs: {"shot": {"screenshotUris": [{"uri": "https://shot.jpg", "uriType": "Download"}]}}
    )
    monkeypatch.setattr(
        media,
        "_build_gameclip_index_for_xuid",
        lambda *args, **kwargs: {"clip": {"gameClipUris": [{"uri": "https://clip.mp4", "uriType": "Download"}]}}
    )
    result = media.prefetch_media_for_messages([], tmp_path)
    assert result["feedItem:loc1"].endswith("screenshot_shot.jpg")
    assert result["feedItem:loc2"].endswith("clip_clip.mp4")
