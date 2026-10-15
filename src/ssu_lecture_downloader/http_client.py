from __future__ import annotations

from time import perf_counter
from typing import Any

from curl_cffi import requests

from ssu_lecture_downloader.trace import NetworkTrace, TraceEntry

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


class HttpClient:
    def __init__(self, session: requests.Session | None = None, trace: NetworkTrace | None = None) -> None:
        self.session = session or requests.Session(impersonate="chrome124")
        self.session.headers.update(DEFAULT_HEADERS)
        self.trace = trace or NetworkTrace()

    @property
    def cookies(self):
        return self.session.cookies

    def get(self, step: str, url: str, **kwargs: Any) -> requests.Response:
        return self.request(step, "GET", url, **kwargs)

    def post(self, step: str, url: str, **kwargs: Any) -> requests.Response:
        return self.request(step, "POST", url, **kwargs)

    def request(self, step: str, method: str, url: str, **kwargs: Any) -> requests.Response:
        started = perf_counter()
        response = self.session.request(method, url, **kwargs)
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        self.trace.add(
            TraceEntry(
                step=step,
                method=method.upper(),
                url=url,
                status_code=response.status_code,
                final_url=str(response.url),
                elapsed_ms=elapsed_ms,
                request_headers=dict(response.request.headers) if response.request else {},
                response_headers=dict(response.headers),
                cookie_names=_set_cookie_names(response.headers.get("Set-Cookie")),
                body_size=len(response.content or b""),
            )
        )
        return response


def _set_cookie_names(set_cookie_header: str | None) -> list[str]:
    if not set_cookie_header:
        return []
    names: list[str] = []
    for part in set_cookie_header.split(","):
        cookie_pair = part.strip().split(";", 1)[0]
        if "=" in cookie_pair:
            names.append(cookie_pair.split("=", 1)[0])
    return names
