"""Request models and request validation for the HTTP API."""

from __future__ import annotations

from pathlib import Path

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


# Starting a download takes the same fields as planning one.
DownloadRequest = DownloadPlanRequest


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
