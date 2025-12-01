#!/usr/bin/env python3
"""
Fetch media for Xbox Live conversation feed items.

- Scans conversation_*.json pages for parts with contentType == "feedItem"
- Locators look like:
    screenshotsmetadata.xboxlive.com/users/xuid(253...)/scids/.../screenshots/<GUID>
    gameclipsmetadata.xboxlive.com/users/xuid(253...)/scids/.../clips/<GUID>
- For each unique locator:
    * Calls the corresponding metadata endpoint with XBL3 auth from xbl3auth
    * Picks a suitable download URI
    * Downloads the image/clip to disk
- Writes feed_media_index.json mapping:
    {
        "<locator>": "screenshots/<id>.jpg",
        ...
    }

Auth sources (in order of precedence):
    1. --auth-header CLI flag
    2. XBL3_AUTH_HEADER environment variable
    3. xbl3auth library (device code flow + keyring)

Usage:
    python fetch_feed_media.py \
        --input-dir raw_conversations/full_conv_... \
        --out-dir attachments

Optional:
    python fetch_feed_media.py \
        --input-dir raw_conversations/full_conv_... \
        --out-dir attachments \
        --account-id default \
        --client-id "your-azure-client-id"
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import requests

try:
    from xbl3auth import Xbl3AuthService, XblAuthConfig
except ImportError as exc:  # noqa: BLE001
    raise SystemExit("xbl3auth is required for this script.\nInstall with: pip install xbl3auth") from exc


LOCATOR_RE = re.compile(
    r"^(?P<host>screenshotsmetadata|gameclipsmetadata)\.xboxlive\.com/(?P<path>.+)$"
)


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _iter_message_parts(json_obj: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for msg in json_obj.get("messages") or []:
        parts = (
            msg.get("contentPayload", {})
            .get("content", {})
            .get("parts", [])
        )
        for part in parts:
            yield part


def _collect_locators(input_dir: Path) -> List[str]:
    locators: set[str] = set()
    for path in sorted(input_dir.glob("*.json")):
        try:
            data = _read_json(path)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] Failed to parse {path}: {exc}")
            continue

        for part in _iter_message_parts(data):
            if part.get("contentType") != "feedItem":
                continue
            locator = part.get("locator")
            if not locator or not isinstance(locator, str):
                continue
            if not LOCATOR_RE.match(locator):
                print(f"[WARN] Skipping unrecognized locator: {locator}")
                continue
            locators.add(locator)

    return sorted(locators)


def _pick_best_uri(candidates: List[Dict[str, Any]], uri_key: str = "uri") -> str:
    """
    Choose a "best" URI from the metadata list.

    Preference:
    - uriType/uri_type == 'Download' (case-insensitive) if present
    - otherwise the largest file_size
    """
    if not candidates:
        raise ValueError("No URIs available in metadata")

    best_download = None
    for item in candidates:
        uri_type = str(item.get("uriType") or item.get("uri_type") or "").lower()
        if uri_type == "download":
            best_download = item
            break

    if best_download:
        return str(best_download.get(uri_key))

    def size_of(it: Dict[str, Any]) -> int:
        try:
            return int(it.get("fileSize") or it.get("file_size") or 0)
        except Exception:  # noqa: BLE001
            return 0

    best = max(candidates, key=size_of)
    return str(best.get(uri_key))


def _guess_extension_from_url(url: str) -> str:
    path = urlparse(url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        ext = ext.split("?")[0]
        if ext:
            return f".{ext}"
    return ""


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _get_auth_header_from_xbl3auth(
    client_id: str | None,
    account_id: str,
) -> str:
    """
    Use xbl3auth to obtain an XBL3.0 header.

    Respects client_id override; otherwise uses remote/built-in config.
    """
    if client_id:
        cfg = XblAuthConfig(client_id=client_id)
    else:
        cfg = XblAuthConfig()

    service = Xbl3AuthService(cfg, account_id=account_id)
    header = service.get_xbl3_token()
    if not isinstance(header, str) or not header.startswith("XBL3.0 "):
        raise RuntimeError("xbl3auth returned an unexpected token format")
    # Do NOT print the token; just acknowledge we got one.
    print("[AUTH] Obtained XBL3.0 header via xbl3auth.")
    return header


def _resolve_auth_header(
    explicit_header: str | None,
    client_id: str | None,
    account_id: str,
) -> str:
    """
    Resolve the Authorization header we'll use for Xbox Live calls.
    Precedence:
        1. explicit_header (CLI)
        2. XBL3_AUTH_HEADER env var
        3. xbl3auth device flow/keyring
    """
    if explicit_header:
        print("[AUTH] Using explicit --auth-header value.")
        return explicit_header

    env_header = os.getenv("XBL3_AUTH_HEADER")
    if env_header:
        print("[AUTH] Using XBL3_AUTH_HEADER from environment.")
        return env_header

    print("[AUTH] No header supplied; using xbl3auth to obtain XBL3.0 token.")
    return _get_auth_header_from_xbl3auth(client_id, account_id)


def _fetch_metadata(
    session: requests.Session,
    locator: str,
    auth_header: str,
) -> Tuple[str, Dict[str, Any]]:
    """
    Fetch metadata for a single locator.

    Returns (kind, metadata_dict) where kind in {"screenshot", "gameclip"}.
    """
    m = LOCATOR_RE.match(locator)
    if not m:
        raise ValueError(f"Unsupported locator: {locator}")

    host = m.group("host")
    path = m.group("path")
    url = f"https://{host}.xboxlive.com/{path}"

    if host == "screenshotsmetadata":
        headers = {
            "Authorization": auth_header,
            "x-xbl-contract-version": "5",
        }
        kind = "screenshot"
    elif host == "gameclipsmetadata":
        headers = {
            "Authorization": auth_header,
            "x-xbl-contract-version": "1",
        }
        kind = "gameclip"
    else:
        raise ValueError(f"Unknown locator host: {host}")

    print(f"[META] GET {url}")
    resp = session.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    if kind == "screenshot":
        container_keys = ["screenshots", "Screenshots"]
    else:
        container_keys = ["gameClips", "GameClips", "game_clips"]

    for key in container_keys:
        if key in data and isinstance(data[key], list) and data[key]:
            return kind, data[key][0]

    # Fallback: treat the whole response as the item
    return kind, data


def _download_file(session: requests.Session, url: str, dest: Path) -> None:
    print(f"[DL] {url} -> {dest}")
    with session.get(url, stream=True) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fp:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                fp.write(chunk)


def fetch_all_media(
    input_dir: Path,
    out_dir: Path,
    auth_header: str,
) -> None:
    locators = _collect_locators(input_dir)
    print(f"[INFO] Found {len(locators)} unique feedItem locators")

    if not locators:
        print("[INFO] Nothing to do.")
        return

    _ensure_dir(out_dir)
    screenshots_dir = out_dir / "screenshots"
    clips_dir = out_dir / "gameclips"
    _ensure_dir(screenshots_dir)
    _ensure_dir(clips_dir)

    mapping: Dict[str, str] = {}

    with requests.Session() as session:
        for locator in locators:
            try:
                kind, meta = _fetch_metadata(session, locator, auth_header)
            except Exception as exc:  # noqa: BLE001
                print(f"[ERROR] Metadata fetch failed for {locator}: {exc}")
                continue

            if kind == "screenshot":
                uris = meta.get("screenshotUris") or meta.get("screenshot_uris") or []
                id_key = "screenshotId"
            else:
                uris = meta.get("gameClipUris") or meta.get("game_clip_uris") or []
                id_key = "gameClipId"

            try:
                download_url = _pick_best_uri(uris)
            except Exception as exc:  # noqa: BLE001
                print(f"[ERROR] No usable URIs for {locator}: {exc}")
                continue

            item_id = str(meta.get(id_key) or meta.get(id_key.lower()) or "unknown")
            ext = _guess_extension_from_url(download_url)
            if not ext:
                ext = ".mp4" if kind == "gameclip" else ".jpg"

            if kind == "screenshot":
                dest = screenshots_dir / f"{item_id}{ext}"
                rel = Path("screenshots") / f"{item_id}{ext}"
            else:
                dest = clips_dir / f"{item_id}{ext}"
                rel = Path("gameclips") / f"{item_id}{ext}"

            if dest.exists():
                print(f"[SKIP] Already exists: {dest}")
            else:
                try:
                    _download_file(session, download_url, dest)
                except Exception as exc:  # noqa: BLE001
                    print(f"[ERROR] Download failed for {locator}: {exc}")
                    continue

            mapping[locator] = str(rel.as_posix())

    index_path = out_dir / "feed_media_index.json"
    with index_path.open("w", encoding="utf-8") as fp:
        json.dump(mapping, fp, indent=2, sort_keys=True)
    print(f"[INFO] Wrote mapping for {len(mapping)} locators -> {index_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prefetch media for Xbox conversation feedItems."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing conversation_..._page_XXX.json files.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("attachments"),
        help="Directory where media files and index JSON will be stored.",
    )
    parser.add_argument(
        "--auth-header",
        type=str,
        default=None,
        help=(
            "Full Authorization header value, e.g. "
            "'XBL3.0 x=USERHASH;TOKEN'. "
            "If omitted, XBL3_AUTH_HEADER env var or xbl3auth will be used."
        ),
    )
    parser.add_argument(
        "--account-id",
        type=str,
        default="default",
        help="xbl3auth account_id label to use when pulling from keyring.",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Optional Azure client ID override for xbl3auth.",
    )

    args = parser.parse_args()

    auth_header = _resolve_auth_header(
        explicit_header=args.auth_header,
        client_id=args.client_id,
        account_id=args.account_id,
    )

    fetch_all_media(args.input_dir, args.out_dir, auth_header)


if __name__ == "__main__":
    main()
