import importlib.util
import json
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bump_version_under_test", REPO_ROOT / "scripts" / "bump_version.py")
bump = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bump  # dataclasses resolves string annotations through sys.modules
_spec.loader.exec_module(bump)


@pytest.fixture
def plain_copy(tmp_path: Path) -> Path:
    """A copy of every version-carrying file, so the real formats are exercised."""
    for target in bump.TARGETS:
        destination = tmp_path / target.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / target.path, destination)
    return tmp_path


@pytest.fixture(params=["as-committed", "0.2.0"])
def repo_copy(request, plain_copy: Path) -> Path:
    """`plain_copy` once with the committed version and once after a bump to 0.2.0.

    Only for tests whose assertions depend on the start version, so they keep passing on a
    repository that has been bumped.
    """
    if request.param != "as-committed":
        bump.set_versions(request.param, plain_copy)
    return plain_copy


def start_version(root: Path) -> str:
    """The single version every file of the copy carries before a test changes it."""
    flat = {value for values in bump.read_versions(root).values() for value in values}
    assert len(flat) == 1, flat
    return flat.pop()


def line_diff(before: str, after: str) -> list[tuple[str, str]]:
    old, new = before.splitlines(), after.splitlines()
    assert len(old) == len(new)
    return [(a, b) for a, b in zip(old, new) if a != b]


def test_repository_versions_agree():
    """Drift between the manifests would make `verify-version` fail on the next tag."""
    versions = bump.read_versions(REPO_ROOT)
    assert set(versions) == {target.path for target in bump.TARGETS}
    flat = {value for values in versions.values() for value in values}
    assert len(flat) == 1, versions
    assert bump.check_versions(flat.pop(), REPO_ROOT) == []


def test_set_versions_updates_every_file_and_keeps_formatting(repo_copy: Path):
    start = start_version(repo_copy)
    before = {t.path: (repo_copy / t.path).read_text(encoding="utf-8") for t in bump.TARGETS}

    changed = bump.set_versions("v9.8.7", repo_copy)

    assert sorted(changed) == sorted(before)
    assert bump.check_versions("9.8.7", repo_copy) == []
    for path, text in before.items():
        after = (repo_copy / path).read_text(encoding="utf-8")
        diff = line_diff(text, after)
        assert diff, path
        for old_line, new_line in diff:
            assert "9.8.7" in new_line and "9.8.7" not in old_line
            assert old_line.replace(start, "9.8.7") == new_line, path
    package_lock_changes = line_diff(before["frontend/package-lock.json"], (repo_copy / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert len(package_lock_changes) == 2


def test_set_versions_leaves_files_parseable_and_dependencies_alone(plain_copy: Path):
    bump.set_versions("2.0.0", plain_copy)

    conf = json.loads((plain_copy / "frontend/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    package = json.loads((plain_copy / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((plain_copy / "frontend/package-lock.json").read_text(encoding="utf-8"))
    cargo = tomllib.loads((plain_copy / "frontend/src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((plain_copy / "pyproject.toml").read_text(encoding="utf-8"))
    assert conf["version"] == package["version"] == lock["version"] == lock["packages"][""]["version"] == "2.0.0"
    assert cargo["package"]["version"] == pyproject["project"]["version"] == "2.0.0"
    original_lock = json.loads((REPO_ROOT / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in lock["packages"].items() if k} == {k: v for k, v in original_lock["packages"].items() if k}
    original_cargo_lock = tomllib.loads((REPO_ROOT / "frontend/src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    cargo_lock = tomllib.loads((plain_copy / "frontend/src-tauri/Cargo.lock").read_text(encoding="utf-8"))
    others = lambda data: sorted((p["name"], p["version"]) for p in data["package"] if p["name"] != "ssu-lecture-downloader")  # noqa: E731
    assert others(cargo_lock) == others(original_cargo_lock)
    uv_lock = tomllib.loads((plain_copy / "uv.lock").read_text(encoding="utf-8"))
    assert [p["version"] for p in uv_lock["package"] if p["name"] == "ssu-lecture-downloader"] == ["2.0.0"]


def test_set_versions_twice_is_a_noop(plain_copy: Path):
    bump.set_versions("0.3.0", plain_copy)
    assert bump.set_versions("0.3.0", plain_copy) == []


def test_check_reports_each_mismatching_file(plain_copy: Path):
    bump.set_versions("1.2.3", plain_copy)
    conf_path = plain_copy / "frontend/src-tauri/tauri.conf.json"
    conf_path.write_text(conf_path.read_text(encoding="utf-8").replace('"version": "1.2.3"', '"version": "1.2.4"'), encoding="utf-8")

    problems = bump.check_versions("1.2.3", plain_copy)

    assert problems == ["frontend/src-tauri/tauri.conf.json: 1.2.4 (expected 1.2.3)"]
    assert bump.check_versions("v1.2.3", plain_copy) == problems


@pytest.mark.parametrize("bad", ["", "1", "1.2", "1.2.3.4", "01.2.3", "1.2.3-rc1", "1.2.3+build", "vv1.2.3", "latest", " 1.2.3", "1.2.3\n", "١.٢.٣"])
def test_invalid_versions_are_rejected(bad: str, plain_copy: Path):
    with pytest.raises(bump.BumpError):
        bump.normalize(bad)
    with pytest.raises(bump.BumpError):
        bump.set_versions(bad, plain_copy)


def test_missing_field_aborts_before_writing_anything(repo_copy: Path):
    broken = repo_copy / "pyproject.toml"
    broken.write_text(broken.read_text(encoding="utf-8").replace(f'version = "{start_version(repo_copy)}"\n', "", 1), encoding="utf-8")
    snapshot = {t.path: (repo_copy / t.path).read_text(encoding="utf-8") for t in bump.TARGETS}

    with pytest.raises(bump.BumpError, match=r"pyproject\.toml: expected 1 version field"):
        bump.set_versions("0.4.0", repo_copy)

    assert {t.path: (repo_copy / t.path).read_text(encoding="utf-8") for t in bump.TARGETS} == snapshot


def test_missing_file_is_reported(plain_copy: Path):
    (plain_copy / "uv.lock").unlink()
    with pytest.raises(bump.BumpError, match=r"uv\.lock: cannot read"):
        bump.read_versions(plain_copy)


def test_cargo_version_must_be_in_package_table(repo_copy: Path):
    cargo = repo_copy / "frontend/src-tauri/Cargo.toml"
    text = cargo.read_text(encoding="utf-8")
    cargo.write_text(text.replace(f'version = "{start_version(repo_copy)}"\n', "", 1), encoding="utf-8")
    with pytest.raises(bump.BumpError, match=r"Cargo\.toml: expected 1"):
        bump.read_versions(repo_copy)


def test_cli_show_check_and_set(plain_copy: Path, monkeypatch, capsys):
    monkeypatch.setattr(bump, "ROOT", plain_copy)

    assert bump.main(["0.5.0"]) == 0
    assert bump.main(["--check", "v0.5.0"]) == 0
    assert bump.main(["--check", "0.5.1"]) == 1
    assert "frontend/package.json: 0.5.0 (expected 0.5.1)" in capsys.readouterr().err
    assert bump.main(["--show"]) == 0
    assert "pyproject.toml: 0.5.0" in capsys.readouterr().out
    assert bump.main(["1.0"]) == 1
    assert "invalid version" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        bump.main([])


def test_cli_check_prints_the_normalized_version(plain_copy: Path, monkeypatch, capsys):
    monkeypatch.setattr(bump, "ROOT", plain_copy)
    assert bump.main(["0.5.0"]) == 0
    capsys.readouterr()

    assert bump.main(["--check", "v0.5.0"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "0.5.0\n"
    assert bump.main(["--check", "0.5.0"]) == 0
    assert capsys.readouterr().out == "0.5.0\n"


@pytest.mark.parametrize("raw", ["vv0.5.0", "vvv0.5.0", "v", "V0.5.0", "refs/tags/v0.5.0"])
def test_cli_check_rejects_more_than_one_leading_v(raw: str, plain_copy: Path, monkeypatch, capsys):
    monkeypatch.setattr(bump, "ROOT", plain_copy)
    assert bump.main(["0.5.0"]) == 0
    capsys.readouterr()

    assert bump.main(["--check", raw]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid version" in captured.err
    assert bump.main([raw]) == 1  # setting is refused the same way
