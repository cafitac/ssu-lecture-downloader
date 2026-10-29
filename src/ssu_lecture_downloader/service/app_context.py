"""State shared by the API routers (one instance per `create_app`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException

from ssu_lecture_downloader.platforms import select_platform
from ssu_lecture_downloader.platforms.base import Platform
from ssu_lecture_downloader.service.api_errors import (
    background_job_error_code,
    missing_credentials_error,
    service_exception_error,
)
from ssu_lecture_downloader.core.chrome_watch import ChromeWatchManager
from ssu_lecture_downloader.service.credentials import CredentialStore
from ssu_lecture_downloader.core.jobs import Job, JobOutcome, JobRegistry, JobRunner
from ssu_lecture_downloader.service.security import MediaPathPolicy
from ssu_lecture_downloader.core.stt_remote import RemoteSTTService
from ssu_lecture_downloader.core.stt_remote_config import RemoteSTTSettingsStore

# The platform adapter is chosen here and nowhere else in `service`: other modules reach it as
# `ctx.platform` and must not import `select_platform` / `Platform` (guarded in tests/test_import_boundaries.py).
__all__ = ["AppContext", "build_app_context", "build_remote_stt", "default_download_dir"]

T = TypeVar("T")


def default_download_dir(platform: Platform | None = None) -> Path:
    """Where downloads go when the request names no folder (the platform adapter decides)."""
    return (platform or select_platform()).default_download_dir()


def build_app_context(
    *,
    service: Any,
    credentials: CredentialStore,
    runner: JobRunner,
    platform: Platform | None = None,
    default_output_dir: Path | None = None,
    remote_stt: RemoteSTTService | None = None,
    chrome_watch: ChromeWatchManager | None = None,
) -> AppContext:
    """An AppContext on `platform` (selected for this OS when None); downloads default to the platform's folder."""
    platform = platform if platform is not None else select_platform()
    output_dir = default_output_dir or platform.default_download_dir()
    return AppContext(
        service=service,
        credentials=credentials,
        default_output_dir=output_dir,
        media_paths=MediaPathPolicy(output_dir),
        runner=runner,
        remote_stt=remote_stt,
        chrome_watch=chrome_watch,
        platform=platform,
    )


def build_remote_stt(platform: Platform) -> RemoteSTTService:
    """The remote-STT service whose settings file lives where the platform says, written privately."""
    return RemoteSTTService(RemoteSTTSettingsStore(path_provider=platform.stt_config_path, writer=platform))


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
    remote_stt: RemoteSTTService | None = None
    # Built by the watch router (it needs the credentials/service helpers above) unless injected.
    chrome_watch: ChromeWatchManager | None = None
    # The OS adapter handed to core objects (AppPaths, ChromeLocator, ProcessTree, SttCapability, SecretFileWriter).
    platform: Platform = field(default_factory=select_platform)

    def __post_init__(self) -> None:
        if self.remote_stt is None:
            self.remote_stt = build_remote_stt(self.platform)

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
