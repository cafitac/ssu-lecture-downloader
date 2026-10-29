"""State shared by the API routers (one instance per `create_app`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException

from ssu_lecture_downloader.service.api_errors import (
    background_job_error_code,
    missing_credentials_error,
    service_exception_error,
)
from ssu_lecture_downloader.core.chrome_watch import ChromeWatchManager
from ssu_lecture_downloader.core.credentials import CredentialStore
from ssu_lecture_downloader.core.jobs import Job, JobOutcome, JobRegistry, JobRunner
from ssu_lecture_downloader.service.security import MediaPathPolicy
from ssu_lecture_downloader.core.stt_remote import RemoteSTTService

APP_DOWNLOAD_FOLDER_NAME = "SSU Lecture Downloader"

T = TypeVar("T")


def default_download_dir() -> Path:
    """Where downloads go when the request names no folder.

    Deliberately not relative to the working directory: a packaged app starts with
    its cwd inside the .app bundle, which is read-only or discarded on update.
    """
    return Path.home() / "Downloads" / APP_DOWNLOAD_FOLDER_NAME


@dataclass
class AppContext:
    service: Any
    credentials: CredentialStore
    default_output_dir: Path
    media_paths: MediaPathPolicy
    runner: JobRunner
    download_jobs: JobRegistry = field(default_factory=JobRegistry)
    audio_jobs: JobRegistry = field(default_factory=JobRegistry)
    stt_jobs: JobRegistry = field(default_factory=JobRegistry)
    dependency_jobs: JobRegistry = field(default_factory=JobRegistry)
    artifact_jobs: JobRegistry = field(default_factory=JobRegistry)
    remote_stt: RemoteSTTService = field(default_factory=RemoteSTTService)
    # Built by the watch router (it needs the credentials/service helpers above) unless injected.
    chrome_watch: ChromeWatchManager | None = None

    def require_credentials(self) -> tuple[str, str]:
        pair = self.credentials.get()
        if pair is None:
            raise missing_credentials_error()
        return pair.username, pair.password

    def output_dir_for(self, value: str | None) -> Path:
        """The request's folder (the user's explicit choice) or the default download folder."""
        if not value:
            return self.default_output_dir
        chosen = Path(value).expanduser()
        self.media_paths.add_root(chosen)
        return chosen

    def remember_outputs(self, *paths: str | Path | None) -> None:
        self.media_paths.add_produced(paths)

    def job_error_code(self, exc: Exception) -> str:
        return background_job_error_code(exc, self.service)

    def call_service_error(self, exc: Exception) -> HTTPException:
        """The API error a service exception maps to (for callers outside a request)."""
        return service_exception_error(exc, self.service)

    def call_service(self, operation: Callable[[], T]) -> T:
        """Run a service call, mapping any failure to the JSON error shape."""
        try:
            return operation()
        except Exception as exc:
            raise service_exception_error(exc, self.service) from exc

    def start_job(self, registry: JobRegistry, work: Callable[[Job], JobOutcome | None], *, wait_seconds: float = 0.0) -> dict[str, Any]:
        """Start background work and return the job snapshot the start endpoints respond with."""
        return self.runner.start(registry, work, wait_seconds=wait_seconds).snapshot()


def job_or_404(registry: JobRegistry, job_id: str, error_code: str):
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error_code": error_code})
    return job


def add_job_routes(router: APIRouter, registry: JobRegistry, *, path: str, not_found_code: str) -> None:
    """GET status and POST cancel for `<path>/{job_id}`, shared by every job kind."""

    @router.get(path + "/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        return job_or_404(registry, job_id, not_found_code).snapshot()

    @router.post(path + "/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        job = job_or_404(registry, job_id, not_found_code)
        job.mark_cancel_requested()
        return job.snapshot()
