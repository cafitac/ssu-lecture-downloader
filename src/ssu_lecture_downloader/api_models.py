"""Request models, request validation and response payload builders for the HTTP API."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from ssu_lecture_downloader.stt import DEFAULT_STT_MODEL

BATCH_MODES = {"selected", "uncompleted", "all"}
ARTIFACT_OUTPUTS = {"mp4", "mp3", "txt"}
AUDIO_FORMATS = {"mp3", "wav", "m4a"}


class DownloadPlanRequest(BaseModel):
    course_id: int | str
    module_item_id: int | str
    course_name: str = "course"
    output_dir: str | None = None
    conflict_strategy: str = "rename"


class DownloadRequest(DownloadPlanRequest):
    pass


class BatchDownloadRequest(BaseModel):
    course_id: int | str
    course_name: str = "course"
    module_item_ids: list[int | str] | None = None
    mode: str = "selected"
    output_dir: str | None = None
    conflict_strategy: str = "rename"
    max_parallel_downloads: int = 4


class ArtifactDownloadRequest(BatchDownloadRequest):
    outputs: list[str] = ["mp4"]
    audio_format: str = "mp3"
    stt_provider: str = "faster_whisper"
    stt_model: str = DEFAULT_STT_MODEL
    stt_language: str = "ko"
    overwrite_artifacts: bool = False


class VideoEnsurePlayableRequest(BaseModel):
    input_path: Path
    overwrite: bool = True


class MediaDependencyInstallRequest(BaseModel):
    components: list[str] | None = None


class CredentialRequest(BaseModel):
    username: str
    password: str


def bad_request(error_code: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"ok": False, "error_code": error_code})


def validate_batch_request(request: BatchDownloadRequest) -> None:
    """Shared by plan, batch download and artifact job entry points."""
    if request.mode not in BATCH_MODES:
        raise bad_request("invalid_batch_mode")
    if request.mode == "selected" and not request.module_item_ids:
        raise bad_request("missing_module_item_ids")


def normalize_artifact_outputs(outputs: list[str]) -> list[str]:
    normalized: list[str] = []
    for output in outputs:
        value = output.lower().strip()
        if value not in ARTIFACT_OUTPUTS:
            raise bad_request("invalid_artifact_output")
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise bad_request("missing_artifact_outputs")
    return normalized


def validate_artifact_request(request: ArtifactDownloadRequest) -> list[str]:
    outputs = normalize_artifact_outputs(request.outputs)
    validate_batch_request(request)
    if request.audio_format not in AUDIO_FORMATS:
        raise bad_request("invalid_audio_format")
    return outputs


def download_payload(result, progress: list[dict[str, Any]]) -> dict[str, Any]:
    return {**result.__dict__, "progress": progress}


def native_download_payload(plan_result) -> dict[str, Any]:
    if isinstance(plan_result, dict):
        result = plan_result["result"]
        native_download = plan_result.get("native_download")
    else:
        result = plan_result.result
        native_download = plan_result.native_download
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
