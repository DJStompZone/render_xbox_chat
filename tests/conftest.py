from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from requests.structures import CaseInsensitiveDict

# Ensure the project root is importable when tests run without installation.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeResponse:
    """A minimal fake response compatible with tests.

    Attributes:
        status_code: HTTP status code to expose.
        _json_data: If set, returned from json(); otherwise json() raises ValueError.
        _content: Raw bytes for iter_content.
        headers: CaseInsensitiveDict of headers.
        url: The URL associated with the response.
    """

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
        headers: Optional[dict[str, str]] = None,
        url: str = "https://example.test",
    ) -> None:
        super().__init__()
        self.status_code = status_code
        self._json_data = json_data
        self._content = text.encode()
        self.headers = CaseInsensitiveDict(headers or {})
        self.url = url

    def json(self, **_: Any) -> Any:  # type: ignore[override]
        if self._json_data is None:
            raise ValueError("No JSON data set on FakeResponse")
        return self._json_data

    def iter_content(self, chunk_size: int = 1) -> Iterable[bytes]:
        content = self._content or b""
        for idx in range(0, len(content), chunk_size):
            yield content[idx : idx + chunk_size]


class SequenceSession:
    """A simple session stub that returns a sequence of responses."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:  # pragma: no cover - delegated
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("No more responses available")
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:  # pragma: no cover - delegated
        return self.request("GET", url, **kwargs)


def make_response_sequence(factory: Callable[[int], FakeResponse], count: int) -> list[FakeResponse]:
    return [factory(i) for i in range(count)]
