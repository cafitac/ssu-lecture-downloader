from ssu_lecture_downloader.core.redaction import redact_headers, redact_url, redact_mapping


def test_redacts_sensitive_headers_without_dropping_safe_context():
    headers = {
        "Authorization": "Bearer raw-token-value",
        "Cookie": "sessionid=raw-cookie-value",
        "User-Agent": "pytest-agent",
    }

    redacted = redact_headers(headers)

    assert redacted["Authorization"] == "<redacted>"
    assert redacted["Cookie"] == "<redacted>"
    assert redacted["User-Agent"] == "pytest-agent"
    assert "raw-token-value" not in str(redacted)
    assert "raw-cookie-value" not in str(redacted)


def test_redacts_sensitive_referer_url_header():
    headers = {"Referer": "https://commons.ssu.ac.kr/em/abc?TargetUrl=https%3A%2F%2Fcanvas.ssu.ac.kr%2Fprogress%3Fuser_id%3Dstudent-id"}

    redacted = redact_headers(headers)

    assert "student-id" not in redacted["Referer"]


def test_redacts_sensitive_query_values_but_keeps_url_shape():
    url = "https://canvas.ssu.ac.kr/path?token=secret-token&course_id=123&password=secret-password&sIdno=student-id"

    redacted = redact_url(url)

    assert redacted.startswith("https://canvas.ssu.ac.kr/path?")
    assert "course_id=123" in redacted
    assert "secret-token" not in redacted
    assert "secret-password" not in redacted
    assert "student-id" not in redacted
    assert "token=%3Credacted%3E" in redacted or "token=<redacted>" in redacted


def test_redact_url_strips_header_control_characters():
    url = "https://commons.ssu.ac.kr/index.php?content_id=abc&token=secret\r\n"

    redacted = redact_url(url)

    assert "secret" not in redacted
    assert "\r" not in redacted
    assert "\n" not in redacted
    assert redacted.endswith("token=%3Credacted%3E")


def test_redact_url_recursively_redacts_nested_url_query_values():
    url = "https://commons.ssu.ac.kr/em/abc?TargetUrl=https%3A%2F%2Fcanvas.ssu.ac.kr%2Fprogress%3Fuser_id%3Dstudent-id%26content_id%3Dcontent-1"

    redacted = redact_url(url)

    assert "student-id" not in redacted
    assert "user_id%3D%253Credacted%253E" in redacted or "user_id" in redacted


def test_redacts_nested_credential_like_mapping_values():
    payload = {
        "username": "student-id",
        "password": "student-password",
        "nested": {"xn_api_token": "raw-api-token", "count": 3},
    }

    redacted = redact_mapping(payload)

    assert redacted["username"] == "<redacted>"
    assert redacted["password"] == "<redacted>"
    assert redacted["nested"]["xn_api_token"] == "<redacted>"
    assert redacted["nested"]["count"] == 3
    assert "student-id" not in str(redacted)
    assert "student-password" not in str(redacted)
    assert "raw-api-token" not in str(redacted)
