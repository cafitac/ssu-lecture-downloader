#!/usr/bin/env python3
"""Check that a release's latest.json is signed by the key the app trusts.

Tauri's updater signatures and public keys are base64-wrapped minisign files. Both carry the
8-byte key ID of the signing key (public key line: `Ed` + key id + key; signature line: `Ed` +
key id + signature). Comparing the two IDs catches a wrong or rotated TAURI_SIGNING_PRIVATE_KEY
secret before the release is published; it does not verify the signature itself (the client does).

Usage: python scripts/check_updater_signature.py LATEST_JSON TAURI_CONF_JSON [PLATFORM ...]
PLATFORM defaults to darwin-x86_64 and darwin-x86_64-app. Exit 1 with one line per problem.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from pathlib import Path

DEFAULT_PLATFORMS = ("darwin-x86_64", "darwin-x86_64-app")
_ALGORITHMS = (b"Ed", b"ED")


class SignatureError(Exception):
    pass


def minisign_key_id(wrapped: str) -> str:
    """Key ID (hex) of a base64-wrapped minisign public key or signature file."""
    try:
        text = base64.b64decode(wrapped.strip(), validate=True).decode("utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        payload = next(line for line in lines if not line.startswith(("untrusted comment:", "trusted comment:")))
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, UnicodeDecodeError, StopIteration, ValueError) as error:
        raise SignatureError("not a base64-wrapped minisign file") from error
    if len(raw) < 10 or raw[:2] not in _ALGORITHMS:
        raise SignatureError("unexpected minisign payload")
    return raw[2:10].hex()


def check(latest: dict, conf: dict, platforms: tuple[str, ...] = DEFAULT_PLATFORMS) -> list[str]:
    problems: list[str] = []
    try:
        trusted = minisign_key_id(conf["plugins"]["updater"]["pubkey"])
    except (KeyError, TypeError, SignatureError) as error:
        return [f"tauri.conf.json plugins.updater.pubkey: unusable ({error})"]
    entries = latest.get("platforms") if isinstance(latest, dict) else None
    for platform in platforms:
        entry = entries.get(platform) if isinstance(entries, dict) else None
        signature = entry.get("signature") if isinstance(entry, dict) else None
        if not isinstance(signature, str) or not signature:
            problems.append(f"{platform}: no signature")
            continue
        try:
            signed_by = minisign_key_id(signature)
        except SignatureError as error:
            problems.append(f"{platform}: signature unreadable ({error})")
            continue
        if signed_by != trusted:
            problems.append(f"{platform}: signed with key id {signed_by}, the app trusts {trusted}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        latest = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        conf = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    platforms = tuple(argv[2:]) or DEFAULT_PLATFORMS
    problems = check(latest, conf, platforms)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems:
        print(f"signature key id matches the app's public key for: {', '.join(platforms)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
