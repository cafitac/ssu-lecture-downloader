from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<redacted>"

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-csrf-token",
    "x-xsrf-token",
}

SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "cookie",
    "authorization",
    "private_key",
    "cryption",
    "credential",
    "ticket",
    "saml",
    "username",
    "user_id",
    "userid",
    "idno",
    "sidno",
    "ssu_username",
    "ssu_password",
)

SAFE_QUERY_KEYS = {"course_id", "module_id", "id", "page", "per_page", "include_detail"}


def is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    if normalized in SAFE_QUERY_KEYS:
        return False
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def redact_headers(headers: Mapping[str, object]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in headers.items():
        if str(key).lower() in {"location", "referer", "referrer"} and isinstance(value, str):
            redacted[str(key)] = redact_url(value)
        elif str(key).lower() in SENSITIVE_HEADER_NAMES or is_sensitive_key(key):
            redacted[str(key)] = REDACTED
        else:
            redacted[str(key)] = value
    return redacted


def redact_url(url: str) -> str:
    sanitized_url = "".join(ch for ch in url if ch >= " " and ch != "\x7f")
    split = urlsplit(sanitized_url)
    query_pairs = parse_qsl(split.query, keep_blank_values=True)
    redacted_pairs = []
    for key, value in query_pairs:
        if is_sensitive_key(key):
            redacted_pairs.append((key, REDACTED))
        elif value.startswith(("http://", "https://")):
            redacted_pairs.append((key, redact_url(value)))
        else:
            redacted_pairs.append((key, value))
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(redacted_pairs), split.fragment))


def redact_mapping(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[object, object] = {}
        for key, child in value.items():
            result[key] = REDACTED if is_sensitive_key(key) else redact_mapping(child)
        return result
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_mapping(item) for item in value)
    return value
