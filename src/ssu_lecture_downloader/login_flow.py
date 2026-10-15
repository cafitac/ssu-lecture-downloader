from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ssu_lecture_downloader.auth import (
    SessionState,
    SsuHttpLoginClient,
    decrypt_canvas_token,
    extract_canvas_login_form,
    extract_iframe_src,
    extract_login_cryption,
    extract_sso_redirect,
)
from ssu_lecture_downloader.errors import LoginFlowError, classify_network_error
from ssu_lecture_downloader.http_client import HttpClient


@dataclass(frozen=True)
class LoginDiscoveryResult:
    ok: bool
    error_code: str | None
    session: SessionState | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok, "error_code": self.error_code}
        if self.session:
            payload["session"] = {
                "xn_api_token_present": self.session.xn_api_token_present,
                "cookie_names": self.session.cookie_names,
            }
        return payload


def run_http_login_discovery(username: str, password: str, http=None) -> LoginDiscoveryResult:
    http_client = http or HttpClient()
    login = SsuHttpLoginClient(http_client)

    try:
        login.fetch_lms_entry()
        smartid_response = login.submit_smartid_login(username, password)
        lms_callback_url = extract_sso_redirect(smartid_response.text)
        callback_response = login.follow_lms_callback(lms_callback_url)
        iframe_src = extract_iframe_src(callback_response.text, base_url=callback_response.url)
        iframe_response = login.fetch_iframe_login(iframe_src)
        form = extract_canvas_login_form(iframe_response.text)
        cryption = extract_login_cryption(iframe_response.text)
        decrypted_token = decrypt_canvas_token(cryption.encrypted_data_b64, cryption.private_key_pem)
        login.submit_canvas_login(form, decrypted_token, referer=iframe_src)
        session = login.verify_canvas_session()
    except LoginFlowError as exc:
        return LoginDiscoveryResult(ok=False, error_code=exc.code)
    except Exception as exc:
        # Transport failures (timeouts, DNS, 5xx) must not look like rejected
        # credentials; only unrecognised page shapes stay in the auth-error class.
        network_code = classify_network_error(exc)
        return LoginDiscoveryResult(ok=False, error_code=network_code or "unknown_response_shape")

    return LoginDiscoveryResult(ok=True, error_code=None, session=session)
