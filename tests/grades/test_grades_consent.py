from __future__ import annotations

import json
import os
import stat
import sys

import pytest
from grades_fakes import FakeClock

from ssu_lecture_downloader.core.grades.consent import CURRENT_CONSENT_VERSION, GradesConsentStore


def store(tmp_path, clock=None):
    return GradesConsentStore(tmp_path / "data" / "grades-consent.json", now=clock or FakeClock())


def test_absent_file_is_no_consent(tmp_path):
    assert store(tmp_path).read() is None


def test_grant_then_read_round_trip_writes_only_schema_version_and_time(tmp_path):
    s = store(tmp_path)
    consent = s.grant(CURRENT_CONSENT_VERSION)
    assert consent.granted_at == "2030-01-02T03:04:05Z"
    assert s.read() == consent
    assert json.loads(s.path.read_text()) == {"schema": 1, "consent_version": 1, "granted_at": "2030-01-02T03:04:05Z"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_consent_file_is_private(tmp_path):
    s = store(tmp_path)
    s.grant(1)
    assert stat.S_IMODE(os.stat(s.path).st_mode) == 0o600


def test_grant_leaves_no_temp_files_and_is_atomic_on_failure(tmp_path, monkeypatch):
    s = store(tmp_path)
    s.grant(1)
    before = s.path.read_text()

    def boom(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store(tmp_path, FakeClock()).grant(1)
    monkeypatch.undo()
    assert s.path.read_text() == before  # the previous file is intact
    assert [p.name for p in s.path.parent.iterdir()] == ["grades-consent.json"]  # temp file cleaned up


def test_revoke_deletes_the_file_and_tolerates_a_missing_one(tmp_path):
    s = store(tmp_path)
    s.revoke()
    s.grant(1)
    s.revoke()
    assert not s.path.exists()
    assert s.read() is None


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        "null",
        json.dumps({"schema": 1, "consent_version": 2, "granted_at": "2030-01-01T00:00:00Z"}),  # newer copy: ask again
        json.dumps({"schema": 1, "consent_version": 0, "granted_at": "2030-01-01T00:00:00Z"}),
        json.dumps({"schema": 1, "consent_version": True, "granted_at": "2030-01-01T00:00:00Z"}),
        json.dumps({"schema": 2, "consent_version": 1, "granted_at": "2030-01-01T00:00:00Z"}),
        json.dumps({"schema": 1, "consent_version": 1}),
        json.dumps({"schema": 1, "consent_version": 1, "granted_at": 5}),
    ],
)
def test_malformed_or_outdated_file_means_no_consent(tmp_path, content):
    s = store(tmp_path)
    s.path.parent.mkdir(parents=True)
    s.path.write_text(content)
    assert s.read() is None


@pytest.mark.parametrize("version", [0, 2, True, "1"])
def test_grant_rejects_other_versions(tmp_path, version):
    with pytest.raises(ValueError):
        store(tmp_path).grant(version)
    assert not (tmp_path / "data").exists()


def test_a_callable_path_is_resolved_on_every_use(tmp_path):
    target = {"dir": tmp_path / "a"}
    s = GradesConsentStore(lambda: target["dir"] / "grades-consent.json")
    s.grant(1)
    target["dir"] = tmp_path / "b"
    assert s.read() is None
