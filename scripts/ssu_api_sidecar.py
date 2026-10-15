"""PyInstaller entrypoint for the packaged SSU Lecture Downloader API sidecar."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

# Import the app directly so PyInstaller sees the project package instead of only
# a dynamic uvicorn import string.
from ssu_lecture_downloader.api import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SSU Lecture Downloader local API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    # The desktop shell redirects stdout/stderr to a log file; without line
    # buffering a crash can leave that file empty.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
        # The API has no WebSocket routes; skipping the protocol avoids importing
        # `websockets` (~40 ms) at start-up.
        ws="none",
    )


if __name__ == "__main__":
    main()
