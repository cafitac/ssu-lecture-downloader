from dataclasses import dataclass

import pytest

from ssu_lecture_downloader.auth import SsuHttpLoginClient
from ssu_lecture_downloader.errors import LoginFlowError


@dataclass
class FakeResponse:
    text: str = ""
    url: str = "https://example.test/final"
    status_code: int = 200

    @property
    def content(self):
        return self.text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpClient:
    def __init__(self):
        self.calls = []
        self.cookies = {}

    def get(self, step, url, **kwargs):
        self.calls.append(("GET", step, url, kwargs))
        return FakeResponse(text=f"response for {step}", url=url)

    def post(self, step, url, **kwargs):
        self.calls.append(("POST", step, url, kwargs))
        if step == "submit_smartid_login":
            return FakeResponse(text="parent.location.href = 'https://smartid.ssu.ac.kr/redirect?sso=abc';")
        return FakeResponse(text="ok", url=url)


def test_submit_smartid_login_posts_expected_form_fields():
    fake = FakeHttpClient()
    client = SsuHttpLoginClient(fake)

    response = client.submit_smartid_login("student-id", "student-password")

    assert "parent.location.href" in response.text
    method, step, url, kwargs = fake.calls[0]
    assert method == "POST"
    assert step == "submit_smartid_login"
    assert url == "https://smartid.ssu.ac.kr/Symtra_sso/smln_pcs.asp"
    assert kwargs["data"] == {
        "in_tp_bit": "0",
        "rqst_caus_cd": "03",
        "userid": "student-id",
        "pwd": "student-password",
    }
    assert kwargs["headers"]["Referer"] == "https://smartid.ssu.ac.kr/Symtra_sso/smln.asp"


def test_submit_smartid_login_classifies_rejected_credentials():
    class RejectingFake(FakeHttpClient):
        def post(self, step, url, **kwargs):
            return FakeResponse(text="아이디 또는 비밀번호를 다시 확인하시기 바랍니다.")

    client = SsuHttpLoginClient(RejectingFake())

    with pytest.raises(LoginFlowError) as exc:
        client.submit_smartid_login("bad", "bad")

    assert exc.value.code == "smartid_login_rejected"


def test_verify_canvas_session_reports_xn_api_token_presence():
    fake = FakeHttpClient()
    fake.cookies = {"xn_api_token": "raw-token"}
    client = SsuHttpLoginClient(fake)

    state = client.verify_canvas_session()

    assert state.xn_api_token_present is True
    assert state.cookie_names == ["xn_api_token"]


def test_verify_canvas_session_classifies_missing_xn_api_token():
    client = SsuHttpLoginClient(FakeHttpClient())

    with pytest.raises(LoginFlowError) as exc:
        client.verify_canvas_session()

    assert exc.value.code == "xn_api_token_missing"
