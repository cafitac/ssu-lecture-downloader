"""Credentials plus LMS discovery: terms, courses, course detail."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ssu_lecture_downloader.service.api_errors import (
    authentication_failed_error,
    clear_service_cache,
    raise_for_result_error,
)
from ssu_lecture_downloader.service.api_models import CredentialRequest
from ssu_lecture_downloader.service.app_context import AppContext
from ssu_lecture_downloader.core.credentials import CredentialPair
from ssu_lecture_downloader.core.download import find_downloaded_files


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    service = ctx.service
    credentials = ctx.credentials

    def login_or_raise(username: str, password: str) -> None:
        login = ctx.call_service(lambda: service.login(username, password))
        if not login.ok:
            raise authentication_failed_error(login.error_code)

    def refresh_kwargs(refresh: bool) -> dict[str, Any]:
        # Only sent when asked for, so services without the option keep working.
        return {"refresh": True} if refresh else {}

    @router.get("/api/credentials/status")
    def credential_status() -> dict[str, object]:
        return {"ok": True, **credentials.status()}

    @router.post("/api/credentials")
    def save_credentials(request: CredentialRequest) -> dict[str, object]:
        username = request.username.strip()
        password = request.password.strip()
        if not username or not password:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "invalid_credentials_payload"})
        current = credentials.get()
        if current is None or (current.username, current.password) != (username, password):
            # Same credentials keep their LMS session and caches instead of logging in again.
            clear_service_cache(service)
        login_or_raise(username, password)
        credentials.save(CredentialPair(username=username, password=password))
        return {"ok": True, **credentials.status()}

    @router.delete("/api/credentials")
    def delete_credentials() -> dict[str, object]:
        credentials.delete()
        clear_service_cache(service)
        return {"ok": True, **credentials.status()}

    @router.get("/api/terms")
    def terms(refresh: bool = False) -> dict[str, object]:
        username, password = ctx.require_credentials()
        login_or_raise(username, password)
        listed_terms = ctx.call_service(lambda: service.list_terms(username, password, **refresh_kwargs(refresh)))
        if not listed_terms:
            clear_service_cache(service)
            raise authentication_failed_error("lms_session_unverified")
        return {"ok": True, "error_code": None, "terms": listed_terms}

    @router.get("/api/courses")
    def courses(term_id: int | None = None, refresh: bool = False) -> dict[str, object]:
        username, password = ctx.require_credentials()
        term_ids = [term_id] if term_id is not None else None
        result = ctx.call_service(lambda: service.discover_courses(username, password, term_ids=term_ids, **refresh_kwargs(refresh)))
        if not result.ok:
            raise_for_result_error(result.error_code)
        return {
            "ok": True,
            "error_code": None,
            "course_count": result.course_count,
            "term_ids": term_ids,
            "courses": [course.model_dump() for course in result.courses],
        }

    @router.get("/api/deadlines")
    def deadlines(term_id: int | None = None, refresh: bool = False) -> dict[str, object]:
        username, password = ctx.require_credentials()
        term_ids = [term_id] if term_id is not None else None
        result = ctx.call_service(lambda: service.list_deadlines(username, password, term_ids=term_ids, **refresh_kwargs(refresh)))
        if not result.ok:
            raise_for_result_error(result.error_code)
        return {
            "ok": True,
            "error_code": None,
            "term_ids": term_ids,
            "deadline_count": len(result.deadlines),
            "deadlines": [item.model_dump() for item in result.deadlines],
        }

    @router.get("/api/courses/{course_id}")
    def course_detail(course_id: str, course_name: str = "", refresh: bool = False, output_dir: str | None = None) -> dict[str, object]:
        username, password = ctx.require_credentials()
        result = ctx.call_service(
            lambda: service.get_course_detail(username, password, course_id=course_id, course_name=course_name, **refresh_kwargs(refresh))
        )
        if not result.ok or result.course is None:
            raise_for_result_error(result.error_code)
        content_items = result.content_items or []
        downloaded = find_downloaded_files(ctx.output_dir_for(output_dir), course_name, (item.content_id for item in content_items if item.downloadable))
        return {
            "ok": True,
            "error_code": None,
            "course": result.course.model_dump(),
            "lecture_count": len(result.lectures),
            "lectures": [lecture.model_dump() for lecture in result.lectures],
            "content_count": len(content_items),
            "content_items": [{**item.model_dump(), "downloaded_path": downloaded.get(item.content_id or "")} for item in content_items],
        }

    return router
