import json

from ssu_lecture_downloader.trace import NetworkTrace, TraceEntry


def test_trace_entry_exports_redacted_request_response_context():
    trace = NetworkTrace()

    trace.add(
        TraceEntry(
            step="submit_login",
            method="POST",
            url="https://smartid.ssu.ac.kr/login?token=raw-token&course_id=123",
            status_code=302,
            final_url="https://lms.ssu.ac.kr/callback?ticket=raw-ticket",
            elapsed_ms=42.5,
            request_headers={"Authorization": "Bearer raw-bearer", "User-Agent": "pytest"},
            response_headers={"Set-Cookie": "SESSION=raw-cookie", "Content-Type": "text/html"},
            cookie_names=["SESSION", "xn_api_token"],
            body_preview={"password": "raw-password", "safe_count": 1},
            body_size=128,
        )
    )

    payload = trace.to_dict()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["entries"][0]["step"] == "submit_login"
    assert payload["entries"][0]["cookie_names"] == ["SESSION", "xn_api_token"]
    assert payload["entries"][0]["request_headers"]["Authorization"] == "<redacted>"
    assert payload["entries"][0]["response_headers"]["Set-Cookie"] == "<redacted>"
    assert payload["entries"][0]["body_preview"]["password"] == "<redacted>"
    assert "raw-token" not in serialized
    assert "raw-ticket" not in serialized
    assert "raw-bearer" not in serialized
    assert "raw-cookie" not in serialized
    assert "raw-password" not in serialized


def test_trace_json_is_stable_and_pretty():
    trace = NetworkTrace()
    trace.add(
        TraceEntry(
            step="entry",
            method="GET",
            url="https://lms.ssu.ac.kr/",
            status_code=200,
            final_url="https://lms.ssu.ac.kr/",
            elapsed_ms=1.0,
        )
    )

    output = trace.to_json()

    assert json.loads(output)["entries"][0]["step"] == "entry"
    assert "\n" in output
