from __future__ import annotations

from fastapi import APIRouter

from ssu_lecture_downloader.app_context import AppContext


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "ok": True,
            "app": "ssu-lecture-downloader",
            "capabilities": [
                "runtime_memory_credentials",
                "download_jobs",
                "artifact_download_jobs",
                "stt_provider_status",
                "packaged_media_dependencies",
                "lms_pages",
            ],
        }

    return router
