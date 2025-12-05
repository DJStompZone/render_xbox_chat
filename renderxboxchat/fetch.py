#!/usr/bin/env python3
"""
Fetch helpers for renderxboxchat.

Responsibilities:
  - Resolve an XBL3.0 Authorization header (explicit or via xbl3auth).
  - Fetch the signed-in user's XUID from profile.xboxlive.com.
  - Enumerate conversations via the inbox endpoint.
  - For each conversation:
      * Try each participant XUID against the history endpoint
        /network/Xbox/users/me/conversations/users/xuid({xuid})
      * Follow continuationToken to fetch all pages with messages.
  - Store each full conversation under:

        <out_root>/
          full_conv_<remote_xuid>/
            page_000.json
            page_001.json
            ...

  - Return a list of (remote_xuid, conversation_dir) for rendering.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from tqdm import tqdm

try:
    from xbl3auth import Xbl3AuthService, XblAuthConfig
except Exception:  # pragma: no cover - optional at runtime
    Xbl3AuthService = None  # type: ignore[assignment]
    XblAuthConfig = None  # type: ignore[assignment]


BASE_MSG_URL = "https://xblmessaging.xboxlive.com"
PROFILE_URL = "https://profile.xboxlive.com/users/me/profile/settings"

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
TIMEOUT_SECONDS = 15.0


@dataclass
class ConversationMeta:
    """Minimal metadata for a discovered conversation."""
    conversation_id: str
    participants: List[str]
    raw: Dict[str, Any]


def _log(level: str, msg: str) -> None:
    """Lightweight log helper to keep output consistent."""
    print(f"[{level}] {msg}")


def _ensure_dir(path: Path) -> None:
    """Create a directory (and parents) if it does not exist."""
    path.mkdir(parents=True, exist_ok=True)


def _sleep_with_jitter(base_delay: float) -> None:
    """Sleep for base_delay plus a bit of jitter to avoid thundering herd."""
    jitter = random.uniform(0.0, base_delay * 0.25)
    delay = min(base_delay + jitter, MAX_BACKOFF_SECONDS)
    time.sleep(delay)


def _request_with_backoff(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_retries: int = MAX_RETRIES,
    **kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request with 429/5xx-aware backoff.

    Args:
        session: Requests session to use.
        method: HTTP method (for example, "GET").
        url: Target URL.
        max_retries: Max retries for 429 or transient 5xx errors.
        **kwargs: Passed through to session.request().

    Returns:
        A successful Response object.

    Raises:
        requests.HTTPError: If a non-retryable HTTP error occurs.
        RuntimeError: If rate limiting persists after max_retries attempts.
    """
    attempt = 0
    backoff = BASE_BACKOFF_SECONDS

    while True:
        attempt += 1
        resp = session.request(method, url, timeout=TIMEOUT_SECONDS, **kwargs)

        # 429 Too Many Requests: obey Retry-After if present, otherwise backoff.
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = backoff
            else:
                delay = backoff

            if attempt > max_retries:
                raise RuntimeError(
                    f"Exceeded max_retries for 429 on {url} after {attempt - 1} retries"
                )

            _log(
                "429",
                f"Too Many Requests on {url}, sleeping {delay:.2f}s "
                f"(attempt {attempt}/{max_retries})",
            )
            time.sleep(delay)
            backoff = min(backoff * 2.0, MAX_BACKOFF_SECONDS)
            continue

        # Transient 5xx: exponential backoff.
        if 500 <= resp.status_code < 600:
            if attempt > max_retries:
                resp.raise_for_status()
            _log(
                str(resp.status_code),
                f"Transient error on {url}, retrying "
                f"(attempt {attempt}/{max_retries})",
            )
            _sleep_with_jitter(backoff)
            backoff = min(backoff * 2.0, MAX_BACKOFF_SECONDS)
            continue

        return resp


def _get_auth_header(explicit_header: Optional[str]) -> str:
    """Resolve an XBL3.0 Authorization header.

    If explicit_header is given, it is returned as-is.
    Otherwise xbl3auth is used to obtain a fresh XBL3.0 token.
    """
    if explicit_header:
        return explicit_header

    if Xbl3AuthService is None or XblAuthConfig is None:
        raise RuntimeError("xbl3auth is not installed; cannot auto-obtain XBL3.0 token")

    _log("AUTH", "No header supplied; using xbl3auth to obtain XBL3.0 token.")
    config = XblAuthConfig()
    service = Xbl3AuthService(config, account_id="default")
    token = service.get_xbl3_token()
    _log("AUTH", "Obtained XBL3.0 header via xbl3auth.")
    return token


def fetch_own_xuid(auth_header: str) -> str:
    """Fetch the signed-in user's own XUID from profile.xboxlive.com."""
    headers = {
        "Authorization": auth_header,
        "x-xbl-contract-version": "2",
    }

    _log("META", f"GET {PROFILE_URL} to fetch own XUID")
    resp = requests.get(PROFILE_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    _log("DEBUG", f"Profile settings response: {data}")

    profile_users = data.get("profileUsers") or []
    if not profile_users:
        raise RuntimeError("profileUsers missing in profile settings response")

    xuid = profile_users[0].get("id")
    if xuid:
        _log("DEBUG", f"Fetched XUID from profileUsers: {xuid}")
        return str(xuid)

    raise RuntimeError("Could not find XUID in profile settings response")


def _fetch_inbox(
    session: requests.Session,
    token: str,
    max_items: int,
) -> Dict[str, Any]:
    """Fetch the inbox listing used to discover conversation IDs and participants."""
    headers = {
        "Authorization": token,
        "x-xbl-contract-version": "1",
        "Accept": "application/json",
    }
    params: Dict[str, Any] = {"maxItems": max_items}
    url = f"{BASE_MSG_URL}/network/Xbox/users/me/inbox"

    _log("META", f"GET {url} params={params}")
    resp = _request_with_backoff(session, "GET", url, headers=headers, params=params)

    try:
        resp.raise_for_status()
        # if hasattr(resp, "json") and callable(resp.json):
        #     _log("DEBUG", f"Inbox response: {resp.json()}")
    except requests.HTTPError as exc:
        _log("ERROR", "Error calling inbox endpoint:")
        _log("ERROR", f"  URL:    {resp.url}")
        _log("ERROR", f"  Status: {resp.status_code}")
        _log("ERROR", f"  Body:   {resp.text[:500]}")
        raise exc

    return resp.json()


def _load_conversations_from_inbox(
    session: requests.Session,
    token: str,
    max_items: int,
) -> List[ConversationMeta]:
    """Return a list of ConversationMeta discovered from the inbox."""
    data = _fetch_inbox(session=session, token=token, max_items=max_items)
    primary = data.get("primary") or {}
    convos = primary.get("conversations") or []

    metas: List[ConversationMeta] = []
    for c in convos:
        cid = c.get("conversationId")
        participants = [str(p) for p in (c.get("participants") or [])]
        if cid is None:
            continue
        metas.append(
            ConversationMeta(
                conversation_id=str(cid),
                participants=participants,
                raw=c,
            )
        )

    _log("INFO", f"Loaded {len(metas)} conversations from inbox.")
    if metas:
        _log("DEBUG", "Sample inbox conversation:")
        _log("DEBUG", json.dumps(metas[0].raw, indent=2)[:600])
    return metas


def _history_url_for_xuid(xuid: str) -> str:
    """Build the history endpoint URL for a given remote XUID.

    Mirrors xbox.webapi.api.provider.message.MessageProvider.get_conversation():
      GET /network/Xbox/users/me/conversations/users/xuid({xuid})
    """
    return f"{BASE_MSG_URL}/network/Xbox/users/me/conversations/users/xuid({xuid})"


def _pages_have_messages(pages: List[Dict[str, Any]]) -> bool:
    """Return True if any page in pages has a non-empty messages[] array."""
    for page in pages:
        msgs = page.get("messages") or []
        if msgs:
            return True
    return False


def fetch_conversation_pages_for_xuid(
    session: requests.Session,
    token: str,
    xuid: str,
    max_items: int,
    max_pages: int,
) -> List[Dict[str, Any]]:
    """Fetch one or more ConversationResponse pages for a given remote XUID.

    Args:
        session: Requests session.
        token: XBL3.0 token string (Authorization header value).
        xuid: Remote user's XUID.
        max_items: maxItems per page.
        max_pages: Safety cap on how many pages to fetch.

    Returns:
        List of JSON pages as returned by the Xbox messaging endpoint.
    """
    headers = {
        "Authorization": token,
        "x-xbl-contract-version": "1",
        "Accept": "application/json",
    }

    pages: List[Dict[str, Any]] = []
    continuation: Optional[str] = None
    page_index = 0

    while page_index < max_pages:
        params: Dict[str, Any] = {"maxItems": max_items}
        if continuation:
            params["continuationToken"] = continuation

        url = _history_url_for_xuid(xuid)
        _log("META", f"GET {url} (page {page_index}) params={params}")
        resp = _request_with_backoff(session, "GET", url, headers=headers, params=params)
        _log("META", f"[{resp.status_code}] {url} (page {page_index})")

        try:
            resp.raise_for_status()
            # if hasattr(resp, "json") and callable(resp.json):
            #     _log("DEBUG", f"History response page {page_index}: {resp.json()}")
            # else:
            #     _log("DEBUG", f"History response page {page_index}: <non-JSON body> {resp.text}")
        except requests.HTTPError as exc:
            _log("ERROR", f"  Error body: {resp.text[:500]}")
            raise exc

        try:
            data = resp.json()
        except ValueError:
            data = {"_non_json_body": resp.text}

        pages.append(data)

        continuation = (
            data.get("continuationToken")
            or data.get("continuation_token")
            or None
        )
        if not continuation:
            break

        page_index += 1

    return pages


def fetch_all_conversations(
    out_root: Path,
    self_xuid: str|None = None,
    *,
    xbl3_header: Optional[str] = None,
    max_items_inbox: int = 200,
    max_items_history: int = 100,
    max_pages_history: int = 1000,
    limit_conversations: Optional[int] = None,
) -> List[Tuple[str, Path]]:
    """Fetch all available conversation histories for the signed-in user.

    This function:
      - Obtains an XBL3.0 Authorization header.
      - Fetches the inbox to discover conversationIds and participants.
      - For each conversation:
          * Picks candidate participant XUIDs (excluding self_xuid where possible).
          * For each candidate XUID:
              - Calls the XUID-based history endpoint.
              - Accepts the first candidate that yields messages.
              - Writes all pages for that XUID under:

                    out_root / f"full_conv_{xuid}" / page_XXX.json

      - Optionally stops after limit_conversations have been successfully
        fetched; by default processes all inbox conversations.

    Args:
        out_root: Root directory to store per-conversation folders.
        self_xuid: XUID of the signed-in user.
        xbl3_header: Optional XBL3.0 Authorization header value. If None,
            xbl3auth is used to obtain one.
        max_items_inbox: maxItems to request from the inbox listing.
        max_items_history: maxItems per history page.
        max_pages_history: Maximum number of history pages per conversation.
        limit_conversations: Optional cap on how many conversations to fetch.
            If None, all discovered conversations are processed.

    Returns:
        List of (remote_xuid, conversation_dir) for each successfully fetched
        conversation.
    """
    out_root = Path(out_root)
    _ensure_dir(out_root)

    auth_header = _get_auth_header(xbl3_header)

    if not self_xuid:
        self_xuid = fetch_own_xuid(auth_header)
        _log("INFO", f"Detected self XUID: {self_xuid}")

    token = auth_header
    session = requests.Session()

    conv_metas = _load_conversations_from_inbox(
        session=session,
        token=token,
        max_items=max_items_inbox,
    )

    if limit_conversations is None or limit_conversations <= 0:
        limit_convs = len(conv_metas)
    else:
        limit_convs = min(limit_conversations, len(conv_metas))

    _log(
        "INFO",
        f"Scanning up to {limit_convs} conversation(s) for full history "+
        f"(inbox has {len(conv_metas)}).",
    )

    results: List[Tuple[str, Path]] = []
    full_convos_dumped = 0
    seen_xuids: set[str] = set()

    for idx, meta in enumerate(
        tqdm(conv_metas, desc="Conversations", unit="conv"), start=1
    ):
        if full_convos_dumped >= limit_convs:
            break

        cid = meta.conversation_id
        participants = meta.participants

        if not participants:
            _log("INFO", f"[{idx}] conversationId={cid}: no participants, skipping.")
            continue

        _log("INFO", f"[{idx}] conversationId={cid}: participants={participants}")

        candidates: List[str] = [
            p for p in participants if str(p) != str(self_xuid)
        ] or participants

        success_for_this_convo = False

        for p in candidates:
            if p in seen_xuids:
                _log("DEBUG", f"  xuid {p}: already fetched, skipping duplicate.")
                success_for_this_convo = True
                break

            try:
                pages = fetch_conversation_pages_for_xuid(
                    session=session,
                    token=token,
                    xuid=p,
                    max_items=max_items_history,
                    max_pages=max_pages_history,
                )
            except requests.HTTPError as exc:
                resp = exc.response
                if resp is not None and resp.status_code == 404:
                    _log(
                        "INFO",
                        f"  xuid {p}: 404 (no direct convo?), trying next participant.",
                    )
                    continue
                _log("ERROR", f"  xuid {p}: fatal HTTP error: {exc}")
                break

            if not pages or not _pages_have_messages(pages):
                _log("INFO", f"  xuid {p}: no messages returned, trying next participant.")
                continue

            conv_dir = out_root / f"full_conv_{p}"
            _ensure_dir(conv_dir)

            for page_index, page in enumerate(pages):
                out_path = conv_dir / f"page_{page_index:03d}.json"
                out_path.write_text(json.dumps(page, indent=2), encoding="utf-8")
                _log("INFO", f"  wrote {out_path}")

            results.append((p, conv_dir))
            seen_xuids.add(p)
            full_convos_dumped += 1
            success_for_this_convo = True
            break

        if not success_for_this_convo:
            _log(
                "WARN",
                f"  !! Could not resolve history with messages for conversationId={cid}",
            )

    _log(
        "INFO",
        f"Done. Dumped {full_convos_dumped} conversation(s) with messages.",
    )
    return results
