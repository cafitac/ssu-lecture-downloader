"""Compatibility entrypoint: ``uvicorn ssu_lecture_downloader.api:app`` stays valid.

The FastAPI app lives in ``ssu_lecture_downloader.service.api``. The desktop shell (debug launch
and stale-backend process detection) pins the ``ssu_lecture_downloader.api:app`` import string.
"""

from ssu_lecture_downloader.service.api import app

__all__ = ["app"]
