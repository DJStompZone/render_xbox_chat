# load static files for HTML rendering
from pathlib import Path
from dataclasses import dataclass

@dataclass
class StaticFiles:
    name: str
    filename: str
    content: str = ""

    def load(self) -> str:
        path = Path(__file__).parent.parent / "static" / self.filename
        return path.read_text(encoding="utf-8")

filenames = {
    "document_head": "head.partial.html",
    "search_component": "search.partial.html",
    "nav_component": "nav.partial.html",
    "sort_component": "sort.partial.html",
    "javascript_main": "script.js",
    "__template": "template.partial.html",
}

__template, document_head, search_component, nav_component, sort_component, javascript_main = (
    StaticFiles(name, filename).load()
    for name, filename in filenames.items()
)

__all__ = [
    "__template",
    "document_head",
    "search_component",
    "nav_component",
    "sort_component",
    "javascript_main",
]
