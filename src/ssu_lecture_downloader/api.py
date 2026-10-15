"""Local FastAPI application factory.

Routes live in `routers/` (one module per area); this module only wires the app:
security middleware, JSON error handlers, shared state and the routers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ssu_lecture_downloader.api_errors import (
    UnhandledErrorMiddleware,
    background_job_error_code,
)
from ssu_lecture_downloader.app_context import AppContext, default_download_dir
from ssu_lecture_downloader.credentials import CredentialStore, default_credential_store
from ssu_lecture_downloader.jobs import JobRunner
from ssu_lecture_downloader.routers import credentials as credentials_routes
from ssu_lecture_downloader.routers import download as download_routes
from ssu_lecture_downloader.routers import health as health_routes
from ssu_lecture_downloader.routers import media as media_routes
from ssu_lecture_downloader.routers import watch as watch_routes
from ssu_lecture_downloader.security import (
    ALLOWED_CORS_ORIGINS,
    API_TOKEN_HEADER,
    ApiTokenMiddleware,
    LoopbackHostMiddleware,
    MediaPathPolicy,
    configured_api_token,
)

ServiceFactory = Callable[[], Any]

__all__ = ["app", "create_app"]


def _json_error_body(detail: Any, status_code: int) -> dict[str, Any]:
    if isinstance(detail, dict):
        return detail
    return {"ok": False, "error_code": str(detail).strip().lower().replace(" ", "_") or f"http_{status_code}"}


def create_app(
    *,
    service_factory: ServiceFactory | None = None,
    default_output_dir: Path | None = None,
    credential_store: CredentialStore | None = None,
    api_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="SSU Lecture Downloader Local API")

    # Middleware order: the last one added is the outermost. Request flow is
    # Host check -> CORS -> API token -> unhandled-error catch-all -> routes.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(ApiTokenMiddleware, token=api_token if api_token is not None else configured_api_token())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", API_TOKEN_HEADER],
    )
    app.add_middleware(LoopbackHostMiddleware)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content=_json_error_body(exc.detail, exc.status_code))

    service = (service_factory or _default_service)()
    output_dir = default_output_dir or default_download_dir()
    ctx = AppContext(
        service=service,
        credentials=credential_store or default_credential_store(),
        default_output_dir=output_dir,
        media_paths=MediaPathPolicy(output_dir),
        runner=JobRunner(error_code_for=lambda exc: background_job_error_code(exc, service)),
    )
    for module in (health_routes, credentials_routes, download_routes, media_routes, watch_routes):
        # The routes are already built; attach them as-is instead of having FastAPI rebuild each one.
        app.router.routes.extend(module.build_router(ctx).routes)
    return app


def _default_service() -> Any:
    from ssu_lecture_downloader.service import SsuLectureService

    return SsuLectureService()


app = create_app()
