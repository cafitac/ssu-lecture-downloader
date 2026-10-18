"""Official-viewer helpers used by the desktop shell (plan + session bridge) and the Chrome playback queue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ssu_lecture_downloader.api_errors import AUTHENTICATION_ERROR_CODES, raise_for_result_error
from ssu_lecture_downloader.api_models import ChromeWatchStartRequest, DownloadPlanRequest
from ssu_lecture_downloader.app_context import AppContext
from ssu_lecture_downloader.chrome_watch import ChromeWatchError, ChromeWatchManager, ChromeWatchPlanError
from ssu_lecture_downloader.payloads import viewer_plan_payload


def _chrome_error(error: ChromeWatchError) -> HTTPException:
    status = 409 if error.code == "chrome_watch_busy" else 400
    return HTTPException(status_code=status, detail={"ok": False, "error_code": error.code, "message": error.message})


def chrome_plan_provider(ctx: AppContext):
    """The session-bridge plan of one lecture for the Chrome driver (a service call, not HTTP)."""

    def provide(course_id: str, module_item_id: str, course_name: str) -> dict[str, Any]:
        try:
            username, password = ctx.require_credentials()
        except HTTPException as exc:
            raise ChromeWatchPlanError("missing_credentials", auth=True) from exc
        try:
            result = ctx.service.official_viewer_session_bridge_plan(
                username, password, course_id=course_id, module_item_id=module_item_id
            )
        except Exception as exc:  # noqa: BLE001 - map to the API error vocabulary
            error = ctx.call_service_error(exc)
            code = error.detail.get("error_code", "service_error") if isinstance(error.detail, dict) else "service_error"
            raise ChromeWatchPlanError(code, auth=code in AUTHENTICATION_ERROR_CODES) from exc
        if not result.ok:
            code = result.error_code or "service_error"
            raise ChromeWatchPlanError(code, auth=code in AUTHENTICATION_ERROR_CODES)
        return viewer_plan_payload(result)

    return provide


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    service = ctx.service
    if ctx.chrome_watch is None:
        ctx.chrome_watch = ChromeWatchManager(chrome_plan_provider(ctx))
    chrome = ctx.chrome_watch

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

    # -- Chrome window playback (see chrome_watch.py). Same token/Host/CORS protection as every /api route.

    @router.post("/api/watch/chrome/start")
    def chrome_watch_start(request: ChromeWatchStartRequest) -> dict[str, Any]:
        ctx.require_credentials()
        try:
            return chrome.start([item.model_dump() for item in request.items])
        except ChromeWatchError as error:
            raise _chrome_error(error) from error

    @router.get("/api/watch/chrome/status")
    def chrome_watch_status() -> dict[str, Any]:
        return chrome.status()

    @router.post("/api/watch/chrome/next")
    def chrome_watch_next() -> dict[str, Any]:
        return chrome.next()

    @router.post("/api/watch/chrome/stop")
    def chrome_watch_stop() -> dict[str, Any]:
        return chrome.stop()

    @router.post("/api/watch/chrome/show")
    def chrome_watch_show() -> dict[str, Any]:
        try:
            return chrome.show()
        except ChromeWatchError as error:
            raise _chrome_error(error) from error

    return router
