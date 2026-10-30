#!/usr/bin/env python3
"""Set, show or check the app version across every file that carries it.

The version lives in four manifests and three lockfiles that must agree:

  frontend/src-tauri/tauri.conf.json   the version the updater compares (`latest.json`)
  frontend/src-tauri/Cargo.toml        + Cargo.lock (the package's own entry)
  frontend/package.json                + package-lock.json (root entries)
  pyproject.toml                       + uv.lock (the project's own entry; `UV_LOCKED=1` fails if stale)

Usage:
  python scripts/bump_version.py 0.2.0          set every file to 0.2.0
  python scripts/bump_version.py --check 0.2.0  exit 1 unless every file already says 0.2.0;
                                                on success prints only the normalized x.y.z (stdout)
  python scripts/bump_version.py --show         print each file's version

A single leading `v` is accepted (`v0.2.0`; `vv0.2.0` is rejected). This script is the one place
that strips it: callers pass the raw tag or input and read the normalized version from `--check`. Only plain `x.y.z` versions are allowed: the release tag is
`v<x.y.z>` and pre-release suffixes would never become the updater's `latest`. Edits are textual
(formatting is preserved) and all-or-nothing: nothing is written if any file fails to match.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VERSION_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_VERSION_VALUE = r'(?P<version>[^"\n]+)'


@dataclass(frozen=True)
class Target:
    path: str  # relative to the repository root
    pattern: re.Pattern[str]  # group "version" is the value; the match must be exactly `count` times
    count: int = 1


def _section_version(header: str) -> re.Pattern[str]:
    """`version = "x"` that belongs to the TOML table `[header]` (before the next table)."""
    return re.compile(
        rf'(?ms)^\[{re.escape(header)}\][ \t]*\n(?:(?!\[)[^\n]*\n)*?version[ \t]*=[ \t]*"{_VERSION_VALUE}"'
    )


def _locked_package(name: str) -> re.Pattern[str]:
    """A lockfile `[[package]]` entry: `name = "<name>"` followed directly by `version = "x"`."""
    return re.compile(rf'(?m)^name = "{re.escape(name)}"\nversion = "{_VERSION_VALUE}"')


TARGETS: tuple[Target, ...] = (
    Target(
        "frontend/src-tauri/tauri.conf.json",
        re.compile(rf'(?m)^  "version": "{_VERSION_VALUE}"'),
    ),
    Target("frontend/src-tauri/Cargo.toml", _section_version("package")),
    Target("frontend/src-tauri/Cargo.lock", _locked_package("ssu-lecture-downloader")),
    Target("frontend/package.json", re.compile(rf'(?m)^  "version": "{_VERSION_VALUE}"')),
    # package-lock.json: top-level "version" and packages[""].version, both right after the name.
    Target(
        "frontend/package-lock.json",
        re.compile(rf'(?m)^(?: {{2}}| {{6}})"version": "{_VERSION_VALUE}",\n(?=(?: {{2}}| {{6}})"(?:lockfileVersion|dependencies))'),
        count=2,
    ),
    Target("pyproject.toml", _section_version("project")),
    Target("uv.lock", _locked_package("ssu-lecture-downloader")),
)


class BumpError(Exception):
    pass


def normalize(version: str) -> str:
    candidate = version[1:] if version.startswith("v") else version
    if not VERSION_RE.fullmatch(candidate):
        raise BumpError(f"invalid version {version!r}: expected x.y.z (optionally prefixed with v), e.g. 0.2.0")
    return candidate


def _read(root: Path, target: Target) -> str:
    path = root / target.path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise BumpError(f"{target.path}: cannot read ({error.strerror or error})") from error


def _matches(root: Path, target: Target, text: str) -> list[re.Match[str]]:
    found = list(target.pattern.finditer(text))
    if len(found) != target.count:
        raise BumpError(f"{target.path}: expected {target.count} version field(s), found {len(found)}")
    return found


def read_versions(root: Path = ROOT) -> dict[str, list[str]]:
    versions: dict[str, list[str]] = {}
    for target in TARGETS:
        found = _matches(root, target, _read(root, target))
        versions[target.path] = [match.group("version") for match in found]
    return versions


def check_versions(version: str, root: Path = ROOT) -> list[str]:
    """Return one problem line per file that does not carry exactly `version`."""
    expected = normalize(version)
    problems = []
    for path, values in read_versions(root).items():
        wrong = sorted({value for value in values if value != expected})
        if wrong:
            problems.append(f"{path}: {', '.join(wrong)} (expected {expected})")
    return problems


def set_versions(version: str, root: Path = ROOT) -> list[str]:
    """Rewrite every target to `version`. Returns the relative paths whose content changed."""
    new_version = normalize(version)
    updates: dict[Path, str] = {}
    for target in TARGETS:
        text = _read(root, target)
        found = _matches(root, target, text)
        for match in reversed(found):
            start, end = match.span("version")
            text = text[:start] + new_version + text[end:]
        updates[root / target.path] = text
    changed = []
    for path, text in updates.items():
        if path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(str(path.relative_to(root)))
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set, show or check the app version in every manifest.")
    parser.add_argument("version", nargs="?", help="version to set, e.g. 0.2.0")
    parser.add_argument("--check", metavar="VERSION", help="exit 1 unless every file already carries VERSION")
    parser.add_argument("--show", action="store_true", help="print each file's version")
    args = parser.parse_args(argv)
    chosen = sum([args.version is not None, args.check is not None, args.show])
    if chosen != 1:
        parser.error("give exactly one of: a version to set, --check VERSION, --show")
    try:
        if args.show:
            for path, values in read_versions(ROOT).items():
                print(f"{path}: {', '.join(values)}")
        elif args.check is not None:
            problems = check_versions(args.check, ROOT)
            if problems:
                print(f"version mismatch with {normalize(args.check)}:", file=sys.stderr)
                for problem in problems:
                    print(f"  {problem}", file=sys.stderr)
                return 1
            print(f"all version files are {normalize(args.check)}", file=sys.stderr)
            print(normalize(args.check))
        else:
            changed = set_versions(args.version, ROOT)
            print(f"set {normalize(args.version)}; changed: {', '.join(changed) if changed else 'nothing'}")
    except BumpError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
