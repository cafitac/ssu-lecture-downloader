import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ssu_lecture_downloader.core.login_flow import run_http_login_discovery


class FakeResponse:
    def __init__(self, text="", url="https://example.test/", status_code=200):
        self.text = text
        self.url = url
        self.status_code = status_code
        self.content = text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttpClient:
    def __init__(self, iframe_html):
        self.calls = []
        self.cookies = {}
        self.trace = None
        self.iframe_html = iframe_html

    def get(self, step, url, **kwargs):
        self.calls.append(("GET", step, url, kwargs))
        if step == "fetch_lms_entry":
            return FakeResponse("entry", url)
        if step == "follow_lms_callback":
            return FakeResponse('iframe.src="/canvas-login"', "https://lms.ssu.ac.kr/xn-sso/gw-cb.php?sso=abc")
        if step == "fetch_iframe_login":
            return FakeResponse(self.iframe_html, "https://lms.ssu.ac.kr/canvas-login")
        raise AssertionError(step)

    def post(self, step, url, **kwargs):
        self.calls.append(("POST", step, url, kwargs))
        if step == "submit_smartid_login":
            return FakeResponse("parent.location.href = 'https://smartid.ssu.ac.kr/redirect?sso=abc&ticket=secret-ticket';", url)
        if step == "submit_canvas_login":
            self.cookies["xn_api_token"] = "raw-token"
            return FakeResponse("Dashboard", "https://canvas.ssu.ac.kr/courses")
        raise AssertionError(step)


def encrypted_login_fixture():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    encrypted = public_key.encrypt(b"decrypted-token", padding.PKCS1v15())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode().replace("\n", "\\n")
    return base64.b64encode(encrypted).decode(), private_pem


def test_run_http_login_discovery_executes_explicit_steps_without_returning_secrets():
    encrypted, private_pem = encrypted_login_fixture()
    iframe_html = f'''
    <form id="login_form" action="https://canvas.ssu.ac.kr/login/canvas">
      <input name="authenticity_token" value="csrf-token" />
    </form>
    <script>window.loginCryption("{encrypted}", "{private_pem}");</script>
    '''
    http = FakeHttpClient(iframe_html)

    result = run_http_login_discovery("student-id", "student-password", http=http)

    assert result.ok is True
    assert result.error_code is None
    assert result.session.xn_api_token_present is True
    assert [call[1] for call in http.calls] == [
        "fetch_lms_entry",
        "submit_smartid_login",
        "follow_lms_callback",
        "fetch_iframe_login",
        "submit_canvas_login",
    ]
    assert "student-password" not in str(result.to_dict())
    assert "raw-token" not in str(result.to_dict())
    canvas_post = http.calls[-1]
    assert canvas_post[3]["data"]["pseudonym_session[password]"] == "decrypted-token"


def test_run_http_login_discovery_returns_failure_code_for_missing_canvas_form():
    encrypted, private_pem = encrypted_login_fixture()
    http = FakeHttpClient(f'<script>window.loginCryption("{encrypted}", "{private_pem}");</script>')

    result = run_http_login_discovery("student-id", "student-password", http=http)

    assert result.ok is False
    assert result.error_code == "canvas_form_missing"
