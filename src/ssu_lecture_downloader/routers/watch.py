"""Official-viewer helpers used by the desktop shell (plan + session bridge)."""

from __future__ import annotations

from fastapi import APIRouter

from ssu_lecture_downloader.api_errors import raise_for_result_error
from ssu_lecture_downloader.api_models import DownloadPlanRequest
from ssu_lecture_downloader.app_context import AppContext
from ssu_lecture_downloader.payloads import viewer_plan_payload


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    service = ctx.service

    @router.post("/api/watch/viewer-plan")
    def official_viewer_plan(request: DownloadPlanRequest) -> dict[str, object]:
        username, password = ctx.require_credentials()
        result = ctx.call_service(
            lambda: service.official_viewer_plan(username, password, course_id=request.course_id, module_item_id=request.module_item_id)
        )
        if not result.ok:
            raise_for_result_error(result.error_code)
        return viewer_plan_payload(result)

    @router.post("/api/watch/session-bridge-plan")
    def official_viewer_session_bridge_plan(request: DownloadPlanRequest) -> dict[str, object]:
        # Returns live LMS cookies: only reachable with the API token (when configured),
        # from an allow-listed origin and a loopback Host (see security.py).
        username, password = ctx.require_credentials()
        result = ctx.call_service(
            lambda: service.official_viewer_session_bridge_plan(username, password, course_id=request.course_id, module_item_id=request.module_item_id)
        )
        if not result.ok:
            raise_for_result_error(result.error_code)
        return viewer_plan_payload(result)

    return router
