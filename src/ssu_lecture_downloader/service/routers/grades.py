"""u-SAINT grades (read-only, opt-in): status, consent, overview, per-semester classes.

Every response under `/api/grades` carries `Cache-Control: no-store` (success and error alike, set by
`GradesNoStoreMiddleware`). Errors use the grades codes, which are NOT LMS authentication codes, so a
grades failure never signs the user out of the LMS. Handlers never put an exception message, a
username, or a value into an error body or a log line.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ssu_lecture_downloader.core.grades.consent import CURRENT_CONSENT_VERSION
from ssu_lecture_downloader.core.grades.models import SEMESTER_NAMES, GradesError
from ssu_lecture_downloader.service.api_errors import grades_error
from ssu_lecture_downloader.service.app_context import AppContext

GRADES_PATH_PREFIX = "/api/grades"
NO_STORE = "no-store"

class ConsentRequest(BaseModel):
    consent_version: Any = None


def _bad_request(code: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"ok": False, "error_code": code})


class GradesNoStoreMiddleware:
    """Adds `Cache-Control: no-store` to every HTTP response whose path is under `/api/grades`."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not (path == GRADES_PATH_PREFIX or path.startswith(GRADES_PATH_PREFIX + "/")):
            await self.app(scope, receive, send)
            return

        async def send_no_store(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [(name, value) for name, value in message.get("headers", []) if name.lower() != b"cache-control"]
                headers.append((b"cache-control", NO_STORE.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_no_store)


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()
    grades = ctx.grades

    @router.get("/api/grades/status")
    def grades_status() -> dict[str, Any]:
        return {"ok": True, **grades.status().to_dict()}

    @router.put("/api/grades/consent")
    def grant_consent(request: ConsentRequest | None = None) -> dict[str, Any]:
        version = request.consent_version if request is not None else None
        if isinstance(version, bool) or version != CURRENT_CONSENT_VERSION:
            raise _bad_request("invalid_consent_version")
        try:
            status = grades.grant_consent(version)
        except GradesError as error:
            raise grades_error(error) from None
        return {"ok": True, **status.to_dict()}

    @router.delete("/api/grades/consent")
    def revoke_consent() -> dict[str, Any]:
        try:
            status = grades.revoke_consent()
        except GradesError as error:
            raise grades_error(error) from None
        return {"ok": True, **status.to_dict()}

    # `async def`: these await the gateway on the event loop, so a slow u-SAINT never holds a worker
    # thread, and the sync LMS routes keep running on the thread pool meanwhile.
    @router.get("/api/grades")
    async def grades_overview(refresh: bool = False) -> dict[str, Any]:
        try:
            overview = await grades.overview(refresh)
        except GradesError as error:
            raise grades_error(error) from None
        return {"ok": True, **overview.to_dict()}

    @router.get("/api/grades/classes")
    async def grades_classes(year: int, semester: str, refresh: bool = False) -> dict[str, Any]:
        if semester not in SEMESTER_NAMES:
            raise _bad_request("invalid_semester")
        try:
            result = await grades.classes(year, semester, refresh)
        except GradesError as error:
            raise grades_error(error) from None
        return {"ok": True, **result.to_dict()}

    return router
