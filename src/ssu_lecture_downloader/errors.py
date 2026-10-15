from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from curl_cffi.requests import exceptions as curl_exceptions

NETWORK_ERROR = "network_error"
NETWORK_TIMEOUT = "network_timeout"

# Hosts that own the LMS login session. A 401/403 from one of these means the
# LMS session is gone; the same status from a CDN host (for example an expired
# signed Commons URL) says nothing about the login session.
LMS_SESSION_HOSTS = frozenset({"canvas.ssu.ac.kr", "lms.ssu.ac.kr", "smartid.ssu.ac.kr"})


@dataclass(frozen=True)
class LoginFlowError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def response_status(exc: BaseException) -> int | None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def response_host(exc: BaseException) -> str | None:
    url: Any = getattr(getattr(exc, "response", None), "url", None)
    if not url:
        return None
    try:
        return urlsplit(str(url)).hostname
    except ValueError:
        return None


def is_lms_auth_failure(exc: BaseException) -> bool:
    """True for a 401/403 that plausibly means the LMS login session expired.

    A response whose host is known and is not an LMS host (CDN, Commons media
    storage) is not an LMS session failure. When the host is unknown we stay
    conservative and treat it as one.
    """
    if response_status(exc) not in {401, 403}:
        return False
    host = response_host(exc)
    return host is None or host in LMS_SESSION_HOSTS


def is_signed_url_failure(exc: BaseException) -> bool:
    """True for a 401/403/410 from a non-LMS host, i.e. an expired signed media URL."""
    if response_status(exc) not in {401, 403, 410}:
        return False
    host = response_host(exc)
    return host is not None and host not in LMS_SESSION_HOSTS


def classify_network_error(exc: BaseException) -> str | None:
    """Return network_timeout/network_error for transport-level failures, else None."""
    if isinstance(exc, (curl_exceptions.Timeout, TimeoutError)):
        return NETWORK_TIMEOUT
    if isinstance(exc, curl_exceptions.HTTPError):
        status = response_status(exc)
        return NETWORK_ERROR if status is not None and status >= 500 else None
    if isinstance(exc, (curl_exceptions.ConnectionError, ConnectionError)):
        return NETWORK_ERROR
    if isinstance(exc, curl_exceptions.RequestException):
        # Remaining curl-level failures (proxy, TLS, incomplete read, ...). Pure
        # client mistakes such as an invalid URL or a JSON decode problem are not
        # transport failures.
        if isinstance(exc, (curl_exceptions.InvalidURL, curl_exceptions.InvalidSchema, curl_exceptions.MissingSchema, curl_exceptions.InvalidJSONError)):
            return None
        return NETWORK_ERROR
    return None
