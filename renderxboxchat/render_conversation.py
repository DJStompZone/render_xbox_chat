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
import html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from renderxboxchat.template import IMAGE_TEMPLATE, VIDEO_EXTS, VIDEO_TEMPLATE, HTML_TEMPLATE

def escape_html(text: str) -> str:
    """
    Safely escape a value for inclusion in HTML.

    Accepts any input, converts it to str, and uses html.escape to replace
    special characters (&, <, >, " and ') with their HTML-safe equivalents.
    """
    # Ensure we always return a string and handle None gracefully.
    return html.escape(f"{text or ''}", quote=True)

def load_all_messages(input_dir: Path) -> List[Dict[str, Any]]:
    """
    Load and normalize chat JSON files from input_dir.

    Scans *.json files (lexicographic order), expects each to contain an object
    with a "messages" list. 
    
    Each message is augmented with:
        - _parsed_ts: parsed datetime (ISO, "Z" → "+00:00") or datetime.max on error
        - _raw_ts: original timestamp string or ""

    Files that fail to parse are skipped with a printed warning.
    Returns messages sorted by _parsed_ts.
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


def render_ctype_text(part: Dict[str, Any]) -> str:
    text = escape_html(part.get("text", ""))
    return "<div class='whitespace-pre-wrap break-words'>{}</div>".format(text)

def render_ctype_image(part: Dict[str, Any], media_map: Optional[Mapping[str, str]] = None) -> str | None:
    """
    Render an HTML image fragment for a conversation content part.

    This function constructs a small HTML snippet containing an <img> element wrapped
    in a <div>. The image source is taken from the part dictionary's "downloadUri"
    key, but can be overridden by a provided media_map that maps a key of the form
    "image:{uri}" to a local or alternate URI.

    Parameters
    ----------
    part : Dict[str, Any]
        A mapping that represents a conversation content part. The function reads
        part.get("downloadUri", "") to obtain the image URI. If that key is missing
        or empty, the function will return None.
    media_map : Optional[Mapping[str, str]], optional
        An optional mapping used to translate remote URIs to local paths or cached
        locations. If provided, the function checks for media_map["image:{uri}"]
        and uses that value as the image source when present and truthy.

    Returns
    -------
    str | None
        A string containing the HTML fragment with the escaped image source, or
        None when there is no available source to render.

    Behavior
    ----------------
    - The function uses part["downloadUri"] as the initial URI if present, else "".
    - If media_map is provided and contains a truthy mapping for f"image:{downloadUri}",
      that mapped value is used instead of the original URI.
    - If the final computed src is empty or falsy, the function returns None.
    """
    uri = part.get("downloadUri", "") or ""
    src = uri
    if media_map is not None:
        local = media_map.get(f"image:{uri}")
        if local:
            src = local
    if not src:
        return
    return  """
        <div class="my-2">
            <img src="{src}"
                alt="image"
                class="rounded-lg border max-w-full h-auto shadow" />
        </div>
        """.format(src=escape_html(src))

def render_ctype_feeditem(part: Dict[str, Any], media_map: Optional[Mapping[str, str]] = None) -> str | None:
    """
    Render a feed-item content part to an HTML snippet.

    Parameters
    ----------
    part : Dict[str, Any]
        A dictionary representing the content part. The function reads the
        "locator" key from this dictionary (falling back to the empty string).
    media_map : Optional[Mapping[str, str]], optional
        Optional mapping from media keys to source URLs/paths. The function will
        look up the key "feedItem:{locator}" to obtain a media source string.
    
    Behavior
    --------
    - Extracts locator = part.get("locator", "") or "".
    - If media_map is provided, attempts to obtain src = media_map.get(f"feedItem:{locator}").
    - If no src is found (or src is falsy), returns a fallback HTML div containing
      the escaped locator (or "[feed item]" if locator is empty):
        "<div class='italic text-xs opacity-70'>[feedItem: {escaped locator}]</div>"
    - If src is found, decides whether to render a video or an image by checking
      the file extension: it lowercases src, splits on '.', and tests whether the
      last segment is in the module-level VIDEO_EXTS set. Any exception during this
      check is treated as "not a video".
    - Selects VIDEO_TEMPLATE for video candidates or IMAGE_TEMPLATE for images,
      formats the chosen template with the escaped src, and returns the resulting HTML.
    
    Returns
    -------
    str | None
        An HTML string representing the feed item or the fallback div. 
        The signature allows None, but in normal operation a string is returned; 
        None could be observed only in unusual/modified implementations.
    """
    locator = part.get("locator", "") or ""
    src = None
    if media_map is not None:
        src = media_map.get(f"feedItem:{locator}")
    if not src:
        text = escape_html(locator or "[feed item]")
        return "<div class='italic text-xs opacity-70'>[feedItem: {}]</div>".format(text)

    # Duck Typing Heuristic: if it looks like a video, render a <video>, else <img>
    try:
        _is_video: bool = src.lower().split('.')[-1] in VIDEO_EXTS
    except Exception:  # noqa: BLE001
        _is_video = False
    _template: str = VIDEO_TEMPLATE if _is_video else IMAGE_TEMPLATE
    return _template.format(src=escape_html(src))
        
def render_ctype_weblink(part: Dict[str, Any]) -> str | None:
    """
    Render a conversation part that contains a web link into an HTML fragment.

    Parameters
    ----------
    part : Dict[str, Any]
        A mapping representing a conversation part. The function looks for the
        "text" key and treats its value as the URL to render. If "text" is
        missing, None, or an empty string, the function returns None.

    Returns
    -------
    str | None
        A small HTML snippet containing a clickable anchor linking to the
        URL if the input URL is truthy, else None.
    """
    url: str = part.get("text", "") or ""
    safe: str = escape_html(url)
    return None if not url else """
        <div class="my-1 text-sm break-all">
            <a href="{safe}" class="underline text-blue-600" target="_blank" rel="noreferrer">
                {safe}
            </a>
        </div>
        """.format(safe=safe)

def render_ctype_title(part: Dict[str, Any]) -> str:
    """
    Render a compact HTML fragment describing the "title" metadata of a conversation part.

    This function extracts product and title identifiers from the provided mapping and
    returns a small, styled HTML <div> containing a bracketed token that lists the
    product identifier followed by a comma-separated list of title identifiers.

    Behavior:
    - Uses 'titleIds' or 'title_ids' in the input mapping if present, else `[]`.
    - Uses 'productId' or 'product_id' in the input mapping if present, else `""`.
    - Each value is converted to a string, title identifiers are joined with commas.
    - The resulting string is wrapped in a <div> with classes "text-xs italic opacity-70".

    Parameters
    ----------
    part : Dict[str, Any]
        Mapping representing a conversation part. Accepted keys:
            - 'titleIds' or 'title_ids': iterable of title identifiers
            - 'productId' or 'product_id': product identifier

    Returns
    -------
    str
        A safe HTML fragment, e.g.:
        "<div class='text-xs italic opacity-70'>[title &lt;product&gt; &lt;id1,id2&gt;]</div>"
    """
    title_ids = part.get("titleIds") or part.get("title_ids") or []
    product_id = part.get("productId") or part.get("product_id") or ""
    return "<div class='text-xs italic opacity-70'>[title {} {}]</div>".format(
        escape_html(str(product_id)),
        escape_html(",".join(str(t) for t in title_ids)),
    )

def render_ctype_voice(part: Dict[str, Any]) -> str:
    """
    Render an HTML fragment describing a voice-message conversation part.

    Parameters
    ----------
    part : Dict[str, Any]
        A dictionary representing a conversation part. The function looks for a
        'duration' key (expected to be the message duration in milliseconds).
        If 'duration' is present and not None it will be included in the label.

    Returns
    -------
    str
        An HTML string containing an italic, small, semi-transparent label for
        the voice message. If a duration is provided the label will read
        "Voice message ({duration} ms)"; otherwise it will read "Voice message".
    """
    duration = part.get("duration")
    label = f"Voice message ({duration} ms)" if duration is not None else "Voice message"
    return f"<div class='italic text-xs opacity-70'>[{escape_html(label)}]</div>"

def render_ctype_directmention(part: Dict[str, Any]) -> str:
    """
    Render a "direct mention" conversation part as an HTML snippet.

    Parameters
    ----------
    part : Dict[str, Any]
        Mapping representing a conversation part. Expected to contain a "text" key whose value
        will be rendered inside the returned HTML. If the "text" key is missing, None, or an
        empty value, an empty string is used.

    Returns
    -------
    str
        An HTML string containing the escaped text wrapped in a <div>
        The text is escaped using `escape_html`.
    """
    text = part.get("text", "") or ""
    return f"<div class='text-xs font-semibold text-purple-700'>{escape_html(text)}</div>"

def render_ctype_unknown(part: Dict[str, Any]) -> str:
    """
    Render a safe, human-readable HTML fallback for a message part whose content type is unrecognized.

    This function is intended as a defensive renderer when the application encounters a
    message "part" with an unknown or unsupported contentType. It produces a small,
    muted HTML fragment that includes the escaped contentType and a JSON-serialized,
    HTML-escaped representation of the entire part so that developers (or users) can
    see what data was received without risking XSS or breaking the page markup.

    Parameters
    ----------
    part : dict
        A mapping representing a message part. The function will attempt to read
        the "contentType" key (falling back to the string "unknown" if missing).
        The entire mapping is JSON-serialized and HTML-escaped for inclusion in the
        output.

    Returns
    -------
    str
        An HTML string containing a <div>, the div text has the format:
        "[unknown contentType: <escaped contentType> part: <escaped json_dump>]".

    Raises
    ------
    TypeError
        If json.dumps(part) fails because `part` contains non-serializable objects.
    """
    ctype = part.get("contentType", "unknown")
    try:
        text, ctype_safe = [escape_html(str(txt)) for txt in (json.dumps(part), ctype)]
    except (TypeError, json.JSONDecodeError):
        text, ctype_safe = [escape_html(str(txt)) for txt in (part, ctype)]
    return f"<div class='italic text-xs opacity-70'>[unknown contentType: {ctype_safe} part: {text}]</div>"

def render_message_html(
    idx: int,
    msg: Mapping[str, Any],
    self_xuid: str,
    media_map: Optional[Mapping[str, str]] = None,
) -> str:
    """
    Render a single message bubble as HTML.

    Each message gets data attributes for JS:
      - data-msg-idx
      - data-sender
      - data-raw-ts

    media_map can optionally map:
      - f"image:{download_uri}" -> relative path
      - f"feedItem:{locator}"   -> relative path to thumbnail or clip
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

        match ctype:
            case "text":
                chunk = render_ctype_text(part)
                body_chunks.append(chunk)

            case "image":
                chunk = render_ctype_image(part, media_map=media_map)
                if not chunk:
                    continue
                body_chunks.append(chunk)

            case "feedItem":
                chunk = render_ctype_feeditem(part, media_map=media_map)
                if not chunk:
                    continue
                body_chunks.append(chunk)

            case "weblink":
                chunk = render_ctype_weblink(part)
                if chunk:
                    body_chunks.append(chunk)

            case "title":
                chunk = render_ctype_title(part)
                body_chunks.append(chunk)

            case "voice":
                chunk = render_ctype_voice(part)
                body_chunks.append(chunk)

            case "directMention":
                chunk = render_ctype_directmention(part)
                body_chunks.append(chunk)

            case _:
                chunk = render_ctype_unknown(part)
                body_chunks.append(chunk)

    body_html = "\n".join(body_chunks) or "<div class='italic opacity-60'>[empty]</div>"

    alignment = f"justify-{'end' if is_self else 'start'}"
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


def render_full_html(
    messages: List[Dict[str, Any]],
    self_xuid: str,
    media_map: Optional[Dict[str, str]] = None,
) -> str:
    """
    Produce the final HTML document.

    - All messages are rendered server-side in chronological order.
    - JS enhances with:
        * Search
        * Asc/Desc sorting (by timestamp)
        * Jump-to-page (PAGE_SIZE chunk, scroll-based)
    
    Procedure (pseudo):
    For contentType == "image":
        key = f"image:{downloadUri}"
        if key in media_map ⇒ use media_map[key] as src
        
    For contentType == "feedItem":
        key = f"feedItem:{locator}`
        if key in media_map ⇒ render <img> or <video> based on extension
    """
    msg_html: List[str] = []
    for idx, msg in enumerate(messages):
        rendered = render_message_html(
            idx,
            msg,
            self_xuid,
            media_map=media_map,
        )
        msg_html.append(rendered)
        if idx < 5 or idx >= len(messages) - 5:
            print(f"[DEBUG] Message {idx} HTML:\n{rendered}\n")
    messages_block = "\n".join(msg_html)
    print(f"[DEBUG] Full messages block length: {len(messages_block)}")
    final_html = HTML_TEMPLATE.replace("__MESSAGES__", messages_block)
    print(f"[DEBUG] Final HTML length: {len(final_html)}")

    return final_html