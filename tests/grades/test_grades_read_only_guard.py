"""Static guards: rusaint is imported only (lazily) in gateway.py, and only read-only names are used."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "ssu_lecture_downloader"
GATEWAY = PACKAGE / "core" / "grades" / "gateway.py"

ALLOWED_RUSAINT_ATTRIBUTES = {"USaintSessionBuilder", "CourseGradesApplicationBuilder", "CourseType", "SemesterType"}
ALLOWED_APP_METHODS = {"semesters", "recorded_summary", "certificated_summary", "classes"}
ALLOWED_SESSION_METHODS = {"with_password"}
ALLOWED_BUILDER_METHODS = {"build"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports_rusaint(tree: ast.Module) -> list[ast.AST]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "rusaint" for alias in node.names):
            found.append(node)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "rusaint":
            found.append(node)
    return found


def test_only_the_gateway_imports_rusaint_and_only_inside_a_function():
    importers = [path for path in sorted(PACKAGE.rglob("*.py")) if _imports_rusaint(_tree(path))]
    assert importers == [GATEWAY]
    tree = _tree(GATEWAY)
    top_level = {id(node) for node in tree.body}
    assert all(id(node) not in top_level for node in _imports_rusaint(tree)), "rusaint must be imported lazily"


def test_the_gateway_uses_only_the_read_only_rusaint_names():
    used = {
        node.attr
        for node in ast.walk(_tree(GATEWAY))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "rusaint"
    }
    assert used <= ALLOWED_RUSAINT_ATTRIBUTES, used - ALLOWED_RUSAINT_ATTRIBUTES
    assert {"USaintSessionBuilder", "CourseGradesApplicationBuilder"} <= used


def test_the_gateway_calls_only_read_methods_and_never_builds_another_application():
    source = GATEWAY.read_text(encoding="utf-8")
    for forbidden in ("ChapelApplication", "CourseRegistration", "Scholarship", "StudentInformation", "LectureAssessment", "PersonalCourseSchedule", "GraduationRequirements"):
        assert forbidden not in source
    called = {
        node.func.attr
        for node in ast.walk(_tree(GATEWAY))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "app"
    }
    assert called <= ALLOWED_APP_METHODS, called - ALLOWED_APP_METHODS
    assert called == ALLOWED_APP_METHODS


def test_the_include_details_flag_is_off():
    classes_calls = [
        node
        for node in ast.walk(_tree(GATEWAY))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "classes" and isinstance(node.func.value, ast.Name) and node.func.value.id == "app"
    ]
    assert len(classes_calls) == 1
    include_details = classes_calls[0].args[3]
    assert isinstance(include_details, ast.Constant) and include_details.value is False


def test_grades_core_never_imports_service_platforms_or_the_lms_trace():
    for path in sorted((PACKAGE / "core" / "grades").glob("*.py")):
        for node in ast.walk(_tree(path)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(("ssu_lecture_downloader.service", "ssu_lecture_downloader.platforms", "ssu_lecture_downloader.cli")), (path.name, name)
                assert "trace" not in name and "http_client" not in name, (path.name, name)


def test_every_rusaint_call_the_gateway_awaits_is_a_coroutine_function():
    """O4: UniFFI async functions are awaited on the event loop (not blocking calls), pinned to 0.16.3's API."""
    import inspect

    rusaint = pytest.importorskip("rusaint")
    for owner, names in (
        (rusaint.USaintSessionBuilder, ["with_password"]),
        (rusaint.CourseGradesApplicationBuilder, ["build"]),
        (rusaint.CourseGradesApplication, ["semesters", "recorded_summary", "certificated_summary", "classes"]),
    ):
        for name in names:
            assert inspect.iscoroutinefunction(getattr(owner, name)), f"{owner.__name__}.{name}"
    assert list(inspect.signature(rusaint.CourseGradesApplication.classes).parameters) == ["self", "course_type", "year", "semester", "include_details"]
