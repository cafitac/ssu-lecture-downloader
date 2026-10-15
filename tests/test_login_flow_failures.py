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


import types

import pytest
from curl_cffi.requests import exceptions as curl_exceptions

from ssu_lecture_downloader.api_errors import AUTHENTICATION_ERROR_CODES


class FailingHttpClient:
    cookies = {}
    trace = None

    def __init__(self, error):
        self.error = error

    def get(self, step, url, **kwargs):
        raise self.error

    def post(self, step, url, **kwargs):
        raise AssertionError("should not post")


def http_status_error(status):
    error = curl_exceptions.HTTPError(f"HTTP {status}")
    error.response = types.SimpleNamespace(status_code=status, url="https://lms.ssu.ac.kr/")
    return error


@pytest.mark.parametrize(
    "error, expected",
    [
        (curl_exceptions.Timeout("timed out"), "network_timeout"),
        (curl_exceptions.ReadTimeout("read timed out"), "network_timeout"),
        (curl_exceptions.ConnectionError("refused"), "network_error"),
        (curl_exceptions.DNSError("could not resolve host"), "network_error"),
        (curl_exceptions.SSLError("tls"), "network_error"),
        (http_status_error(503), "network_error"),
        (TimeoutError("socket timeout"), "network_timeout"),
    ],
)
def test_run_http_login_discovery_reports_transport_failures_as_network_errors(error, expected):
    result = run_http_login_discovery("student-id", "student-password", http=FailingHttpClient(error))

    assert result.ok is False
    assert result.error_code == expected
    # Valid credentials must never be reported as rejected because the network hiccuped.
    assert result.error_code not in AUTHENTICATION_ERROR_CODES


def test_run_http_login_discovery_keeps_shape_errors_in_the_auth_class():
    result = run_http_login_discovery("student-id", "student-password", http=FailingHttpClient(http_status_error(404)))

    assert result.error_code == "unknown_response_shape"
