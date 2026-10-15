"""PyInstaller entrypoint for the packaged SSU Lecture Downloader API sidecar."""

from __future__ import annotations

import argparse

import uvicorn

# Import the app directly so PyInstaller sees the project package instead of only
# a dynamic uvicorn import string.
from ssu_lecture_downloader.api import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SSU Lecture Downloader local API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()
