"""JSON payload builders for service results, shared by the HTTP API and the CLI.

Kept free of FastAPI imports so the CLI does not pay for the web stack.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any


def download_payload(result, progress: list[dict[str, Any]]) -> dict[str, Any]:
    return {**result.__dict__, "progress": progress}


def unpack_native_plan(plan_result) -> tuple[Any, Any]:
    """(DownloadLectureResult, native transfer plan) from a service result or its dict form."""
    if isinstance(plan_result, dict):
        return plan_result["result"], plan_result.get("native_download")
    return plan_result.result, plan_result.native_download


def native_download_payload(plan_result) -> dict[str, Any]:
    result, native_download = unpack_native_plan(plan_result)
    payload = download_payload(result, [])
    payload["native_download"] = asdict(native_download) if native_download is not None and not isinstance(native_download, dict) else native_download
    return payload


def viewer_plan_payload(result) -> dict[str, Any]:
    return result if isinstance(result, dict) else asdict(result)


def batch_payload(result, progress: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "error_code": result.error_code,
        "dry_run": result.dry_run,
        "course_id": result.course_id,
        "course_name": result.course_name,
        "total_count": result.total_count,
        "success_count": result.success_count,
        "failure_count": result.failure_count,
        "items": [asdict(item) for item in result.items],
        "progress": progress,
    }


def artifact_payload(download: dict[str, Any], artifacts: list[dict[str, Any]], progress: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "error_code": None,
        "download": download,
        "artifacts": artifacts,
        "progress": progress,
    }
