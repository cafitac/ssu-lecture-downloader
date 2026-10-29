"""Guards the layer boundary: ``core`` must not import ``service`` or ``cli``.

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
    assert "core imports nothing from service or cli KEPT" in result.stdout


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
