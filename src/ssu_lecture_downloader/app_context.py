"""State shared by the API routers (one instance per `create_app`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ssu_lecture_downloader.api_errors import background_job_error_code, missing_credentials_error
from ssu_lecture_downloader.credentials import CredentialStore
from ssu_lecture_downloader.jobs import JobRegistry, JobRunner
from ssu_lecture_downloader.security import MediaPathPolicy

APP_DOWNLOAD_FOLDER_NAME = "SSU Lecture Downloader"


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


def job_or_404(registry: JobRegistry, job_id: str, error_code: str):
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error_code": error_code})
    return job
