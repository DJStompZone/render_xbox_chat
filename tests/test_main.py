from __future__ import annotations

from pathlib import Path

import pytest

from renderxboxchat import __main__


def test_render_single_conversation_writes_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    inbox = tmp_path / "input"
    inbox.mkdir()
    (inbox / "page_000.json").write_text("{\"messages\": []}", encoding="utf-8")
    out_html = tmp_path / "out.html"
    media_dir = tmp_path / "static"

    monkeypatch.setattr(__main__, "load_all_messages", lambda path: [{"sender": "self", "_parsed_ts": None, "_raw_ts": "", "contentPayload": {"content": {"parts": []}}}])
    monkeypatch.setattr(__main__, "prefetch_media_for_messages", lambda messages, static_dir, xbl3_header=None: {"k": "v"})
    monkeypatch.setattr(__main__, "render_full_html", lambda messages, self_xuid, media_map=None: "<html>done</html>")

    __main__._render_single_conversation(inbox, out_html, self_xuid="self", static_dir=media_dir)
    assert out_html.read_text(encoding="utf-8") == "<html>done</html>"


def test_render_single_conversation_uses_default_static_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    inbox = tmp_path / "input"
    inbox.mkdir()
    (inbox / "page_000.json").write_text("{\"messages\": []}", encoding="utf-8")
    out_html = tmp_path / "output.html"

    monkeypatch.setattr(__main__, "load_all_messages", lambda path: [])
    captured = {}

    def fake_prefetch(messages, static_dir, xbl3_header=None):
        captured["static_dir"] = static_dir
        static_dir.mkdir(parents=True, exist_ok=True)
        return {}

    monkeypatch.setattr(__main__, "prefetch_media_for_messages", fake_prefetch)
    monkeypatch.setattr(__main__, "render_full_html", lambda *args, **kwargs: "<html></html>")

    __main__._render_single_conversation(inbox, out_html, self_xuid="self", static_dir=None)
    assert captured["static_dir"] == out_html.parent / "static"


def test_auto_fetch_and_render_runs_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    html_root = tmp_path / "html"
    raw_root = tmp_path / "raw"
    conv_dir = raw_root / "full_conv_1"
    conv_dir.mkdir(parents=True)
    (conv_dir / "page_000.json").write_text("{\"messages\": []}", encoding="utf-8")

    monkeypatch.setattr(__main__, "_get_auth_header", lambda header=None: "hdr")
    monkeypatch.setattr(__main__, "fetch_own_xuid", lambda *_: "self")
    monkeypatch.setattr(__main__, "fetch_all_conversations", lambda *_, **__: [("1", conv_dir)])
    called = {}
    monkeypatch.setattr(__main__, "_render_single_conversation", lambda **kwargs: called.setdefault("ran", True))

    __main__._auto_fetch_and_render(raw_root, html_root, self_xuid="", page_size=10, xbl3_header=None)
    assert called.get("ran") is True


def test_auto_fetch_and_render_catches_exceptions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    html_root = tmp_path / "html"
    raw_root = tmp_path / "raw"
    conv_dir = raw_root / "full_conv_1"
    conv_dir.mkdir(parents=True)
    (conv_dir / "page_000.json").write_text("{\"messages\": []}", encoding="utf-8")

    monkeypatch.setattr(__main__, "_get_auth_header", lambda header=None: "hdr")
    monkeypatch.setattr(__main__, "fetch_own_xuid", lambda *_: "self")
    monkeypatch.setattr(__main__, "fetch_all_conversations", lambda *_, **__: [("1", conv_dir)])
    monkeypatch.setattr(__main__, "_render_single_conversation", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    __main__._auto_fetch_and_render(raw_root, html_root, self_xuid="", page_size=10, xbl3_header=None)
    out = capsys.readouterr().out
    assert "Failed to render conversation" in out


def test_main_argument_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    inbox = tmp_path / "input"
    inbox.mkdir()
    out_html = tmp_path / "out.html"

    called = {}
    monkeypatch.setattr(__main__, "_render_single_conversation", lambda **kwargs: called.setdefault("called", kwargs))
    monkeypatch.setattr(__main__.argparse, "ArgumentParser", __main__.argparse.ArgumentParser)

    argv = [
        "prog",
        "--input-dir",
        str(inbox),
        "--out",
        str(out_html),
        "--self-xuid",
        "self",
    ]
    monkeypatch.setattr(__main__, "main", __main__.main)
    monkeypatch.setattr(__main__.sys, "argv", argv)
    __main__.main()
    assert called["called"]["self_xuid"] == "self"


def test_main_requires_out_when_input_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    argv = ["prog", "--input-dir", str(tmp_path)]
    monkeypatch.setattr(__main__.sys, "argv", argv)
    with pytest.raises(SystemExit):
        __main__.main()


def test_auto_fetch_and_render_no_conversations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(__main__, "_get_auth_header", lambda header=None: "hdr")
    monkeypatch.setattr(__main__, "fetch_own_xuid", lambda *_: "self")
    monkeypatch.setattr(__main__, "fetch_all_conversations", lambda *_, **__: [])
    __main__._auto_fetch_and_render(tmp_path, tmp_path, self_xuid="", page_size=10, xbl3_header=None)
    out = capsys.readouterr().out
    assert "Nothing to render" in out
