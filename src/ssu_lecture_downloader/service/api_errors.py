"""Translate service/transport exceptions into the `{ok: false, error_code, ...}` API shape."""

from __future__ import annotations

import logging
import traceback
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

from curl_cffi.requests import exceptions as curl_exceptions
from fastapi import HTTPException

from ssu_lecture_downloader.core.grades.models import (
    GRADES_CONSENT_REQUIRED,
    GRADES_LOGIN_REJECTED,
    MISSING_CREDENTIALS,
    GradesError,
)
from ssu_lecture_downloader.core.errors import (
    NETWORK_ERROR,
    NETWORK_TIMEOUT,
    LibraryConflict,
    LoginFlowError,
    PathTooLong,
    TidyRunNotFound,
    classify_network_error,
    is_session_lost,
)

logger = logging.getLogger("ssu_lecture_downloader.api")

# Codes meaning "these credentials / this LMS session do not work" (HTTP 401).
# Transport failures (network_error, network_timeout) are deliberately NOT in here:
# a flaky network must never look like rejected credentials.
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

# u-SAINT grades codes. They are deliberately NOT in AUTHENTICATION_ERROR_CODES: the frontend signs the
# user out of the LMS on those, and a grades failure must only degrade the grades tab.
GRADES_ERROR_CODES = frozenset(
    {
        "grades_consent_required",  # 409
        "grades_login_rejected",  # 403
        "grades_unavailable",  # 503
        "invalid_consent_version",  # 400
        "invalid_semester",  # 400
        "invalid_year",  # 400
    }
)

INTERNAL_ERROR_CODE = "internal_error"
LIBRARY_CONFLICT_CODE = LibraryConflict.error_code
PATH_TOO_LONG_CODE = PathTooLong.error_code
LIBRARY_MESSAGES = {
    LIBRARY_CONFLICT_CODE: "저장할 자리에 같은 이름의 다른 파일이 생겼어요. 잠시 후 다시 받아 주세요.",
    PATH_TOO_LONG_CODE: "저장 경로가 너무 길어요. 더 짧은 폴더로 바꿔서 다시 받아 주세요.",
}
# Tidy-up of the download folder (LAYOUT-PY-MIGRATION): all three are 409 and raised at the start request.
LIBRARY_BUSY_CODE = "library_busy"
LIBRARY_TIDY_RUNNING_CODE = "library_tidy_running"
TIDY_PLAN_EXPIRED_CODE = "tidy_plan_expired"
TIDY_RUN_NOT_FOUND_CODE = "tidy_run_not_found"
INVALID_RUN_ID_CODE = "invalid_run_id"
TIDY_MESSAGES = {
    LIBRARY_BUSY_CODE: "다운로드가 끝난 뒤에 정리할 수 있어요.",
    LIBRARY_TIDY_RUNNING_CODE: "폴더를 정리하는 중이에요. 끝나면 받을 수 있어요.",
    TIDY_PLAN_EXPIRED_CODE: "미리보기가 오래됐어요. 다시 확인해 주세요.",
    TIDY_RUN_NOT_FOUND_CODE: "되돌릴 정리 기록을 찾지 못했어요.",
}
MEDIA_CONTENT_ID_MISSING_MESSAGE = "attendance item does not expose Commons content_id"


def missing_credentials_error() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "ok": False,
            "error_code": "missing_credentials",
            "credentials": {"SSU_USERNAME": "missing", "SSU_PASSWORD": "missing"},
        },
    )


def grades_error(error: GradesError) -> HTTPException:
    """The API error for a GradesError: code only, never a message or the cause."""
    if error.code == MISSING_CREDENTIALS:
        return missing_credentials_error()  # the existing contract: 400 missing_credentials
    status = {
        GRADES_CONSENT_REQUIRED: HTTPStatus.CONFLICT,
        GRADES_LOGIN_REJECTED: HTTPStatus.FORBIDDEN,
    }.get(error.code, HTTPStatus.SERVICE_UNAVAILABLE)
    code = error.code if error.code in GRADES_ERROR_CODES else "grades_unavailable"
    return HTTPException(status_code=status, detail={"ok": False, "error_code": code})


def status_for_error_code(error_code: str | None) -> int:
    code = error_code or "authentication_failed"
    if code in AUTHENTICATION_ERROR_CODES:
        return HTTPStatus.UNAUTHORIZED
    if code == NETWORK_TIMEOUT:
        return HTTPStatus.GATEWAY_TIMEOUT
    if code == LIBRARY_CONFLICT_CODE:
        return HTTPStatus.CONFLICT
    if code in (PATH_TOO_LONG_CODE, INVALID_RUN_ID_CODE):
        return HTTPStatus.BAD_REQUEST
    if code in (LIBRARY_BUSY_CODE, LIBRARY_TIDY_RUNNING_CODE, TIDY_PLAN_EXPIRED_CODE):
        return HTTPStatus.CONFLICT
    if code == TIDY_RUN_NOT_FOUND_CODE:
        return HTTPStatus.NOT_FOUND
    return HTTPStatus.BAD_GATEWAY


def service_error(error_code: str | None, *, message: str | None = None, detail: dict[str, Any] | None = None) -> HTTPException:
    code = error_code or "service_error"
    payload: dict[str, Any] = {"ok": False, "error_code": code}
    if message:
        payload["message"] = message
    if detail:
        payload["detail"] = detail
    return HTTPException(status_code=status_for_error_code(code), detail=payload)


def tidy_error(error_code: str) -> HTTPException:
    """The 409/404/400 of the tidy-up endpoints, with the plain Korean message the UI may show."""
    return service_error(error_code, message=TIDY_MESSAGES.get(error_code))


def authentication_failed_error(error_code: str | None) -> HTTPException:
    """Error for a failed login result; auth codes -> 401, network codes -> 502/504."""
    return service_error(error_code or "authentication_failed")


def raise_for_result_error(error_code: str | None) -> None:
    raise service_error(error_code)


def clear_service_cache(service: Any) -> None:
    clear_cache = getattr(service, "clear_cache", None)
    if clear_cache:
        clear_cache()


def last_trace_entry(service: Any) -> dict[str, Any] | None:
    trace = getattr(getattr(service, "http", None), "trace", None)
    last_entry = getattr(trace, "last_entry", None)
    if not last_entry:
        return None
    try:
        return last_entry()
    except Exception:  # noqa: BLE001 - diagnostics must never mask the original error
        return None


def lms_http_error_detail(exc: Exception, service: Any) -> tuple[str, str, dict[str, Any]]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    trace_entry = last_trace_entry(service) or {}
    step = str(trace_entry.get("step") or "unknown_step")
    status = status_code or trace_entry.get("status_code") or "unknown_status"
    host = None
    final_url = trace_entry.get("final_url") or trace_entry.get("url")
    if isinstance(final_url, str):
        host = urlsplit(final_url).netloc or None
    logger.warning("lms_request_failed step=%s status=%s", step, status)
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


def _network_message(code: str) -> str:
    if code == NETWORK_TIMEOUT:
        return "LMS/Commons 서버 응답이 지연되고 있습니다. 잠시 후 다시 시도해 주세요."
    return "LMS/Commons 서버에 연결하지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도해 주세요."


def internal_error(exc: BaseException) -> HTTPException:
    """500 with a stable JSON shape. The exception text is logged, never returned."""
    _log_unexpected(exc)
    return HTTPException(
        status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        detail={
            "ok": False,
            "error_code": INTERNAL_ERROR_CODE,
            "message": "예상하지 못한 오류가 발생했습니다.",
            "detail": {"exception": type(exc).__name__},
        },
    )


def _log_unexpected(exc: BaseException) -> None:
    # Only the type and the innermost frame: exception messages from HTTP clients can
    # embed signed media URLs.
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "unknown"
    logger.error("unhandled %s at %s", type(exc).__name__, where)


def service_exception_error(exc: Exception, service: Any) -> HTTPException:
    if is_session_lost(exc):
        clear_service_cache(service)
        return service_error("session_expired")
    if isinstance(exc, LoginFlowError):
        return service_error(exc.code, message=exc.message)
    if isinstance(exc, (LibraryConflict, PathTooLong)):
        return service_error(exc.error_code, message=LIBRARY_MESSAGES[exc.error_code])
    if isinstance(exc, curl_exceptions.HTTPError):
        code, message, detail = lms_http_error_detail(exc, service)
        return service_error(code, message=message, detail=detail)
    network_code = classify_network_error(exc)
    if network_code:
        return service_error(network_code, message=_network_message(network_code))
    if isinstance(exc, RuntimeError) and str(exc) == MEDIA_CONTENT_ID_MISSING_MESSAGE:
        return service_error(
            "media_content_id_missing",
            message="선택한 항목은 영상/자료 다운로드 주소가 없습니다. 강의 목록을 새로고침한 뒤 MP4/MP3/TXT는 영상 항목만 선택해 주세요.",
            detail={"step": "parse_attendance_item_media", "reason": "content_id_missing"},
        )
    return internal_error(exc)


def background_job_error_code(exc: Exception, service: Any) -> str:
    """error_code stored on a failed background job."""
    if is_session_lost(exc):
        clear_service_cache(service)
        return "session_expired"
    if isinstance(exc, LoginFlowError):
        return exc.code
    if isinstance(exc, (LibraryConflict, PathTooLong, TidyRunNotFound)):
        return exc.error_code
    if isinstance(exc, curl_exceptions.HTTPError):
        return lms_http_error_detail(exc, service)[0]
    network_code = classify_network_error(exc)
    if network_code:
        return network_code
    return type(exc).__name__


__all__ = [
    "AUTHENTICATION_ERROR_CODES",
    "GRADES_ERROR_CODES",
    "INTERNAL_ERROR_CODE",
    "NETWORK_ERROR",
    "NETWORK_TIMEOUT",
]


class UnhandledErrorMiddleware:
    """Innermost user middleware: turns any escaped exception into the JSON error shape.

    Sitting inside the CORS middleware means even these 500s carry CORS headers, so the
    frontend can read `error_code` instead of seeing an opaque network failure.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if response_started:
                raise
            from starlette.responses import JSONResponse

            error = internal_error(exc)
            await JSONResponse(status_code=error.status_code, content=error.detail)(scope, receive, send)
