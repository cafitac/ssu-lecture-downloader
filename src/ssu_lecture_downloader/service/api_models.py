"""Request models and request validation for the HTTP API."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from ssu_lecture_downloader.core.stt import DEFAULT_STT_MODEL

BATCH_MODES = {"selected", "uncompleted", "all"}
ARTIFACT_OUTPUTS = {"mp4", "mp3", "txt"}
AUDIO_FORMATS = {"mp3", "wav", "m4a"}


class DownloadPlanRequest(BaseModel):
    course_id: int | str
    module_item_id: int | str
    course_name: str = "course"
    output_dir: str | None = None
    conflict_strategy: str = "rename"


class ChromeWatchItemRequest(BaseModel):
    course_id: int | str
    course_name: str = ""
    module_item_id: int | str
    title: str = ""
    duration: float | None = None


class ChromeWatchStartRequest(BaseModel):
    items: list[ChromeWatchItemRequest]


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


class CanvasFileDownloadRequest(BaseModel):
    course_id: int | str
    file_id: int | str
    course_name: str = "course"
    output_dir: str | None = None
    conflict_strategy: str = "skip"


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


CONFLICT_STRATEGIES = {"rename", "overwrite", "skip"}
_MAX_PAGE_REF_LENGTH = 200


def validate_digits(value: int | str, error_code: str) -> str:
    """A numeric LMS id as a string, or a 400 with `error_code`."""
    text = str(value).strip()
    if not text.isdigit():
        raise bad_request(error_code)
    return text


def validate_page_ref(value: str) -> str:
    """A wiki page slug or numeric id: no path separators, control characters or absurd length."""
    text = value.strip()
    if not text or len(text) > _MAX_PAGE_REF_LENGTH or any(ch in text for ch in "/\\") or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise bad_request("invalid_page_ref")
    return text


def validate_canvas_file_request(request: CanvasFileDownloadRequest) -> tuple[str, str]:
    if request.conflict_strategy not in CONFLICT_STRATEGIES:
        raise bad_request("invalid_conflict_strategy")
    return validate_digits(request.course_id, "invalid_course_id"), validate_digits(request.file_id, "invalid_file_id")


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
