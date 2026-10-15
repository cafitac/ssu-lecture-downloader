from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlparse

from ssu_lecture_downloader.errors import LoginFlowError

LMS_SSO_CALLBACK_URL = "https://lms.ssu.ac.kr/xn-sso/gw-cb.php"
SMARTID_LOGIN_URL = "https://smartid.ssu.ac.kr/Symtra_sso/smln_pcs.asp"
SMARTID_LOGIN_PAGE_URL = "https://smartid.ssu.ac.kr/Symtra_sso/smln.asp"


class HttpClientProtocol(Protocol):
    cookies: dict[str, str]

    def get(self, step: str, url: str, **kwargs): ...

    def post(self, step: str, url: str, **kwargs): ...


@dataclass(frozen=True)
class SessionState:
    xn_api_token_present: bool
    cookie_names: list[str]


@dataclass(frozen=True)
class CanvasLoginForm:
    action: str
    fields: dict[str, str]


@dataclass(frozen=True)
class LoginCryption:
    encrypted_data_b64: str
    private_key_pem: str


def extract_sso_redirect(html: str) -> str:
    match = re.search(r"parent\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", html)
    if not match:
        raise LoginFlowError("sso_redirect_missing", "SmartID response did not contain parent.location.href redirect")

    redirect_url = match.group(1)
    parsed = urlparse(redirect_url)
    if not parsed.query:
        raise LoginFlowError("sso_redirect_missing", "SmartID redirect did not contain query parameters")
    if parsed.netloc == "lms.ssu.ac.kr" and parsed.path == "/xn-sso/gw-cb.php":
        return redirect_url
    return f"{LMS_SSO_CALLBACK_URL}?{parsed.query}"


def extract_iframe_src(html: str, base_url: str | None = None) -> str:
    script_match = re.search(r"iframe\.src\s*=\s*['\"]([^'\"]+)['\"]", html)
    if script_match:
        return urljoin(base_url or "", script_match.group(1))

    from bs4 import BeautifulSoup  # imported lazily: only the login flow needs HTML parsing

    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe")
    if iframe and iframe.get("src"):
        return urljoin(base_url or "", iframe["src"])

    raise LoginFlowError("iframe_src_missing", "LMS callback response did not contain iframe src")


def extract_canvas_login_form(html: str) -> CanvasLoginForm:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", {"id": "login_form"})
    if not form:
        raise LoginFlowError("canvas_form_missing", "Canvas iframe response did not contain login_form")
    action = form.get("action")
    if not action:
        raise LoginFlowError("canvas_form_missing", "Canvas login_form did not contain action")

    fields = {
        input_tag["name"]: input_tag.get("value", "")
        for input_tag in form.find_all("input")
        if input_tag.get("name")
    }
    return CanvasLoginForm(action=action, fields=fields)


def extract_login_cryption(html: str) -> LoginCryption:
    match = re.search(r"window\.loginCryption\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"](.+?)['\"]\s*\)", html, re.DOTALL)
    if not match:
        raise LoginFlowError("login_cryption_missing", "Canvas iframe response did not contain loginCryption script")
    return LoginCryption(
        encrypted_data_b64=match.group(1),
        private_key_pem=match.group(2).replace("\\n", "\n"),
    )


def decrypt_canvas_token(encrypted_data_b64: str, private_key_pem: str) -> str:
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    try:
        encrypted_bytes = base64.b64decode(encrypted_data_b64)
        private_key = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        decrypted = private_key.decrypt(encrypted_bytes, padding.PKCS1v15())
        return decrypted.decode("utf-8")
    except Exception as exc:
        raise LoginFlowError("token_decryption_failed", "Canvas token decryption failed") from exc


class SsuHttpLoginClient:
    def __init__(self, http: HttpClientProtocol) -> None:
        self.http = http

    def fetch_lms_entry(self):
        response = self.http.get("fetch_lms_entry", "https://lms.ssu.ac.kr/")
        response.raise_for_status()
        return response

    def submit_smartid_login(self, username: str, password: str):
        response = self.http.post(
            "submit_smartid_login",
            SMARTID_LOGIN_URL,
            data={
                "in_tp_bit": "0",
                "rqst_caus_cd": "03",
                "userid": username,
                "pwd": password,
            },
            headers={"Referer": SMARTID_LOGIN_PAGE_URL},
        )
        response.raise_for_status()
        if "다시 확인하시기 바랍니다" in response.text:
            raise LoginFlowError("smartid_login_rejected", "SmartID rejected the provided credentials")
        return response

    def follow_lms_callback(self, url: str):
        response = self.http.get("follow_lms_callback", url)
        response.raise_for_status()
        return response

    def fetch_iframe_login(self, url: str):
        response = self.http.get("fetch_iframe_login", url)
        response.raise_for_status()
        return response

    def submit_canvas_login(self, form: CanvasLoginForm, decrypted_token: str, referer: str):
        payload = dict(form.fields)
        payload["pseudonym_session[password]"] = decrypted_token
        response = self.http.post(
            "submit_canvas_login",
            form.action,
            data=payload,
            headers={"Referer": referer},
        )
        response.raise_for_status()
        return response

    def verify_canvas_session(self) -> SessionState:
        cookie_names = sorted(self.http.cookies.keys())
        if "xn_api_token" not in self.http.cookies:
            raise LoginFlowError("xn_api_token_missing", "Canvas session does not contain xn_api_token cookie")
        return SessionState(xn_api_token_present=True, cookie_names=cookie_names)
