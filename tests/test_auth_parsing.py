import pytest

from ssu_lecture_downloader.core.auth import (
    CanvasLoginForm,
    LoginCryption,
    extract_canvas_login_form,
    extract_iframe_src,
    extract_login_cryption,
    extract_sso_redirect,
)
from ssu_lecture_downloader.core.errors import LoginFlowError


def test_extract_sso_redirect_builds_lms_callback_url_from_smartid_script():
    html = """
    <script>
      parent.location.href = 'https://lms.ssu.ac.kr/xn-sso/gw-cb.php?sso=abc&ticket=secret-ticket';
    </script>
    """

    result = extract_sso_redirect(html)

    assert result == "https://lms.ssu.ac.kr/xn-sso/gw-cb.php?sso=abc&ticket=secret-ticket"


def test_extract_sso_redirect_converts_non_lms_redirect_to_lms_callback():
    html = "<script>parent.location.href = 'https://smartid.ssu.ac.kr/redirect?sso=abc&ticket=secret-ticket';</script>"

    result = extract_sso_redirect(html)

    assert result == "https://lms.ssu.ac.kr/xn-sso/gw-cb.php?sso=abc&ticket=secret-ticket"


def test_extract_sso_redirect_classifies_missing_script():
    with pytest.raises(LoginFlowError) as exc:
        extract_sso_redirect("<html>No redirect</html>")

    assert exc.value.code == "sso_redirect_missing"


def test_extract_iframe_src_supports_script_assignment_and_relative_url():
    html = 'var iframe = document.createElement("iframe"); iframe.src="/canvas/login?ticket=abc";'

    result = extract_iframe_src(html, base_url="https://lms.ssu.ac.kr/xn-sso/gw-cb.php")

    assert result == "https://lms.ssu.ac.kr/canvas/login?ticket=abc"


def test_extract_iframe_src_supports_html_iframe_tag():
    html = '<iframe id="launch" src="https://canvas.ssu.ac.kr/login/canvas"></iframe>'

    result = extract_iframe_src(html)

    assert result == "https://canvas.ssu.ac.kr/login/canvas"


def test_extract_iframe_src_classifies_missing_shape():
    with pytest.raises(LoginFlowError) as exc:
        extract_iframe_src("<html>No iframe</html>")

    assert exc.value.code == "iframe_src_missing"


def test_extract_canvas_login_form_reads_action_and_named_inputs():
    html = """
    <form id="login_form" action="https://canvas.ssu.ac.kr/login/canvas">
      <input name="authenticity_token" value="csrf-token" />
      <input name="pseudonym_session[unique_id]" value="student" />
      <input name="empty" />
      <input value="ignored-without-name" />
    </form>
    """

    form = extract_canvas_login_form(html)

    assert form == CanvasLoginForm(
        action="https://canvas.ssu.ac.kr/login/canvas",
        fields={
            "authenticity_token": "csrf-token",
            "pseudonym_session[unique_id]": "student",
            "empty": "",
        },
    )


def test_extract_canvas_login_form_classifies_missing_form():
    with pytest.raises(LoginFlowError) as exc:
        extract_canvas_login_form("<form id='other'></form>")

    assert exc.value.code == "canvas_form_missing"


def test_extract_login_cryption_restores_escaped_private_key_newlines():
    html = 'window.loginCryption("encrypted-b64", "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----");'

    cryption = extract_login_cryption(html)

    assert cryption == LoginCryption(
        encrypted_data_b64="encrypted-b64",
        private_key_pem="-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
    )


def test_extract_login_cryption_classifies_missing_script():
    with pytest.raises(LoginFlowError) as exc:
        extract_login_cryption("<script>console.log('nope')</script>")

    assert exc.value.code == "login_cryption_missing"
