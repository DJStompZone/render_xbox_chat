#!/usr/bin/env python3
"""
Render a complete Xbox Live conversation into a searchable,
mobile-friendly HTML file.

Features:
- Loads all JSON pages from an input directory
- Merges and sorts messages chronologically
- Renders full conversation by default
- Client-side controls for:
    * Search
    * Asc/Desc sort
    * "Jump to page" (scrolls to a page-sized offset)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from renderxboxchat.template import html_template as template

def escape_html(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def load_all_messages(input_dir: Path) -> List[Dict[str, Any]]:
    """
    Load every page JSON, extract messages[], and flatten into one list.
    Adds _parsed_ts (datetime) for sorting and _raw_ts (ISO string) for JS.
    """
    pages = sorted(input_dir.glob("*.json"))
    all_msgs: List[Dict[str, Any]] = []

    for page in pages:
        try:
            data = json.loads(page.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to parse {page}: {exc}")
            continue

        msgs = data.get("messages") or []
        for m in msgs:
            ts = m.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                dt = datetime.max

            m["_parsed_ts"] = dt
            m["_raw_ts"] = ts
            all_msgs.append(m)

    all_msgs.sort(key=lambda m: m["_parsed_ts"])
    return all_msgs


def render_message_html(idx: int, msg: Dict[str, Any], self_xuid: str) -> str:
    """
    Render a single message bubble as HTML.

    Each message gets data attributes for JS:
      - data-msg-idx
      - data-sender
      - data-raw-ts
    """
    sender = msg.get("sender", "")
    is_self = sender == self_xuid

    raw_ts = msg.get("_raw_ts") or msg.get("timestamp", "")
    try:
        dt = msg["_parsed_ts"]
        if isinstance(dt, datetime):
            ts_display = dt.strftime("%Y-%m-%d %H:%M")
        else:
            ts_display = raw_ts
    except Exception:  # noqa: BLE001
        ts_display = raw_ts

    parts = (
        msg.get("contentPayload", {})
        .get("content", {})
        .get("parts", [])
    )

    body_chunks: List[str] = []
    for part in parts:
        ctype = part.get("contentType")
        if ctype == "text":
            text = escape_html(part.get("text", ""))
            body_chunks.append("<div class='whitespace-pre-wrap break-words'>{}</div>".format(text))
        elif ctype == "image":
            uri = part.get("downloadUri", "")
            body_chunks.append(
                """
                <div class="my-2">
                    <img src="{src}"
                         alt="image"
                         class="rounded-lg border max-w-full h-auto shadow" />
                </div>
                """.format(src=escape_html(uri))
            )
        else:
            text = escape_html(f"[{ctype or 'unknown'} part]")
            body_chunks.append(f"<div class='italic text-xs opacity-70'>{text}</div>")

    body_html = "\n".join(body_chunks) or "<div class='italic opacity-60'>[empty]</div>"

    alignment = "justify-end" if is_self else "justify-start"
    bubble_style = "bg-green-600 text-white" if is_self else "bg-gray-200 text-black"

    return f"""
    <div class="flex {alignment} my-2" 
         data-msg-idx="{idx}" 
         data-sender="{escape_html(sender)}" 
         data-raw-ts="{escape_html(raw_ts)}">
        <div class="max-w-[80%] p-3 rounded-lg {bubble_style} shadow">
            {body_html}
            <div class="text-xs opacity-70 mt-1">{escape_html(ts_display)}</div>
        </div>
    </div>
    """


def render_full_html(messages: List[Dict[str, Any]], self_xuid: str) -> str:
    """
    Produce the final HTML document.

    - All messages are rendered server-side in chronological order.
    - JS enhances with:
        * Search
        * Asc/Desc sorting (by timestamp)
        * Jump-to-page (PAGE_SIZE chunk, scroll-based)
    """
    msg_html = []
    for idx, msg in enumerate(messages):
        msg_html.append(render_message_html(idx, msg, self_xuid))

    messages_block = "\n".join(msg_html)

    
    return template.replace("__MESSAGES__", messages_block)



