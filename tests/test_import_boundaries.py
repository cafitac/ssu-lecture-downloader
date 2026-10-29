"""Guards the layer boundaries: ``core`` must not import ``service``, ``cli`` or ``platforms``, and
``platforms`` only the composition roots may import.

The same contract runs in CI as ``uv run lint-imports`` (see ``[tool.importlinter]`` in
pyproject.toml). These tests run it through pytest as well, and prove with a deliberate violation
that the check fails when core reaches into service.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "src" / "ssu_lecture_downloader"
FORBIDDEN_FOR_CORE = ("ssu_lecture_downloader.service", "ssu_lecture_downloader.cli")

_LINT_IMPORTS = "import sys; from importlinter.cli import lint_imports_command; sys.exit(lint_imports_command())"


def _run_lint_imports(config: Path, cwd: Path, extra_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = None
    if extra_path is not None:
        env = {**os.environ, "PYTHONPATH": str(extra_path)}
    return subprocess.run(
        [sys.executable, "-c", _LINT_IMPORTS, "--config", str(config)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_import_linter_contract_is_kept():
    result = _run_lint_imports(REPO_ROOT / "pyproject.toml", REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    for contract in (
        "core imports nothing from service or cli",
        "core imports nothing from platforms",
        "platforms import nothing from service or cli",
        "only app_context and cli import platforms",
    ):
        assert f"{contract} KEPT" in result.stdout


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_core_modules_do_not_import_service_or_cli():
    offenders = []
    for path in sorted((PACKAGE_DIR / "core").rglob("*.py")):
        for name in _imported_modules(path):
            if any(name == forbidden or name.startswith(forbidden + ".") for forbidden in FORBIDDEN_FOR_CORE):
                offenders.append(f"{path.relative_to(PACKAGE_DIR)} imports {name}")
    assert offenders == []


def test_import_linter_fails_on_a_deliberate_core_to_service_import(tmp_path):
    package = tmp_path / "boundary_fixture"
    (package / "core").mkdir(parents=True)
    (package / "service").mkdir()
    for init in (package / "__init__.py", package / "core" / "__init__.py", package / "service" / "__init__.py"):
        init.write_text("")
    (package / "service" / "api.py").write_text("VALUE = 1\n")
    (package / "core" / "leak.py").write_text("from boundary_fixture.service.api import VALUE\n")
    config = tmp_path / "importlinter.ini"
    config.write_text(
        textwrap.dedent(
            """\
            [importlinter]
            root_package = boundary_fixture

            [importlinter:contract:core-not-service]
            name = core imports nothing from service
            type = forbidden
            source_modules = boundary_fixture.core
            forbidden_modules = boundary_fixture.service
            """
        )
    )
    result = _run_lint_imports(config, tmp_path, extra_path=tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "BROKEN" in result.stdout
    assert "boundary_fixture.core.leak" in result.stdout

    # Same fixture without the leak passes, so the failure above comes from the violation alone.
    (package / "core" / "leak.py").write_text("VALUE = 1\n")
    clean = _run_lint_imports(config, tmp_path, extra_path=tmp_path)
    assert clean.returncode == 0, clean.stdout + clean.stderr


def test_root_api_shim_resolves_like_the_desktop_shell_launch():
    # frontend/src-tauri/src/backend.rs launches (`uv run uvicorn ssu_lecture_downloader.api:app`) and
    # detects stale backends by that exact string, so this shim must stay until the Rust side changes.
    from uvicorn.importer import import_from_string

    from ssu_lecture_downloader.service import api as service_api

    assert import_from_string("ssu_lecture_downloader.api:app") is service_api.app


def test_only_app_context_and_cli_import_platforms():
    allowed = {PACKAGE_DIR / "service" / "app_context.py", PACKAGE_DIR / "cli.py"}
    offenders = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        if path in allowed or "platforms" in path.relative_to(PACKAGE_DIR).parts:
            continue
        for name in _imported_modules(path):
            if name == "ssu_lecture_downloader.platforms" or name.startswith("ssu_lecture_downloader.platforms."):
                offenders.append(f"{path.relative_to(PACKAGE_DIR)} imports {name}")
    assert offenders == []


def _contract_fixture(tmp_path: Path, contract: str, leak_file: str, leak_source: str) -> tuple[Path, Path]:
    """A tiny package with core/service/platforms, one contract, and one file that may leak."""
    package = tmp_path / "boundary_fixture"
    for sub in ("core", "service", "platforms"):
        (package / sub).mkdir(parents=True)
        (package / sub / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "platforms" / "macos.py").write_text("VALUE = 1\n")
    (package / "service" / "app_context.py").write_text("from boundary_fixture.platforms import macos\n")
    (package / "service" / "api.py").write_text("VALUE = 1\n")
    (package / leak_file).write_text(leak_source)
    config = tmp_path / "importlinter.ini"
    config.write_text("[importlinter]\nroot_package = boundary_fixture\n\n" + textwrap.dedent(contract))
    return config, package / leak_file


CORE_NOT_PLATFORMS = """\
    [importlinter:contract:core-not-platforms]
    name = core imports nothing from platforms
    type = forbidden
    source_modules = boundary_fixture.core
    forbidden_modules = boundary_fixture.platforms
    """

PLATFORMS_NOT_SERVICE = """\
    [importlinter:contract:platforms-not-service]
    name = platforms import nothing from service
    type = forbidden
    source_modules = boundary_fixture.platforms
    forbidden_modules = boundary_fixture.service
    """

ONLY_APP_CONTEXT = """\
    [importlinter:contract:only-app-context]
    name = only app_context imports platforms
    type = forbidden
    source_modules =
        boundary_fixture.core
        boundary_fixture.service
    forbidden_modules = boundary_fixture.platforms
    ignore_imports =
        boundary_fixture.service.app_context -> boundary_fixture.platforms.macos
    """


def _assert_violation_is_caught(tmp_path: Path, contract: str, leak_file: str, leak_source: str, offender: str) -> None:
    config, leak = _contract_fixture(tmp_path, contract, leak_file, leak_source)
    result = _run_lint_imports(config, tmp_path, extra_path=tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "BROKEN" in result.stdout
    assert offender in result.stdout

    # The same fixture without the leak passes, so the failure comes from the violation alone.
    leak.write_text("VALUE = 1\n")
    clean = _run_lint_imports(config, tmp_path, extra_path=tmp_path)
    assert clean.returncode == 0, clean.stdout + clean.stderr


def test_import_linter_fails_on_a_deliberate_core_to_platforms_import(tmp_path):
    _assert_violation_is_caught(tmp_path, CORE_NOT_PLATFORMS, "core/leak.py", "from boundary_fixture.platforms import macos\n", "boundary_fixture.core.leak")


def test_import_linter_fails_on_a_deliberate_platforms_to_service_import(tmp_path):
    _assert_violation_is_caught(tmp_path, PLATFORMS_NOT_SERVICE, "platforms/leak.py", "from boundary_fixture.service import api\n", "boundary_fixture.platforms.leak")


def test_import_linter_fails_when_a_service_module_other_than_app_context_imports_platforms(tmp_path):
    _assert_violation_is_caught(tmp_path, ONLY_APP_CONTEXT, "service/routers_leak.py", "from boundary_fixture.platforms import macos\n", "boundary_fixture.service.routers_leak")
