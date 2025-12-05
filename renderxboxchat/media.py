#!/usr/bin/env python3
"""
Media prefetcher for renderxboxchat.

Walks conversation messages, discovers:
  - direct image attachments (contentType == "image")
  - feed items referencing screenshots or game clips (contentType == "feedItem")

Then:
  - Resolves metadata via the official Xbox metadata endpoints, using an XBL3.0
    auth header obtained via the xbl3auth library if the caller does not supply one.
  - Downloads media to a local directory.
  - Returns a mapping from logical keys to local file paths for use by the renderer.

Logical key schema:
  - Direct images: f"image:{download_uri}"
  - Feed items:    f"feedItem:{locator}"
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

try:
    from xbl3auth import Xbl3AuthService, XblAuthConfig
except Exception:  # pragma: no cover - optional import, validated at runtime
    Xbl3AuthService = None  # type: ignore[assignment]
    XblAuthConfig = None  # type: ignore[assignment]


GAMECLIPS_BASE = "https://gameclipsmetadata.xboxlive.com"
SCREENSHOTS_BASE = "https://screenshotsmetadata.xboxlive.com"


@dataclass
class LocatorInfo:
    kind: str  # "gameclip" or "screenshot"
    raw: str
    xuid: str
    scid: str
    item_id: str


def _log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}")


def _raise_for_status(resp: Any) -> None:
    """Call resp.raise_for_status() if available, else emulate it."""

    raise_func = getattr(resp, "raise_for_status", None)
    if callable(raise_func):
        raise_func()
        return

    status_code = getattr(resp, "status_code", None)
    if status_code is not None and status_code >= 400:
        raise requests.HTTPError(response=resp)


def _parse_locator(locator: str) -> Optional[LocatorInfo]:
    """
    Parse a feedItem locator string into structured components.

    Expected patterns (no scheme, just host + path):

      screenshotsmetadata.xboxlive.com/users/xuid(253...)/scids/{scid}/screenshots/{id}
      gameclipsmetadata.xboxlive.com/users/xuid(253...)/scids/{scid}/clips/{id}

    Returns None if the shape does not match.
    """
    if not locator:
        return None

    parts = locator.split("/")
    if len(parts) < 7:
        return None

    host = parts[0]
    if not host.endswith("xboxlive.com"):
        return None

    # host, "users", "xuid(123)", "scids", "{scid}", "screenshots|clips", "{guid}"
    try:
        users_token = parts[1]
        xuid_token = parts[2]
        scids_token = parts[3]
        scid = parts[4]
        kind_segment = parts[5]
        item_id = parts[6]
    except IndexError:
        return None

    if users_token != "users" or not xuid_token.startswith("xuid(") or scids_token != "scids":
        return None

    xuid = xuid_token[len("xuid(") : -1]

    if "screenshot" in kind_segment:
        kind = "screenshot"
    elif "clip" in kind_segment:
        kind = "gameclip"
    else:
        return None

    return LocatorInfo(kind=kind, raw=locator, xuid=xuid, scid=scid, item_id=item_id)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _infer_extension_from_url(url: str, default: str) -> str:
    path = url.split("?", 1)[0]
    _, _, tail = path.rpartition("/")
    if "." in tail:
        return tail.split(".")[-1].lower()
    return default


def _sas_is_expired(url: str) -> bool:
    """
    Best-effort check for expired Azure Blob SAS URLs by inspecting the `se` query param.

    Returns True if:
      - There is an `se` param we can parse, and
      - It is strictly earlier than "now" in UTC.

    Otherwise returns False.
    """
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        se_vals = qs.get("se") or qs.get("se[]")
        if not se_vals:
            return False
        se_str = se_vals[0]
        # Typical format: 2023-11-09T05:58:00Z
        if se_str.endswith("Z"):
            se_str = se_str.replace("Z", "+00:00")
        expiry = datetime.fromisoformat(se_str)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return expiry < now
    except Exception:
        return False


def _get_auth_header(explicit_header: Optional[str]) -> str:
    """
    Return an XBL3.0 Authorization header value.

    If explicit_header is not None, it is returned as-is.
    Otherwise, xbl3auth is used to obtain a fresh token.
    """
    if explicit_header:
        return explicit_header

    if Xbl3AuthService is None or XblAuthConfig is None:
        raise RuntimeError("xbl3auth is not installed; cannot auto-obtain XBL3.0 token")

    _log("AUTH", "No header supplied; using xbl3auth to obtain XBL3.0 token.")
    config = XblAuthConfig()
    service = Xbl3AuthService(config, account_id="default")
    xbl3_token = service.get_xbl3_token()
    _log("AUTH", "Obtained XBL3.0 header via xbl3auth.")
    return xbl3_token


def _collect_locators(messages: Iterable[Mapping[str, Any]]) -> List[LocatorInfo]:
    locators: List[LocatorInfo] = []

    for msg in messages:
        content = msg.get("contentPayload", {}).get("content", {})
        parts = content.get("parts", []) or []
        for part in parts:
            if part.get("contentType") != "feedItem":
                continue
            locator_str = part.get("locator") or ""
            info = _parse_locator(locator_str)
            if info is not None:
                locators.append(info)

    return locators


def _collect_direct_images(messages: Iterable[Mapping[str, Any]]) -> List[str]:
    uris: List[str] = []
    seen: set[str] = set()
    for msg in messages:
        content = msg.get("contentPayload", {}).get("content", {})
        parts = content.get("parts", []) or []
        for part in parts:
            if part.get("contentType") != "image":
                continue
            uri = part.get("downloadUri") or ""
            if uri and uri not in seen:
                seen.add(uri)
                uris.append(uri)
    return uris


def _group_locators_by_xuid(
    locators: Iterable[LocatorInfo],
) -> Tuple[Dict[str, List[LocatorInfo]], Dict[str, List[LocatorInfo]]]:
    screenshots_by_xuid: Dict[str, List[LocatorInfo]] = {}
    clips_by_xuid: Dict[str, List[LocatorInfo]] = {}

    for info in locators:
        if info.kind == "screenshot":
            screenshots_by_xuid.setdefault(info.xuid, []).append(info)
        elif info.kind == "gameclip":
            clips_by_xuid.setdefault(info.xuid, []).append(info)

    return screenshots_by_xuid, clips_by_xuid


def _download_file(
    session: requests.Session,
    url: str,
    dest: Path,
    headers: Optional[Dict[str, str]] = None,
) -> None:
    _log("DL", f"GET {url}")
    resp = session.get(url, headers=headers, stream=True, timeout=60)
    _raise_for_status(resp)
    with dest.open("wb") as fp:
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            fp.write(chunk)


def _build_screenshot_index_for_xuid(
    session: requests.Session,
    xuid: str,
    auth_header: str,
) -> Dict[str, Mapping[str, Any]]:
    """
    Fetch recent screenshots for a given XUID and build a map:

        screenshot_id -> screenshot_json

    Note: This currently only fetches a single page (maxItems=1000).
    """
    url = f"{SCREENSHOTS_BASE}/users/xuid({xuid})/screenshots"
    params = {"skipItems": 0, "maxItems": 1000}
    headers = {
        "Authorization": auth_header,
        "x-xbl-contract-version": "5",
    }
    _log("META", f"GET {url}")
    resp = session.get(url, params=params, headers=headers, timeout=60)
    _raise_for_status(resp)
    data = resp.json()
    screenshots = data.get("screenshots") or []
    idx: Dict[str, Mapping[str, Any]] = {}
    for shot in screenshots:
        sid = shot.get("screenshotId") or shot.get("screenshot_id") or shot.get("screenshotid")
        if sid:
            idx[str(sid)] = shot
    _log("INFO", f"Indexed {len(idx)} screenshots for XUID {xuid}")
    return idx


def _build_gameclip_index_for_xuid(
    session: requests.Session,
    xuid: str,
    auth_header: str,
) -> Dict[str, Mapping[str, Any]]:
    """
    Fetch recent clips for a given XUID and build a map:

        game_clip_id -> clip_json

    Note: This currently only fetches a single page (maxItems=1000).
    """
    url = f"{GAMECLIPS_BASE}/users/xuid({xuid})/clips"
    params = {"skipItems": 0, "maxItems": 1000}
    headers = {
        "Authorization": auth_header,
        "x-xbl-contract-version": "1",
    }
    _log("META", f"GET {url}")
    resp = session.get(url, params=params, headers=headers, timeout=60)
    _raise_for_status(resp)
    data = resp.json()
    clips = data.get("gameClips") or data.get("game_clips") or []
    idx: Dict[str, Mapping[str, Any]] = {}
    for clip in clips:
        cid = clip.get("gameClipId") or clip.get("game_clip_id") or clip.get("id")
        if cid:
            idx[str(cid)] = clip
    _log("INFO", f"Indexed {len(idx)} clips for XUID {xuid}")
    return idx


def prefetch_media_for_messages(
    messages: Iterable[Mapping[str, Any]],
    out_dir: Path,
    xbl3_header: Optional[str] = None,
) -> Dict[str, str]:
    """
    Prefetch media referenced in a batch of messages.

    Args:
        messages: Iterable of message dicts from the conversation JSON.
        out_dir: Directory where media files will be saved. It is created if missing.
        xbl3_header: Optional Authorization header value
                    (e.g., "XBL3.0 x=<uhs>;<token>").
                    If omitted, xbl3auth is used to obtain one.

    Returns:
        Dict mapping logical keys to relative file paths, e.g.:

            {
                "image:https://...": "static/img_0001.jpg",
                "feedItem:screenshotsmetadata.xboxlive.com/...": "static/shot_0001.jpg",
                "feedItem:gameclipsmetadata.xboxlive.com/...": "static/clip_0001.mp4",
            }
    """
    out_dir = Path(out_dir)
    _ensure_dir(out_dir)
    base = out_dir.name

    auth_header = _get_auth_header(xbl3_header)
    headers_auth = {"Authorization": auth_header}

    session = requests.Session()

    # Collect feedItem locators and group by type/xuid
    locators = _collect_locators(messages)
    unique_locators = {loc.raw: loc for loc in locators}
    _log("INFO", f"Found {len(unique_locators)} unique feedItem locators")

    screenshots_by_xuid, clips_by_xuid = _group_locators_by_xuid(unique_locators.values())

    # Mapping from logical key -> relative path (relative to HTML file)
    media_map: Dict[str, str] = {}

    # First, handle direct image parts (no need for metadata)
    direct_images = _collect_direct_images(messages)
    for idx, uri in enumerate(direct_images, start=1):
        ext = _infer_extension_from_url(uri, default="jpg")
        filename = f"image_{idx:04d}.{ext}"
        dest = out_dir / filename
        key = f"image:{uri}"

        # If SAS is obviously expired, don't even bother.
        if _sas_is_expired(uri):
            _log("INFO", f"Skipping expired direct image SAS URL: {uri}")
            continue

        if dest.exists():
            media_map[key] = f"{base}/{dest.name}"
            continue
        try:
            _download_file(session, uri, dest, headers=None)
            media_map[key] = f"{base}/{dest.name}"
        except Exception as exc:
            _log("WARN", f"Failed to download direct image {uri}: {exc}")

    # Screenshots
    for xuid, infos in screenshots_by_xuid.items():
        index = _build_screenshot_index_for_xuid(session, xuid, auth_header)
        for info in infos:
            key = f"feedItem:{info.raw}"
            shot = index.get(info.item_id)
            if not shot:
                _log("WARN", f"No screenshot metadata found for locator: {info.raw}")
                continue

            uris = shot.get("screenshotUris") or shot.get("screenshot_uris") or []
            if not uris:
                _log("WARN", f"Screenshot has no URIs: locator={info.raw}")
                continue

            # Prefer a full-resolution download URI if possible
            chosen = None
            for entry in uris:
                uri_type = entry.get("uriType") or entry.get("uri_type") or ""
                if "download" in uri_type.lower():
                    chosen = entry
                    break
            if chosen is None:
                chosen = uris[0]

            url = chosen.get("uri")
            if not url:
                _log("WARN", f"Screenshot URI entry missing 'uri' for locator: {info.raw}")
                continue

            ext = _infer_extension_from_url(url, default="jpg")
            filename = f"screenshot_{info.item_id}.{ext}"
            dest = out_dir / filename
            if dest.exists():
                media_map[key] = f"{base}/{dest.name}"
                continue
            try:
                _download_file(session, url, dest, headers=headers_auth)
                media_map[key] = f"{base}/{dest.name}"
            except Exception as exc:
                _log("WARN", f"Failed to download screenshot for locator {info.raw}: {exc}")

    # Game clips
    for xuid, infos in clips_by_xuid.items():
        index = _build_gameclip_index_for_xuid(session, xuid, auth_header)
        for info in infos:
            key = f"feedItem:{info.raw}"
            clip = index.get(info.item_id)
            if not clip:
                _log("WARN", f"No clip metadata found for locator: {info.raw}")
                continue

            uris = clip.get("gameClipUris") or clip.get("game_clip_uris") or []
            if not uris:
                _log("WARN", f"Clip has no URIs: locator={info.raw}")
                continue

            chosen = None
            for entry in uris:
                uri_type = entry.get("uriType") or entry.get("uri_type") or ""
                if "download" in uri_type.lower():
                    chosen = entry
                    break
            if chosen is None:
                chosen = uris[0]

            url = chosen.get("uri")
            if not url:
                _log("WARN", f"Clip URI entry missing 'uri' for locator: {info.raw}")
                continue

            ext = _infer_extension_from_url(url, default="mp4")
            filename = f"clip_{info.item_id}.{ext}"
            dest = out_dir / filename
            if dest.exists():
                media_map[key] = f"{base}/{dest.name}"
                continue
            try:
                _download_file(session, url, dest, headers=headers_auth)
                media_map[key] = f"{base}/{dest.name}"
            except Exception as exc:
                _log("WARN", f"Failed to download clip for locator {info.raw}: {exc}")

    # Optionally write an index file for debugging
    try:
        index_path = out_dir / "media_index.json"
        with index_path.open("w", encoding="utf-8") as fp:
            json.dump(media_map, fp, indent=2, sort_keys=True)
    except Exception as exc:
        _log("WARN", f"Failed to write media_index.json: {exc}")

    return media_map
