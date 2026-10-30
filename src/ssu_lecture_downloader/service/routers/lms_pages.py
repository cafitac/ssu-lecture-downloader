"""LMS pages opened inside the app: session cookies, wiki-page attachments, Canvas file download."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from ssu_lecture_downloader.service.api_errors import raise_for_result_error
from ssu_lecture_downloader.service.api_models import (
    CanvasFileDownloadRequest,
    validate_canvas_file_request,
    validate_digits,
    validate_page_ref,
)
from ssu_lecture_downloader.service.app_context import AppContext
from ssu_lecture_downloader.core.download import find_downloaded_files


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    service = ctx.service

    @router.post("/api/lms/session-bridge")
    def lms_session_bridge() -> dict[str, Any]:
        # Returns live LMS cookies for the desktop shell's page window: protected like the
        # watch session bridge (API token, allow-listed origin, loopback Host; see security.py).
        username, password = ctx.require_credentials()
        result = ctx.call_service(lambda: service.lms_session_cookies(username, password))
        if not result.ok:
            raise_for_result_error(result.error_code)
        return {"ok": True, "error_code": None, "cookies": [asdict(cookie) for cookie in result.cookies], "diagnostics": result.diagnostics}

    @router.get("/api/courses/{course_id}/pages/{page_ref}/files")
    def page_files(course_id: str, page_ref: str, course_name: str = "", output_dir: str | None = None) -> dict[str, Any]:
        username, password = ctx.require_credentials()
        course = validate_digits(course_id, "invalid_course_id")
        ref = validate_page_ref(page_ref)
        result = ctx.call_service(lambda: service.list_page_files(username, password, course_id=course, page_ref=ref))
        if not result.ok:
            raise_for_result_error(result.error_code)
        downloaded = (
            find_downloaded_files(ctx.output_dir_for(output_dir), course_name, (f"canvas-{file.file_id}" for file in result.files))
            if course_name
            else {}
        )
        return {
            "ok": True,
            "error_code": None,
            "course_id": course,
            "page_ref": ref,
            "title": result.title,
            "file_count": len(result.files),
            "files": [{**asdict(file), "downloaded_path": downloaded.get(f"canvas-{file.file_id}")} for file in result.files],
        }

    @router.post("/api/download/canvas-file")
    def download_canvas_file(request: CanvasFileDownloadRequest) -> dict[str, Any]:
        username, password = ctx.require_credentials()
        _, file_id = validate_canvas_file_request(request)
        output_dir = ctx.output_dir_for(request.output_dir)
        with ctx.library_lock.downloading():
            result = ctx.call_service(
                lambda: service.download_canvas_file(
                    username,
                    password,
                    file_id=file_id,
                    course_name=request.course_name,
                    output_dir=output_dir,
                    conflict_strategy=request.conflict_strategy,
                    module_name=request.module_name,
                    week_position=request.week_position,
                )
            )
        if not result.ok:
            raise_for_result_error(result.error_code)
        ctx.remember_outputs(result.output_path)
        return asdict(result)

    return router
