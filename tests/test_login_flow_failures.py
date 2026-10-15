from ssu_lecture_downloader.login_flow import run_http_login_discovery


class ExplodingHttpClient:
    cookies = {}
    trace = None

    def get(self, step, url, **kwargs):
        raise RuntimeError("network exploded with raw-token-like text")

    def post(self, step, url, **kwargs):
        raise AssertionError("should not post")


def test_run_http_login_discovery_classifies_unexpected_exceptions():
    result = run_http_login_discovery("student-id", "student-password", http=ExplodingHttpClient())

    assert result.ok is False
    assert result.error_code == "unknown_response_shape"
    assert "student-password" not in str(result.to_dict())
