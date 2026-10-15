"""Credentials plus LMS discovery: terms, courses, course detail."""

from __future__ import annotations

from typing import Callable, TypeVar

from fastapi import APIRouter, HTTPException

from ssu_lecture_downloader.api_errors import (
    authentication_failed_error,
    clear_service_cache,
    raise_for_result_error,
    service_exception_error,
)
from ssu_lecture_downloader.api_models import CredentialRequest
from ssu_lecture_downloader.app_context import AppContext
from ssu_lecture_downloader.credentials import CredentialPair

T = TypeVar("T")


def call_service(ctx: AppContext, operation: Callable[[], T]) -> T:
    """Run a service call, mapping any failure to the JSON error shape."""
    try:
        return operation()
    except Exception as exc:
        raise service_exception_error(exc, ctx.service) from exc


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    service = ctx.service
    credentials = ctx.credentials

    @router.get("/api/credentials/status")
    def credential_status() -> dict[str, object]:
        return {"ok": True, **credentials.status()}

    @router.post("/api/credentials")
    def save_credentials(request: CredentialRequest) -> dict[str, object]:
        username = request.username.strip()
        password = request.password.strip()
        if not username or not password:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "invalid_credentials_payload"})
        clear_service_cache(service)
        login = call_service(ctx, lambda: service.login(username, password))
        if not login.ok:
            raise authentication_failed_error(login.error_code)
        credentials.save(CredentialPair(username=username, password=password))
        return {"ok": True, **credentials.status()}

    @router.delete("/api/credentials")
    def delete_credentials() -> dict[str, object]:
        credentials.delete()
        clear_service_cache(service)
        return {"ok": True, **credentials.status()}

    @router.get("/api/terms")
    def terms() -> dict[str, object]:
        username, password = ctx.require_credentials()
        login = call_service(ctx, lambda: service.login(username, password))
        if not login.ok:
            raise authentication_failed_error(login.error_code)
        listed_terms = call_service(ctx, lambda: service.list_terms(username, password))
        if not listed_terms:
            clear_service_cache(service)
            raise authentication_failed_error("lms_session_unverified")
        return {"ok": True, "error_code": None, "terms": listed_terms}

    @router.get("/api/courses")
    def courses(term_id: int | None = None) -> dict[str, object]:
        username, password = ctx.require_credentials()
        term_ids = [term_id] if term_id is not None else None
        result = call_service(ctx, lambda: service.discover_courses(username, password, term_ids=term_ids))
        if not result.ok:
            raise_for_result_error(result.error_code)
        return {
            "ok": True,
            "error_code": None,
            "course_count": result.course_count,
            "term_ids": term_ids,
            "courses": [course.model_dump() for course in result.courses],
        }

    @router.get("/api/courses/{course_id}")
    def course_detail(course_id: str, course_name: str = "") -> dict[str, object]:
        username, password = ctx.require_credentials()
        result = call_service(ctx, lambda: service.get_course_detail(username, password, course_id=course_id, course_name=course_name))
        if not result.ok or result.course is None:
            raise_for_result_error(result.error_code)
        content_items = result.content_items or []
        return {
            "ok": True,
            "error_code": None,
            "course": result.course.model_dump(),
            "lecture_count": len(result.lectures),
            "lectures": [lecture.model_dump() for lecture in result.lectures],
            "content_count": len(content_items),
            "content_items": [item.model_dump() for item in content_items],
        }

    return router
