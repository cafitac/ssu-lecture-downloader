from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from curl_cffi.requests import exceptions as curl_exceptions
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ssu_lecture_downloader.credentials import CredentialPair, CredentialStore, default_credential_store
from ssu_lecture_downloader.dependency_installer import install_missing_media_components, install_supported, normalize_components
from ssu_lecture_downloader.service import SsuLectureService
from ssu_lecture_downloader.postprocess_media import AudioExtractionRequest, ensure_playable_mp4, extract_audio, ffmpeg_status, plan_audio_output_path
from ssu_lecture_downloader.stt import DEFAULT_STT_MODEL, TranscriptionRequest, check_stt_provider, transcribe_audio, transcript_output_paths


MAX_JOB_PROGRESS_EVENTS = 400
MAX_ARTIFACT_POSTPROCESS_WORKERS = 1
COALESCED_PROGRESS_STAGES = {"download_progress", "audio_progress", "transcript_progress"}


class DownloadJob:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.status = "queued"
        self.cancel_requested = False
        self.progress: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error_code: str | None = None
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "job_id": self.job_id,
                "status": self.status,
                "cancel_requested": self.cancel_requested,
                "progress": list(self.progress),
                "result": self.result,
                "error_code": self.error_code,
            }

    def append_progress(self, event: dict[str, Any]) -> None:
        with self._lock:
            _append_compacted_progress(self.progress, event)

    def mark_cancel_requested(self) -> None:
        with self._lock:
            self.cancel_requested = True
            if self.status == "queued":
                self.status = "cancelled"

    def should_cancel(self) -> bool:
        with self._lock:
            return self.cancel_requested

    def set_status(self, status: str, *, result: dict[str, Any] | None = None, error_code: str | None = None) -> None:
        with self._lock:
            self.status = status
            self.result = result
            self.error_code = error_code


class DownloadJobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()

    def create(self) -> DownloadJob:
        job = DownloadJob(uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.get(job_id)


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


ServiceFactory = Callable[[], Any]


def _progress_identity(event: dict[str, Any]) -> tuple[object, object, object, object]:
    return (
        event.get("stage"),
        event.get("index"),
        str(event.get("module_item_id")) if event.get("module_item_id") is not None else None,
        event.get("output_path") or event.get("input_path") or event.get("audio_path"),
    )


def _append_compacted_progress(progress: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """Append progress without retaining every byte-level tick forever."""
    if event.get("stage") in COALESCED_PROGRESS_STAGES:
        identity = _progress_identity(event)
        for index in range(len(progress) - 1, -1, -1):
            if _progress_identity(progress[index]) == identity:
                progress[index] = event
                break
        else:
            progress.append(event)
    else:
        progress.append(event)
    overflow = len(progress) - MAX_JOB_PROGRESS_EVENTS
    if overflow > 0:
        del progress[:overflow]


AUTHENTICATION_ERROR_CODES = {
    "smartid_login_rejected",
    "authentication_failed",
    "missing_credentials",
    "session_expired",
    "sso_redirect_missing",
    "iframe_src_missing",
    "canvas_form_missing",
    "login_cryption_missing",
    "token_decryption_failed",
    "xn_api_token_missing",
    "unknown_response_shape",
    "lms_session_unverified",
}


def _credentials(store: CredentialStore) -> tuple[str, str] | None:
    pair = store.get()
    if pair is None:
        return None
    return pair.username, pair.password


def _missing_credentials_error() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "ok": False,
            "error_code": "missing_credentials",
            "credentials": {"SSU_USERNAME": "missing", "SSU_PASSWORD": "missing"},
        },
    )


def _authentication_failed_error(error_code: str | None) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"ok": False, "error_code": error_code or "authentication_failed"},
    )


def _selected_output_dir(value: str | None, default: Path) -> Path:
    return Path(value).expanduser() if value else default


def _status_for_error_code(error_code: str | None) -> int:
    if (error_code or "authentication_failed") in AUTHENTICATION_ERROR_CODES:
        return HTTPStatus.UNAUTHORIZED
    return HTTPStatus.BAD_GATEWAY


def _service_error(error_code: str | None, *, message: str | None = None, detail: dict[str, Any] | None = None) -> HTTPException:
    code = error_code or "service_error"
    payload: dict[str, Any] = {"ok": False, "error_code": code}
    if message:
        payload["message"] = message
    if detail:
        payload["detail"] = detail
    return HTTPException(status_code=_status_for_error_code(code), detail=payload)


def _clear_service_cache(service: Any) -> None:
    clear_cache = getattr(service, "clear_cache", None)
    if clear_cache:
        clear_cache()


def _service_exception_error(exc: Exception, service: Any) -> HTTPException:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        _clear_service_cache(service)
        return _service_error("session_expired")
    if isinstance(exc, curl_exceptions.HTTPError):
        code, message, detail = _lms_http_error_detail(exc, service)
        return _service_error(code, message=message, detail=detail)
    if isinstance(exc, RuntimeError) and str(exc) == "attendance item does not expose Commons content_id":
        return _service_error(
            "media_content_id_missing",
            message="선택한 항목은 영상/자료 다운로드 주소가 없습니다. 강의 목록을 새로고침한 뒤 MP4/MP3/TXT는 영상 항목만 선택해 주세요.",
            detail={"step": "parse_attendance_item_media", "reason": "content_id_missing"},
        )
    raise exc


def _raise_for_result_error(error_code: str | None) -> None:
    raise _service_error(error_code)


def _background_job_error_code(exc: Exception, service: Any) -> str:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        _clear_service_cache(service)
        return "session_expired"
    if isinstance(exc, curl_exceptions.HTTPError):
        return _lms_http_error_detail(exc, service)[0]
    return type(exc).__name__


def _last_trace_entry(service: Any) -> dict[str, Any] | None:
    http = getattr(service, "http", None)
    trace = getattr(http, "trace", None)
    to_dict = getattr(trace, "to_dict", None)
    if not to_dict:
        return None
    try:
        entries = to_dict().get("entries", [])
    except Exception:
        return None
    return entries[-1] if entries else None


def _lms_http_error_detail(exc: Exception, service: Any) -> tuple[str, str, dict[str, Any]]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    trace_entry = _last_trace_entry(service) or {}
    step = str(trace_entry.get("step") or "unknown_step")
    status = status_code or trace_entry.get("status_code") or "unknown_status"
    host = None
    final_url = trace_entry.get("final_url") or trace_entry.get("url")
    if isinstance(final_url, str):
        from urllib.parse import urlsplit

        host = urlsplit(final_url).netloc or None
    code = f"lms_request_failed:{step}:{status}"
    message = f"LMS/Commons 요청 실패: {step} 단계에서 HTTP {status} 응답을 받았습니다."
    detail = {
        "step": step,
        "status_code": status,
        "host": host,
        "elapsed_ms": trace_entry.get("elapsed_ms"),
        "body_size": trace_entry.get("body_size"),
    }
    return code, message, detail


def _download_payload(result, progress: list[dict[str, Any]]) -> dict[str, Any]:
    return {**result.__dict__, "progress": progress}


def _native_download_payload(plan_result) -> dict[str, Any]:
    if isinstance(plan_result, dict):
        result = plan_result["result"]
        native_download = plan_result.get("native_download")
    else:
        result = plan_result.result
        native_download = plan_result.native_download
    payload = _download_payload(result, [])
    payload["native_download"] = asdict(native_download) if native_download is not None and not isinstance(native_download, dict) else native_download
    return payload


def _viewer_plan_payload(result) -> dict[str, Any]:
    return result if isinstance(result, dict) else asdict(result)


def _batch_payload(result, progress: list[dict[str, Any]]) -> dict[str, Any]:
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


def _normalize_artifact_outputs(outputs: list[str]) -> list[str]:
    normalized: list[str] = []
    for output in outputs:
        value = output.lower().strip()
        if value not in {"mp4", "mp3", "txt"}:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "invalid_artifact_output"})
        if value not in normalized:
            normalized.append(value)
    if not normalized:
        raise HTTPException(status_code=400, detail={"ok": False, "error_code": "missing_artifact_outputs"})
    return normalized


def _artifact_payload(download_payload: dict[str, Any], artifacts: list[dict[str, Any]], progress: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "error_code": None,
        "download": download_payload,
        "artifacts": artifacts,
        "progress": progress,
    }


def create_app(
    *,
    service_factory: ServiceFactory | None = None,
    default_output_dir: Path | None = None,
    credential_store: CredentialStore | None = None,
) -> FastAPI:
    app = FastAPI(title="SSU Lecture Downloader Local API")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(http://127\.0\.0\.1:\d+|http://localhost:\d+|tauri://localhost)$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    service = (service_factory or (lambda: SsuLectureService()))()
    output_dir = default_output_dir or (Path.cwd() / ".local" / "downloads")
    credentials = credential_store or default_credential_store()
    job_registry = DownloadJobRegistry()
    audio_job_registry = DownloadJobRegistry()
    stt_job_registry = DownloadJobRegistry()
    dependency_install_job_registry = DownloadJobRegistry()
    artifact_job_registry = DownloadJobRegistry()

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request, exc: HTTPException):
        from fastapi.responses import JSONResponse

        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error_code": str(exc.detail)})

    @app.get("/api/health")
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
            ],
        }

    @app.get("/api/credentials/status")
    def credential_status() -> dict[str, object]:
        return {"ok": True, **credentials.status()}

    @app.post("/api/credentials")
    def save_credentials(request: CredentialRequest) -> dict[str, object]:
        username = request.username.strip()
        password = request.password.strip()
        if not username or not password:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "invalid_credentials_payload"})
        _clear_service_cache(service)
        login = service.login(username, password)
        if not login.ok:
            raise _authentication_failed_error(login.error_code)
        credentials.save(CredentialPair(username=username, password=password))
        return {"ok": True, **credentials.status()}

    @app.delete("/api/credentials")
    def delete_credentials() -> dict[str, object]:
        credentials.delete()
        _clear_service_cache(service)
        return {"ok": True, **credentials.status()}

    @app.get("/api/terms")
    def terms() -> dict[str, object]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        login = service.login(creds[0], creds[1])
        if not login.ok:
            raise _authentication_failed_error(login.error_code)
        try:
            listed_terms = service.list_terms(creds[0], creds[1])
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        if not listed_terms:
            _clear_service_cache(service)
            raise _authentication_failed_error("lms_session_unverified")
        return {"ok": True, "error_code": None, "terms": listed_terms}

    @app.get("/api/courses")
    def courses(term_id: int | None = None) -> dict[str, object]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        term_ids = [term_id] if term_id is not None else None
        try:
            result = service.discover_courses(creds[0], creds[1], term_ids=term_ids)
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        if not result.ok:
            _raise_for_result_error(result.error_code)
        return {
            "ok": True,
            "error_code": None,
            "course_count": result.course_count,
            "term_ids": term_ids,
            "courses": [course.model_dump() for course in result.courses],
        }

    @app.get("/api/courses/{course_id}")
    def course_detail(course_id: str, course_name: str = "") -> dict[str, object]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        try:
            result = service.get_course_detail(creds[0], creds[1], course_id=course_id, course_name=course_name)
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        if not result.ok or result.course is None:
            _raise_for_result_error(result.error_code)
        content_items = result.content_items or []
        return {
            "ok": True,
            "error_code": None,
            "course": result.course.model_dump(),
            "lecture_count": len(result.lectures),
            "lectures": [lecture.model_dump() for lecture in result.lectures],
            "content_count": len(content_items),
            "content_items": [item.model_dump() for item in content_items],
        }

    @app.post("/api/download/plan")
    def download_plan(request: DownloadPlanRequest) -> dict[str, object]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        selected_output_dir = _selected_output_dir(request.output_dir, output_dir)
        try:
            result = service.download_lecture(
                creds[0],
            creds[1],
            course_id=request.course_id,
            module_item_id=request.module_item_id,
            course_name=request.course_name,
            output_dir=selected_output_dir,
            dry_run=True,
                conflict_strategy=request.conflict_strategy,
            )
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        if not result.ok:
            _raise_for_result_error(result.error_code)
        return _download_payload(result, [])

    @app.post("/api/download/native-plan")
    def native_download_plan(request: DownloadPlanRequest) -> dict[str, object]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        selected_output_dir = _selected_output_dir(request.output_dir, output_dir)
        try:
            result = service.native_download_plan(
                creds[0],
                creds[1],
                course_id=request.course_id,
                module_item_id=request.module_item_id,
                course_name=request.course_name,
                output_dir=selected_output_dir,
                conflict_strategy=request.conflict_strategy,
            )
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        download_result = result["result"] if isinstance(result, dict) else result.result
        if not download_result.ok:
            _raise_for_result_error(download_result.error_code)
        return _native_download_payload(result)

    @app.post("/api/watch/viewer-plan")
    def official_viewer_plan(request: DownloadPlanRequest) -> dict[str, object]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        try:
            result = service.official_viewer_plan(
                creds[0],
                creds[1],
                course_id=request.course_id,
                module_item_id=request.module_item_id,
            )
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        if not result.ok:
            _raise_for_result_error(result.error_code)
        return _viewer_plan_payload(result)

    @app.post("/api/watch/session-bridge-plan")
    def official_viewer_session_bridge_plan(request: DownloadPlanRequest) -> dict[str, object]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        try:
            result = service.official_viewer_session_bridge_plan(
                creds[0],
                creds[1],
                course_id=request.course_id,
                module_item_id=request.module_item_id,
            )
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        if not result.ok:
            _raise_for_result_error(result.error_code)
        return _viewer_plan_payload(result)

    @app.post("/api/download")
    def download(request: DownloadRequest) -> dict[str, object]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        selected_output_dir = _selected_output_dir(request.output_dir, output_dir)
        progress: list[dict[str, Any]] = []
        try:
            result = service.download_lecture(
                creds[0],
                creds[1],
                course_id=request.course_id,
                module_item_id=request.module_item_id,
                course_name=request.course_name,
                output_dir=selected_output_dir,
                dry_run=False,
                on_progress=lambda event: _append_compacted_progress(progress, event),
                conflict_strategy=request.conflict_strategy,
            )
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        if not result.ok:
            _raise_for_result_error(result.error_code)
        return _download_payload(result, progress)

    @app.post("/api/download/jobs")
    def start_download_job(request: DownloadRequest) -> dict[str, Any]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        selected_output_dir = _selected_output_dir(request.output_dir, output_dir)
        job = job_registry.create()

        def run_job() -> None:
            job.set_status("running")
            progress: list[dict[str, Any]] = []
            progress_lock = threading.Lock()

            def on_progress(event: dict[str, Any]) -> None:
                with progress_lock:
                    _append_compacted_progress(progress, event)
                job.append_progress(event)

            try:
                result = service.download_lecture(
                    creds[0],
                    creds[1],
                    course_id=request.course_id,
                    module_item_id=request.module_item_id,
                    course_name=request.course_name,
                    output_dir=selected_output_dir,
                    dry_run=False,
                    on_progress=on_progress,
                    should_cancel=job.should_cancel,
                    conflict_strategy=request.conflict_strategy,
                )
                if not result.ok:
                    job.set_status("failed", error_code=result.error_code or "download_failed")
                    return
                status = "cancelled" if job.should_cancel() else "succeeded"
                job.set_status(status, result=_download_payload(result, progress))
            except Exception as exc:
                status = "cancelled" if job.should_cancel() or type(exc).__name__ == "DownloadCancelled" else "failed"
                job.set_status(status, error_code=_background_job_error_code(exc, service))

        threading.Thread(target=run_job, daemon=True).start()
        return job.snapshot()

    @app.post("/api/download/batch/jobs")
    def start_batch_download_job(request: BatchDownloadRequest) -> dict[str, Any]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        if request.mode not in {"selected", "uncompleted", "all"}:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "invalid_batch_mode"})
        if request.mode == "selected" and not request.module_item_ids:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "missing_module_item_ids"})
        selected_output_dir = _selected_output_dir(request.output_dir, output_dir)
        job = job_registry.create()

        def run_job() -> None:
            job.set_status("running")
            progress: list[dict[str, Any]] = []
            progress_lock = threading.Lock()

            def on_progress(event: dict[str, Any]) -> None:
                with progress_lock:
                    _append_compacted_progress(progress, event)
                job.append_progress(event)

            try:
                result = service.batch_download_lectures(
                    creds[0],
                    creds[1],
                    course_id=request.course_id,
                    course_name=request.course_name,
                    output_dir=selected_output_dir,
                    dry_run=False,
                    module_item_ids=request.module_item_ids if request.mode == "selected" else None,
                    include_completed=request.mode == "all",
                    on_progress=on_progress,
                    should_cancel=job.should_cancel,
                    conflict_strategy=request.conflict_strategy,
                    max_parallel_downloads=request.max_parallel_downloads,
                )
                if not result.ok:
                    job.set_status("failed", error_code=result.error_code or "download_failed")
                    return
                status = "cancelled" if job.should_cancel() else "succeeded"
                job.set_status(status, result=_batch_payload(result, progress))
            except Exception as exc:
                status = "cancelled" if job.should_cancel() or type(exc).__name__ == "DownloadCancelled" else "failed"
                job.set_status(status, error_code=_background_job_error_code(exc, service))

        threading.Thread(target=run_job, daemon=True).start()
        return job.snapshot()

    @app.get("/api/download/jobs/{job_id}")
    def download_job_status(job_id: str) -> dict[str, Any]:
        job = job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "download_job_not_found"})
        return job.snapshot()

    @app.post("/api/download/jobs/{job_id}/cancel")
    def cancel_download_job(job_id: str) -> dict[str, Any]:
        job = job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "download_job_not_found"})
        job.mark_cancel_requested()
        return job.snapshot()

    def _run_batch(request: BatchDownloadRequest, *, dry_run: bool) -> dict[str, Any]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        if request.mode not in {"selected", "uncompleted", "all"}:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "invalid_batch_mode"})
        if request.mode == "selected" and not request.module_item_ids:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "missing_module_item_ids"})
        selected_output_dir = _selected_output_dir(request.output_dir, output_dir)
        progress: list[dict[str, Any]] = []
        try:
            result = service.batch_download_lectures(
                creds[0],
                creds[1],
                course_id=request.course_id,
                course_name=request.course_name,
                output_dir=selected_output_dir,
                dry_run=dry_run,
                module_item_ids=request.module_item_ids if request.mode == "selected" else None,
                include_completed=request.mode == "all",
                on_progress=lambda event: _append_compacted_progress(progress, event),
                conflict_strategy=request.conflict_strategy,
                max_parallel_downloads=request.max_parallel_downloads,
            )
        except Exception as exc:
            raise _service_exception_error(exc, service) from exc
        if not result.ok:
            _raise_for_result_error(result.error_code)
        return _batch_payload(result, progress)

    @app.post("/api/download/batch/plan")
    def batch_download_plan(request: BatchDownloadRequest) -> dict[str, Any]:
        return _run_batch(request, dry_run=True)

    @app.post("/api/download/batch")
    def batch_download(request: BatchDownloadRequest) -> dict[str, Any]:
        return _run_batch(request, dry_run=False)

    @app.post("/api/media/video/ensure-playable")
    def ensure_playable_video(request: VideoEnsurePlayableRequest) -> dict[str, Any]:
        progress: list[dict[str, Any]] = []
        try:
            result = ensure_playable_mp4(request.input_path, overwrite=request.overwrite, on_progress=lambda event: _append_compacted_progress(progress, event))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={"ok": False, "error_code": "video_reconstruction_failed", "message": str(exc)},
            ) from exc
        payload = result.model_dump(mode="json")
        payload["progress"] = progress
        if not result.ok:
            raise HTTPException(status_code=422, detail=payload)
        return payload

    @app.post("/api/media/audio/jobs")
    def start_audio_extraction_job(request: AudioExtractionRequest) -> dict[str, Any]:
        job = audio_job_registry.create()

        def run_job() -> None:
            job.set_status("running")
            job.append_progress({"stage": "extracting_audio", "input_path": str(request.input_path)})
            result = extract_audio(request)
            if not result.ok:
                job.set_status("failed", result=result.model_dump(mode="json"), error_code=result.error_code or "audio_extraction_failed")
                return
            job.append_progress({"stage": "audio_done", "output_path": str(result.output_path) if result.output_path else None, "file_size": result.file_size})
            job.set_status("succeeded", result=result.model_dump(mode="json"))

        thread = threading.Thread(target=run_job, daemon=True)
        thread.start()
        thread.join(timeout=0.2)
        return job.snapshot()

    @app.get("/api/media/audio/jobs/{job_id}")
    def audio_extraction_job_status(job_id: str) -> dict[str, Any]:
        job = audio_job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "audio_job_not_found"})
        return job.snapshot()

    @app.post("/api/media/audio/jobs/{job_id}/cancel")
    def cancel_audio_extraction_job(job_id: str) -> dict[str, Any]:
        job = audio_job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "audio_job_not_found"})
        job.mark_cancel_requested()
        return job.snapshot()

    def _stt_provider_status_payload() -> list[dict[str, object]]:
        providers = []
        for provider in ["faster_whisper", "whisper_cpp"]:
            status = check_stt_provider(provider)
            providers.append(status if isinstance(status, dict) else status.model_dump())
        return providers

    @app.get("/api/stt/providers/status")
    def stt_provider_status() -> dict[str, object]:
        return {"ok": True, "providers": _stt_provider_status_payload()}

    @app.get("/api/media/dependencies/status")
    def media_dependency_status() -> dict[str, object]:
        auto_install_available, auto_install_unavailable_reason = install_supported()
        return {
            "ok": True,
            "packaging_mode": "app_bundled",
            "auto_install_available": auto_install_available,
            "auto_install_unavailable_reason": auto_install_unavailable_reason,
            "dependencies": [ffmpeg_status()],
            "providers": _stt_provider_status_payload(),
        }

    @app.post("/api/media/dependencies/install/jobs")
    def start_media_dependency_install_job(request: MediaDependencyInstallRequest) -> dict[str, Any]:
        try:
            components = normalize_components(request.components)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": str(exc).split(":", 1)[0]}) from exc
        job = dependency_install_job_registry.create()

        def run_job() -> None:
            job.set_status("running")
            job.append_progress({"stage": "dependency_install_checking", "components": components})
            result = install_missing_media_components(components, on_progress=job.append_progress)
            if not result.get("ok"):
                job.set_status("failed", result=result, error_code=str(result.get("error_code") or "dependency_install_failed"))
                return
            job.set_status("succeeded", result=result)

        thread = threading.Thread(target=run_job, daemon=True)
        thread.start()
        thread.join(timeout=0.2)
        return job.snapshot()

    @app.get("/api/media/dependencies/install/jobs/{job_id}")
    def media_dependency_install_job_status(job_id: str) -> dict[str, Any]:
        job = dependency_install_job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "dependency_install_job_not_found"})
        return job.snapshot()

    @app.post("/api/stt/jobs")
    def start_stt_job(request: TranscriptionRequest) -> dict[str, Any]:
        job = stt_job_registry.create()

        def run_job() -> None:
            job.set_status("running")
            job.append_progress({"stage": "transcribing", "audio_path": str(request.audio_path), "provider": request.provider, "model": request.model})
            result = transcribe_audio(request)
            if not result.ok:
                job.set_status("failed", result=result.model_dump(mode="json"), error_code=result.error_code or "stt_failed")
                return
            job.append_progress({"stage": "transcript_done", "text_path": str(result.text_path) if result.text_path else None})
            job.set_status("succeeded", result=result.model_dump(mode="json"))

        thread = threading.Thread(target=run_job, daemon=True)
        thread.start()
        thread.join(timeout=0.2)
        return job.snapshot()

    @app.get("/api/stt/jobs/{job_id}")
    def stt_job_status(job_id: str) -> dict[str, Any]:
        job = stt_job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "stt_job_not_found"})
        return job.snapshot()

    @app.post("/api/stt/jobs/{job_id}/cancel")
    def cancel_stt_job(job_id: str) -> dict[str, Any]:
        job = stt_job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "stt_job_not_found"})
        job.mark_cancel_requested()
        return job.snapshot()

    @app.post("/api/download/artifacts/jobs")
    def start_artifact_download_job(request: ArtifactDownloadRequest) -> dict[str, Any]:
        creds = _credentials(credentials)
        if creds is None:
            raise _missing_credentials_error()
        outputs = _normalize_artifact_outputs(request.outputs)
        if request.mode not in {"selected", "uncompleted", "all"}:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "invalid_batch_mode"})
        if request.mode == "selected" and not request.module_item_ids:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "missing_module_item_ids"})
        if request.audio_format not in {"mp3", "wav", "m4a"}:
            raise HTTPException(status_code=400, detail={"ok": False, "error_code": "invalid_audio_format"})
        selected_output_dir = _selected_output_dir(request.output_dir, output_dir)
        job = artifact_job_registry.create()

        def run_job() -> None:
            job.set_status("running")
            progress: list[dict[str, Any]] = []
            progress_lock = threading.Lock()

            def on_progress(event: dict[str, Any]) -> None:
                with progress_lock:
                    _append_compacted_progress(progress, event)
                job.append_progress(event)

            try:
                artifact_conflict_strategy = "overwrite" if request.conflict_strategy == "overwrite" else "skip"
                plan_result = service.batch_download_lectures(
                    creds[0],
                    creds[1],
                    course_id=request.course_id,
                    course_name=request.course_name,
                    output_dir=selected_output_dir,
                    dry_run=True,
                    module_item_ids=request.module_item_ids if request.mode == "selected" else None,
                    include_completed=request.mode == "all",
                    on_progress=on_progress,
                    should_cancel=job.should_cancel,
                    conflict_strategy=artifact_conflict_strategy,
                    max_parallel_downloads=request.max_parallel_downloads,
                )
                if not plan_result.ok:
                    job.set_status("failed", error_code=plan_result.error_code or "download_plan_failed")
                    return

                outputs_need_audio = "mp3" in outputs or "txt" in outputs
                planned_items = [item for item in plan_result.items if getattr(item, "output_path", None)]
                needs_video_ids: list[int | str] = []
                for item in planned_items:
                    video_path = Path(str(item.output_path))
                    audio_path = plan_audio_output_path(video_path, video_path.parent, request.audio_format)
                    text_path, md_path, json_path = transcript_output_paths(audio_path, video_path.parent)
                    transcript_exists = any(path.exists() for path in (text_path, md_path, json_path))
                    audio_satisfied = audio_path.exists() and not request.overwrite_artifacts
                    transcript_satisfied = transcript_exists and not request.overwrite_artifacts
                    needs_mp4_file = "mp4" in outputs and (request.overwrite_artifacts or not video_path.exists())
                    needs_audio_source = outputs_need_audio and not audio_satisfied and not video_path.exists()
                    needs_transcript_source = "txt" in outputs and not transcript_satisfied and not audio_path.exists() and not video_path.exists()
                    if needs_mp4_file or needs_audio_source or needs_transcript_source:
                        needs_video_ids.append(item.module_item_id)

                downloaded_by_id: dict[str, Any] = {}
                if needs_video_ids:
                    batch_result = service.batch_download_lectures(
                        creds[0],
                        creds[1],
                        course_id=request.course_id,
                        course_name=request.course_name,
                        output_dir=selected_output_dir,
                        dry_run=False,
                        module_item_ids=needs_video_ids,
                        include_completed=True,
                        on_progress=on_progress,
                        should_cancel=job.should_cancel,
                        conflict_strategy=artifact_conflict_strategy,
                        max_parallel_downloads=request.max_parallel_downloads,
                    )
                    if not batch_result.ok:
                        job.set_status("failed", error_code=batch_result.error_code or "download_failed")
                        return
                    downloaded_by_id = {str(item.module_item_id): item for item in batch_result.items}
                    download_payload = _batch_payload(batch_result, progress)
                else:
                    download_payload = _batch_payload(plan_result, progress)
                    on_progress({"stage": "artifact_downloads_skipped", "total": len(planned_items)})

                downloaded_items = [downloaded_by_id.get(str(item.module_item_id), item) for item in planned_items]

                artifacts: list[dict[str, Any]] = []
                artifacts_lock = threading.Lock()
                wants_audio = "mp3" in outputs or "txt" in outputs
                processable_items = [(index, item) for index, item in enumerate(downloaded_items, start=1) if getattr(item, "output_path", None)]
                if wants_audio:
                    on_progress({"stage": "artifact_audio_started", "total": len(processable_items), "max_parallel_downloads": request.max_parallel_downloads})
                if "txt" in outputs:
                    on_progress({"stage": "artifact_transcripts_queued", "total": len(processable_items), "max_parallel_downloads": request.max_parallel_downloads})

                def add_artifact(artifact: dict[str, Any]) -> None:
                    with artifacts_lock:
                        artifacts.append(artifact)

                def process_artifact_item(index_and_item: tuple[int, Any]) -> str | None:
                    index, item = index_and_item
                    if job.should_cancel():
                        return "cancelled"
                    output_path = getattr(item, "output_path", None)
                    if not output_path:
                        return None
                    video_path = Path(output_path)
                    module_item_id = getattr(item, "module_item_id", None)
                    title = getattr(item, "title", None)
                    artifact_event_context = {"index": index, "total": len(downloaded_items), "module_item_id": module_item_id, "title": title}
                    video_exists = video_path.exists()
                    video_was_downloaded = bool(getattr(item, "written", False))
                    audio_path = plan_audio_output_path(video_path, video_path.parent, request.audio_format)
                    text_path, md_path, json_path = transcript_output_paths(audio_path, video_path.parent)
                    transcript_exists = any(path.exists() for path in (text_path, md_path, json_path))
                    if "mp4" in outputs and video_exists:
                        def on_video_progress(event: dict[str, Any]) -> None:
                            on_progress({**event, **artifact_event_context})

                        video_result = ensure_playable_mp4(video_path, overwrite=request.overwrite_artifacts, on_progress=on_video_progress)
                        add_artifact({"kind": "mp4", **video_result.model_dump(mode="json"), "file_exists": getattr(item, "file_exists", False), "skipped": getattr(item, "skipped", False)})
                        if not video_result.ok:
                            return video_result.error_code or "video_reconstruction_failed"
                    elif "mp4" in outputs:
                        return "video_not_found"
                    if wants_audio:
                        if audio_path.exists() and not request.overwrite_artifacts:
                            add_artifact({"kind": request.audio_format, "ok": True, "input_path": str(video_path), "output_path": str(audio_path), "file_size": audio_path.stat().st_size, "file_exists": True, "skipped": True})
                            on_progress({"stage": "audio_skipped", **artifact_event_context, "output_path": str(audio_path), "file_size": audio_path.stat().st_size, "progress_percent": 100})
                        else:
                            if not video_exists:
                                return "source_video_not_found"
                            on_progress({"stage": "extracting_audio", **artifact_event_context, "output_path": str(video_path)})

                            def on_audio_progress(event: dict[str, Any]) -> None:
                                on_progress({**event, **artifact_event_context})

                            audio_request = AudioExtractionRequest(input_path=video_path, output_dir=video_path.parent, format=request.audio_format, overwrite=request.overwrite_artifacts)
                            try:
                                audio_result = extract_audio(audio_request, on_progress=on_audio_progress)
                            except TypeError:
                                # Test doubles or older adapters may still expose the pre-progress signature.
                                audio_result = extract_audio(audio_request)
                            add_artifact({"kind": request.audio_format, **audio_result.model_dump(mode="json")})
                            if not audio_result.ok:
                                return audio_result.error_code or "audio_extraction_failed"
                            audio_path = audio_result.output_path
                            on_progress({"stage": "audio_done", **artifact_event_context, "output_path": str(audio_path), "file_size": audio_result.file_size, "progress_percent": 100})
                    if "txt" in outputs:
                        if transcript_exists and not request.overwrite_artifacts:
                            add_artifact({"kind": "txt", "ok": True, "audio_path": str(audio_path), "text_path": str(text_path), "md_path": str(md_path), "json_path": str(json_path), "segments": [], "file_exists": True, "skipped": True})
                            on_progress({"stage": "transcript_skipped", **artifact_event_context, "output_path": str(text_path), "progress_percent": 100})
                        else:
                            if not audio_path.exists():
                                return "audio_not_found"
                            on_progress({"stage": "transcribing", **artifact_event_context, "output_path": str(audio_path), "provider": request.stt_provider, "model": request.stt_model})

                            def on_transcript_progress(event: dict[str, Any]) -> None:
                                on_progress({**event, **artifact_event_context})

                            stt_request = TranscriptionRequest(audio_path=audio_path, output_dir=video_path.parent, provider=request.stt_provider, language=request.stt_language, model=request.stt_model, overwrite=request.overwrite_artifacts)
                            try:
                                stt_result = transcribe_audio(stt_request, on_progress=on_transcript_progress)
                            except TypeError:
                                stt_result = transcribe_audio(stt_request)
                            add_artifact({"kind": "txt", **stt_result.model_dump(mode="json")})
                            if not stt_result.ok:
                                return stt_result.error_code or "stt_failed"
                            on_progress({"stage": "transcript_done", **artifact_event_context, "output_path": str(stt_result.text_path) if stt_result.text_path else None, "progress_percent": 100})
                    if "mp4" not in outputs and video_exists and video_was_downloaded:
                        video_path.unlink(missing_ok=True)
                        on_progress({"stage": "source_video_removed", **artifact_event_context, "output_path": str(video_path)})
                    return None

                if processable_items:
                    max_workers = max(1, min(request.max_parallel_downloads, MAX_ARTIFACT_POSTPROCESS_WORKERS, len(processable_items)))
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = [executor.submit(process_artifact_item, item) for item in processable_items]
                        for future in as_completed(futures):
                            error_code = future.result()
                            if error_code:
                                job.set_status("failed", result=_artifact_payload(download_payload, artifacts, progress), error_code=error_code)
                                return
                status = "cancelled" if job.should_cancel() else "succeeded"
                job.set_status(status, result=_artifact_payload(download_payload, artifacts, progress))
            except Exception as exc:
                status = "cancelled" if job.should_cancel() or type(exc).__name__ == "DownloadCancelled" else "failed"
                job.set_status(status, error_code=_background_job_error_code(exc, service))

        threading.Thread(target=run_job, daemon=True).start()
        return job.snapshot()

    @app.get("/api/download/artifacts/jobs/{job_id}")
    def artifact_download_job_status(job_id: str) -> dict[str, Any]:
        job = artifact_job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "artifact_job_not_found"})
        return job.snapshot()

    @app.post("/api/download/artifacts/jobs/{job_id}/cancel")
    def cancel_artifact_download_job(job_id: str) -> dict[str, Any]:
        job = artifact_job_registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail={"ok": False, "error_code": "artifact_job_not_found"})
        job.mark_cancel_requested()
        return job.snapshot()

    return app


app = create_app()
