from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from ssu_lecture_downloader.errors import LoginFlowError, classify_network_error
from ssu_lecture_downloader.http_client import HttpClient
from ssu_lecture_downloader.login_flow import run_http_login_discovery
from ssu_lecture_downloader.payloads import batch_payload
from ssu_lecture_downloader.redaction import redact_url
from ssu_lecture_downloader.service import SsuLectureService


@dataclass(frozen=True)
class CredentialStatus:
    username: str
    password: str

    @property
    def ok(self) -> bool:
        return self.username == "present" and self.password == "present"

    def as_dict(self) -> dict[str, str]:
        return {"SSU_USERNAME": self.username, "SSU_PASSWORD": self.password}


def credential_status() -> CredentialStatus:
    username = "present" if os.getenv("SSU_USERNAME") else "missing"
    password = "present" if os.getenv("SSU_PASSWORD") else "missing"
    return CredentialStatus(username=username, password=password)


def format_progress_event(event: dict[str, object]) -> str:
    stage = event.get("stage")
    index = event.get("index")
    total = event.get("total")
    title = event.get("title") or event.get("module_item_id") or event.get("output_path") or "download"
    prefix = f"[{index}/{total}] " if index is not None and total is not None else ""
    byte_suffix = ""
    bytes_written = event.get("bytes_written")
    total_bytes = event.get("total_bytes")
    if isinstance(bytes_written, int) and isinstance(total_bytes, int) and total_bytes > 0:
        percent = bytes_written / total_bytes * 100
        byte_suffix = f" {bytes_written}/{total_bytes} bytes ({percent:.1f}%)"
    elif isinstance(bytes_written, int):
        byte_suffix = f" {bytes_written} bytes"
    return f"{prefix}{stage}: {title}{byte_suffix}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ssu-lecture-downloader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Run pure HTTP login discovery.")
    login.add_argument("--trace", type=Path, default=None, help="Write redacted network trace JSON to this path.")
    login.add_argument("--json", action="store_true", help="Print redacted machine-readable JSON output.")

    discover_session = subparsers.add_parser("discover-session", help="Inspect authenticated session state.")
    discover_session.add_argument("--json", action="store_true", help="Print redacted machine-readable JSON output.")

    discover_lms = subparsers.add_parser("discover-lms", help="Login and list LearningX course/module summary.")
    discover_lms.add_argument("--json", action="store_true", help="Print redacted machine-readable JSON output.")
    discover_lms.add_argument("--trace", type=Path, default=None, help="Write redacted network trace JSON to this path.")
    discover_lms.add_argument("--term-id", dest="term_ids", type=int, action="append", help="LearningX term id. Can be repeated. Defaults to current courses where ended=False; use this to inspect older terms.")

    course_detail = subparsers.add_parser("course-detail", help="Login and list lecture rows for one course so UI/users can select lectures without raw ids.")
    course_detail.add_argument("--course-id", required=True, help="Canvas course id.")
    course_detail.add_argument("--course-name", default="", help="Optional display name for the course.")
    course_detail.add_argument("--json", action="store_true", help="Print redacted machine-readable JSON output.")
    course_detail.add_argument("--trace", type=Path, default=None, help="Write redacted network trace JSON to this path.")

    resolve_media = subparsers.add_parser("resolve-media", help="Resolve one Canvas module item to Commons download metadata without downloading bytes.")
    resolve_media.add_argument("--course-id", required=True, help="Canvas course id.")
    resolve_media.add_argument("--module-item-id", required=True, help="Canvas module item id.")
    resolve_media.add_argument("--json", action="store_true", help="Print redacted machine-readable JSON output.")
    resolve_media.add_argument("--trace", type=Path, default=None, help="Write redacted network trace JSON to this path.")

    download = subparsers.add_parser("download", help="Download one resolved lecture video to a selected output directory.")
    download.add_argument("--course-id", required=True, help="Canvas course id.")
    download.add_argument("--module-item-id", required=True, help="Canvas module item id.")
    download.add_argument("--course-name", default="course", help="Course folder name for the output path.")
    download.add_argument("--output-dir", required=True, type=Path, help="Directory where course folders and media files will be written.")
    download.add_argument("--dry-run", action="store_true", help="Resolve and plan the file path without downloading bytes.")
    download.add_argument("--json", action="store_true", help="Print redacted machine-readable JSON output.")
    download.add_argument("--trace", type=Path, default=None, help="Write redacted network trace JSON to this path.")

    batch_download = subparsers.add_parser("batch-download", help="Plan or download multiple lecture videos for selected module ids or all uncompleted lectures.")
    batch_download.add_argument("--course-id", required=True, help="Canvas course id.")
    batch_download.add_argument("--course-name", default="course", help="Course folder name for the output path.")
    batch_download.add_argument("--module-item-id", dest="module_item_ids", action="append", help="Canvas module item id to include. Can be repeated.")
    batch_download.add_argument("--uncompleted", action="store_true", help="Download all uncompleted lectures in the course instead of selected module item ids.")
    batch_download.add_argument("--include-completed", action="store_true", help="With --uncompleted absent and no selected ids, include completed lectures too.")
    batch_download.add_argument("--output-dir", required=True, type=Path, help="Directory where course folders and media files will be written.")
    batch_download.add_argument("--dry-run", action="store_true", help="Resolve and plan file paths without downloading bytes.")
    batch_download.add_argument("--json", action="store_true", help="Print redacted machine-readable JSON output.")
    batch_download.add_argument("--trace", type=Path, default=None, help="Write redacted network trace JSON to this path.")

    return parser


def _print_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _print_missing_credentials(status: CredentialStatus, as_json: bool) -> None:
    if as_json:
        _print_json({"ok": False, "error_code": "missing_credentials", "credentials": status.as_dict()})
        return

    print("Login discovery cannot start: missing credentials")
    print("Credentials:")
    for name, state in status.as_dict().items():
        print(f"  {name}: {state}")


def _credentials_from_env(args: argparse.Namespace) -> tuple[str, str] | None:
    """(username, password) from `.env`/environment, or None after reporting them missing."""
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    status = credential_status()
    if not status.ok:
        _print_missing_credentials(status, args.json)
        return None
    return os.environ["SSU_USERNAME"], os.environ["SSU_PASSWORD"]


def _write_trace(args: argparse.Namespace, http: HttpClient) -> None:
    if args.trace:
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        args.trace.write_text(http.trace.to_json(), encoding="utf-8")


def _trace_field(args: argparse.Namespace) -> str | None:
    return str(args.trace) if args.trace else None


def _progress_printer(args: argparse.Namespace) -> tuple[list[dict[str, object]], Callable[[dict[str, object]], None]]:
    progress: list[dict[str, object]] = []

    def on_progress(event: dict[str, object]) -> None:
        progress.append(event)
        if not args.json:
            print(format_progress_event(event))

    return progress, on_progress


def _print_trace_path(args: argparse.Namespace) -> None:
    if args.trace:
        print(f"Trace: {args.trace}")


def run_login(args: argparse.Namespace) -> int:
    credentials = _credentials_from_env(args)
    if credentials is None:
        return 2

    http = HttpClient()
    result = run_http_login_discovery(*credentials, http=http)
    _write_trace(args, http)

    payload = result.to_dict() | {"trace": _trace_field(args)}
    if args.json:
        _print_json(payload)
    else:
        print("Login discovery complete" if result.ok else "Login discovery failed")
        print(f"Status: {'ok' if result.ok else result.error_code}")
        if result.session:
            print(f"xn_api_token: {'present' if result.session.xn_api_token_present else 'missing'}")
            print("Cookies: " + ", ".join(result.session.cookie_names))
        _print_trace_path(args)
    return 0 if result.ok else 1


def run_discover_session(args: argparse.Namespace) -> int:
    payload = {"ok": False, "error_code": "not_implemented_yet"}
    if args.json:
        _print_json(payload)
    else:
        print("Session discovery scaffold ready")
        print("Status: not_implemented_yet")
    return 1


def run_discover_lms(args: argparse.Namespace) -> int:
    credentials = _credentials_from_env(args)
    if credentials is None:
        return 2

    http = HttpClient()
    result = SsuLectureService(http=http).discover_courses(*credentials, term_ids=args.term_ids)
    _write_trace(args, http)
    if not result.ok:
        payload = result.login.to_dict() | {"courses": [], "course_count": 0}
        if args.trace:
            payload["trace"] = str(args.trace)
        if args.json:
            _print_json(payload)
        else:
            print("LMS discovery failed")
            print(f"Status: {result.error_code}")
        return 1

    payload = {
        "ok": True,
        "error_code": None,
        "course_count": result.course_count,
        "courses": [course.model_dump() for course in result.courses],
        "trace": _trace_field(args),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Courses: {result.course_count}")
        for course in result.courses:
            print(
                f"- {course.id} {course.name} | modules={course.module_count} "
                f"lectures={course.lecture_count} uncompleted={course.uncompleted_lecture_count}"
            )
        _print_trace_path(args)
    return 0


def run_course_detail(args: argparse.Namespace) -> int:
    credentials = _credentials_from_env(args)
    if credentials is None:
        return 2

    http = HttpClient()
    result = SsuLectureService(http=http).get_course_detail(*credentials, course_id=args.course_id, course_name=args.course_name)
    _write_trace(args, http)
    if not result.ok or result.course is None:
        payload = {"ok": False, "error_code": result.error_code, "course": None, "lectures": []}
        if args.json:
            _print_json(payload)
        else:
            print(f"Course detail failed: {result.error_code}")
        return 1

    payload = {
        "ok": True,
        "error_code": None,
        "course": result.course.model_dump(),
        "lecture_count": len(result.lectures),
        "lectures": [lecture.model_dump() for lecture in result.lectures],
        "trace": _trace_field(args),
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"Course: {result.course.id} {result.course.name}")
        print(f"Lectures: {len(result.lectures)}")
        for lecture in result.lectures:
            print(
                f"- module={lecture.module_name} module_item_id={lecture.module_item_id} "
                f"completed={lecture.completed} title={lecture.title}"
            )
        _print_trace_path(args)
    return 0


def _print_login_failure(args: argparse.Namespace, error_code: str | None) -> int:
    if args.json:
        _print_json({"ok": False, "error_code": error_code})
    else:
        print(f"Login failed: {error_code}")
    return 1


def run_resolve_media(args: argparse.Namespace) -> int:
    credentials = _credentials_from_env(args)
    if credentials is None:
        return 2

    http = HttpClient()
    result = SsuLectureService(http=http).resolve_media(*credentials, course_id=args.course_id, module_item_id=args.module_item_id)
    if not result.ok:
        return _print_login_failure(args, result.error_code)
    payload = {
        "ok": True,
        "error_code": None,
        "course_id": result.course_id,
        "module_item_id": result.module_item_id,
        "attendance_item_id": result.attendance_item_id,
        "title": result.title,
        "content_id": result.content_id,
        "content_type": result.content_type,
        "duration": result.duration,
        "download_url": result.download_url,
        "download_size": result.download_size,
        "head_status": result.head_status,
        "head_location": result.head_location,
        "head_content_type": result.head_content_type,
        "head_content_length": result.head_content_length,
        "trace": _trace_field(args),
    }
    _write_trace(args, http)
    if args.json:
        _print_json(payload)
    else:
        print(f"Title: {payload['title']}")
        print(f"Content: {payload['content_id']} ({payload['content_type']}, {payload['duration']}s)")
        print(f"Download size: {payload['download_size']}")
        print(f"HEAD: {payload['head_status']} {payload['head_content_type']} {payload['head_content_length']}")
        _print_trace_path(args)
    return 0


def run_download(args: argparse.Namespace) -> int:
    credentials = _credentials_from_env(args)
    if credentials is None:
        return 2

    http = HttpClient()
    progress, on_progress = _progress_printer(args)
    result = SsuLectureService(http=http).download_lecture(
        *credentials,
        course_id=args.course_id,
        module_item_id=args.module_item_id,
        course_name=args.course_name,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        on_progress=on_progress,
    )
    if not result.ok:
        return _print_login_failure(args, result.error_code)

    _write_trace(args, http)
    payload = {
        "ok": True,
        "error_code": None,
        "dry_run": result.dry_run,
        "written": result.written,
        "course_id": result.course_id,
        "module_item_id": result.module_item_id,
        "attendance_item_id": result.attendance_item_id,
        "title": result.title,
        "content_id": result.content_id,
        "download_url": result.download_url,
        "download_size": result.download_size,
        "output_path": result.output_path,
        "file_size": result.file_size,
        "progress": progress,
        "trace": _trace_field(args),
    }
    if args.json:
        _print_json(payload)
    else:
        print("Download planned" if args.dry_run else "Download complete")
        print(f"Title: {payload['title']}")
        print(f"Output: {result.output_path}")
        if result.file_size is not None:
            print(f"File size: {result.file_size}")
        _print_trace_path(args)
    return 0


def run_batch_download(args: argparse.Namespace) -> int:
    credentials = _credentials_from_env(args)
    if credentials is None:
        return 2
    if not args.uncompleted and not args.include_completed and not args.module_item_ids:
        message = "batch-download needs --module-item-id, --uncompleted, or --include-completed"
        if args.json:
            _print_json({"ok": False, "error_code": "missing_batch_selection", "message": message})
        else:
            print(message)
        return 2

    http = HttpClient()
    progress, on_progress = _progress_printer(args)
    result = SsuLectureService(http=http).batch_download_lectures(
        *credentials,
        course_id=args.course_id,
        course_name=args.course_name,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        module_item_ids=args.module_item_ids if args.module_item_ids else None,
        include_completed=args.include_completed,
        on_progress=on_progress,
    )
    _write_trace(args, http)

    payload = batch_payload(result, progress) | {"trace": _trace_field(args)}
    if args.json:
        _print_json(payload)
    else:
        print("Batch download planned" if args.dry_run else "Batch download complete")
        print(f"Total: {result.total_count} success={result.success_count} failed={result.failure_count}")
        for item in result.items:
            print(f"- {'ok' if item.ok else 'failed'} module_item_id={item.module_item_id} title={item.title} output={item.output_path}")
        _print_trace_path(args)
    return 0 if result.ok else 1


_URL_PATTERN = re.compile(r"https?://\S+")
MAX_ERROR_MESSAGE_CHARS = 300


def describe_failure(exc: BaseException) -> tuple[str, str]:
    """(error_code, one-line message) for an unexpected exception, URLs redacted."""
    if isinstance(exc, LoginFlowError):
        return exc.code, exc.message
    network_code = classify_network_error(exc)
    if network_code:
        return network_code, "LMS/Commons 서버에 연결하지 못했거나 응답이 지연되었습니다."
    text = " ".join(str(exc).split())
    text = _URL_PATTERN.sub(lambda match: redact_url(match.group(0)), text)
    return type(exc).__name__, text[:MAX_ERROR_MESSAGE_CHARS]


def report_failure(exc: BaseException, *, as_json: bool) -> int:
    error_code, message = describe_failure(exc)
    if as_json:
        _print_json({"ok": False, "error_code": error_code, "message": message})
    else:
        print(f"Error: {error_code}: {message}" if message else f"Error: {error_code}", file=sys.stderr)
    return 1


COMMANDS = {
    "login": run_login,
    "discover-session": run_discover_session,
    "discover-lms": run_discover_lms,
    "course-detail": run_course_detail,
    "resolve-media": run_resolve_media,
    "download": run_download,
    "batch-download": run_batch_download,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.error(f"unsupported command: {args.command}")
        return 2
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - last line of defence for the CLI
        return report_failure(exc, as_json=bool(getattr(args, "json", False)))


if __name__ == "__main__":
    sys.exit(main())
