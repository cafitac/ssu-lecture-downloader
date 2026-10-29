"""Canvas wiki-page attachments: pure parsing helpers.

Course pages ("1주차 강의자료") carry their files as links in the page body:
`<a class="instructure_file_link" href="/courses/7/files/123/download?download_frd=1"
data-api-endpoint="https://canvas.ssu.ac.kr/api/v1/courses/7/files/123">`. The service fetches
the page body through the Canvas API with the authenticated session; this module only finds
the file ids in that HTML and reads Canvas JSON.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

CANVAS_ORIGIN = "https://canvas.ssu.ac.kr"
# A page with more attachments than this is cut off (a lecture page has a handful).
MAX_PAGE_FILES = 50

_FILE_PATH = re.compile(r"/files/(\d+)(?:/|$)")
_PAGE_SLUG = re.compile(r"/pages/([^/?#]+)")
_JSON_GUARD = "while(1);"


@dataclass(frozen=True)
class PageFileLink:
    file_id: str
    link_text: str


def _is_same_site(host: str | None) -> bool:
    if not host:
        return True  # relative link
    host = host.lower()
    return host == "ssu.ac.kr" or host.endswith(".ssu.ac.kr")


def file_id_from_address(address: str | None) -> str | None:
    """The Canvas file id in a link or API address, or None (other hosts and folders are ignored)."""
    if not address:
        return None
    try:
        split = urlsplit(address.strip())
    except ValueError:
        return None
    if split.scheme not in ("", "http", "https") or not _is_same_site(split.hostname):
        return None
    match = _FILE_PATH.search(split.path)
    return match.group(1) if match else None


def extract_file_links(html: str, limit: int = MAX_PAGE_FILES) -> list[PageFileLink]:
    """File links of a page body in document order, one per file id.

    Only links count (`<a href>` or an element carrying `data-api-endpoint`); images embedded
    in the text are not attachments.
    """
    if not html:
        return []
    from bs4 import BeautifulSoup  # imported lazily to keep API start-up light

    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, str] = {}
    for tag in soup.find_all(lambda candidate: candidate.name == "a" or candidate.has_attr("data-api-endpoint")):
        file_id = file_id_from_address(tag.get("data-api-endpoint")) or (
            file_id_from_address(tag.get("href")) if tag.name == "a" else None
        )
        if file_id is None:
            continue
        text = " ".join(tag.get_text(" ", strip=True).split())
        if file_id not in found or (not found[file_id] and text):
            found[file_id] = text
        if len(found) >= limit:
            break
    return [PageFileLink(file_id=file_id, link_text=text) for file_id, text in found.items()]


def wiki_page_ref(*, url: Any, page_url: Any, resource_id: Any) -> str | None:
    """What the Canvas pages API takes to identify a wiki page (`url_or_id`).

    The module item may carry the page slug (`page_url`), an address ending in `/pages/<slug>`,
    or only the numeric page id.
    """
    if isinstance(page_url, str) and page_url.strip():
        return unquote(page_url.strip())
    if isinstance(url, str):
        match = _PAGE_SLUG.search(urlsplit(url.strip()).path)
        if match:
            return unquote(match.group(1))
    if resource_id is not None and str(resource_id).strip().isdigit():
        return str(resource_id).strip()
    return None


def parse_canvas_json(response: Any) -> Any:
    """JSON of a Canvas API response; Canvas may prefix it with `while(1);`."""
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.lstrip().startswith(_JSON_GUARD):
        return json.loads(text.lstrip()[len(_JSON_GUARD):])
    return response.json()
