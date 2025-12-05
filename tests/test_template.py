from __future__ import annotations

from renderxboxchat import template


def test_replace_template_parts_replaces_all():
    template_str = "Hello __NAME__ and __NAME__"
    result = template.replace_template_parts(template_str, {"__NAME__": "World"})
    assert result == "Hello World and World"


def test_video_exts_present():
    assert "mp4" in template.VIDEO_EXTS
