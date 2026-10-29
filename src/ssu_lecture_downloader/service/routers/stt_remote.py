"""Remote subtitle server settings: status, saved config and a connection check."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ssu_lecture_downloader.service.app_context import AppContext
from ssu_lecture_downloader.core.stt_remote_config import ConfigError


class TunnelConfigRequest(BaseModel):
    host: str
    remote_port: int
    local_port: int | None = None


class RemoteSttConfigRequest(BaseModel):
    """Partial update: only the fields present in the body change.

    `api_key`: omit to keep the saved key, send an empty string to remove it.
    `tunnel`: send `null` to remove the SSH tunnel.
    """

    enabled: bool | None = None
    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    tunnel: TunnelConfigRequest | None = None


def build_router(ctx: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/stt/remote/status")
    def remote_status() -> dict[str, Any]:
        return {"ok": True, **ctx.remote_stt.status()}

    @router.put("/api/stt/remote/config")
    def update_remote_config(request: RemoteSttConfigRequest) -> dict[str, Any]:
        patch = {name: getattr(request, name) for name in request.model_fields_set}
        if "tunnel" in patch and request.tunnel is not None:
            patch["tunnel"] = request.tunnel.model_dump()
        try:
            status = ctx.remote_stt.update(patch)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": exc.code}) from exc
        return {"ok": True, **status}

    @router.post("/api/stt/remote/check")
    def check_remote() -> dict[str, Any]:
        # A failed probe is a normal answer (`reachable: false` + `last_error`), not an HTTP error.
        return {"ok": True, **ctx.remote_stt.check()}

    return router
