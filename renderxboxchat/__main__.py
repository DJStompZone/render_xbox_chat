#!/usr/bin/env python3
"""
CLI entrypoint for renderxboxchat.

Modes:

1. Single-conversation render:

    ```
    python -m renderxboxchat \
      --input-dir path/to/full_conv_1234 \
      --out path/to/conversation_1234.html \
      --self-xuid 2531234567890123
    ```

2. Full auto mode:

    `python -m renderxboxchat`

    - Uses xbl3auth to obtain an XBL3.0 token
    - Fetches all conversations and all pages into --raw-root
    - Prefetches media for each conversation
    - Renders an HTML file per conversation into --html-root
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from renderxboxchat.render_conversation import load_all_messages, render_full_html
from renderxboxchat.media import prefetch_media_for_messages
from renderxboxchat.fetch import fetch_all_conversations, fetch_own_xuid, _get_auth_header

def _log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}")


def _render_single_conversation(
    input_dir: Path,
    out_html: Path,
    self_xuid: str,
    static_dir: Optional[Path] = None,
    xbl3_header: Optional[str] = None,
) -> None:
    """
    Render a single conversation from already-downloaded JSON pages.

    - Loads all messages from input_dir
    - Prefetches media into static_dir (sibling of out_html by default)
    - Writes final HTML to out_html
    """
    input_dir = input_dir.resolve()
    out_html = out_html.resolve()

    if static_dir is None:
        static_dir = out_html.parent / "static"

    _log("INFO", f"Loading messages from {input_dir} ...")
    messages = load_all_messages(input_dir)

    _log("INFO", f"Prefetching media into {static_dir} ...")
    media_map = prefetch_media_for_messages(messages, static_dir, xbl3_header=xbl3_header)

    _log("INFO", f"Rendering HTML to {out_html} ...")
    html = render_full_html(messages, self_xuid, media_map=media_map)
    out_html.write_text(html, encoding="utf-8")
    _log("INFO", f"Wrote {out_html} ({len(messages)} messages).")


def _auto_fetch_and_render(
    raw_root: Path,
    html_root: Path,
    self_xuid: str,
    page_size: int,
    xbl3_header: Optional[str],
) -> None:
    """
    Full-auto mode:
      - Fetch all conversations to raw_root
      - Render each conversation to html_root
    """
    raw_root = raw_root.resolve()
    html_root = html_root.resolve()
    raw_root.mkdir(parents=True, exist_ok=True)
    html_root.mkdir(parents=True, exist_ok=True)

    # If no self_xuid provided, try to fetch it from the Xbox Live profile.
    if not self_xuid:

        _log("INFO", "Fetching own XUID from Xbox Live profile ...")
        auth_header = _get_auth_header(xbl3_header)
        self_xuid = fetch_own_xuid(auth_header)
        _log("INFO", f"Detected self XUID: {self_xuid}")

    _log("INFO", f"Fetching all conversations into {raw_root} ...")
    convs = fetch_all_conversations(raw_root, xbl3_header=xbl3_header, max_items_history=page_size)

    if not convs:
        _log("WARN", "No conversations fetched. Nothing to render.")
        return

    _log("INFO", f"Rendering {len(convs)} conversations into {html_root} ...")

    for conv_id, conv_dir in tqdm(convs, desc="Rendering", unit="conv"):
        out_html = html_root / f"conversation_{conv_id}.html"
        static_dir = html_root / f"static_{conv_id}"
        try:
            _render_single_conversation(
                input_dir=conv_dir,
                out_html=out_html,
                self_xuid=self_xuid,
                static_dir=static_dir,
                xbl3_header=xbl3_header,
            )
        except Exception as exc:  # noqa: BLE001
            _log("WARN", f"Failed to render conversation {conv_id}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render Xbox Live conversations to searchable HTML."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Directory containing JSON pages for a single conversation "+
             "(e.g. full_conv_<xuid>). If omitted, full auto mode is used.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Output HTML file path for single-conversation mode.",
    )
    parser.add_argument(
        "--self-xuid",
        default="",
        help="Your XUID (messages from this XUID are right-aligned).",
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=Path("raw_conversations"),
        help="Root directory for raw conversation JSON in auto mode.",
    )
    parser.add_argument(
        "--html-root",
        type=Path,
        default=Path("raw_conversations"),
        help="Root directory for rendered HTML in auto mode.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=200,
        help="Max items per API page when fetching messages.",
    )
    parser.add_argument(
        "--xbl3-header",
        type=str,
        default=None,
        help='Optional explicit XBL3.0 Authorization header '+
            '(e.g. "XBL3.0 x=<uhs>;<token>"). '+
            'If omitted, xbl3auth is used.',
    )

    args = parser.parse_args()

    if args.input_dir is not None:
        if args.out is None:
            raise SystemExit("--out is required when --input-dir is specified.")
        _render_single_conversation(
            input_dir=args.input_dir,
            out_html=args.out,
            self_xuid=args.self_xuid,
            static_dir=None,
            xbl3_header=args.xbl3_header,
        )
        return

    _auto_fetch_and_render(
        raw_root=args.raw_root,
        html_root=args.html_root,
        self_xuid=args.self_xuid,
        page_size=args.page_size,
        xbl3_header=args.xbl3_header,
    )


if __name__ == "__main__":
    main()
