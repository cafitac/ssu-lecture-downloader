from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from ssu_lecture_downloader.http_client import HttpClient


class RecordingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Set-Cookie", "SESSION=raw-cookie; Path=/")
        self.end_headers()
        self.wfile.write(f"GET {self.path}".encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        self.send_response(302)
        self.send_header("Location", "/done?ticket=raw-ticket")
        self.send_header("Set-Cookie", "xn_api_token=raw-token; Path=/")
        self.end_headers()
        self.wfile.write(f"POST {body}".encode())

    def log_message(self, format, *args):
        return


def run_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_http_client_get_records_redacted_trace_entry():
    server = run_server()
    client = HttpClient()

    try:
        response = client.get("fetch_entry", f"http://127.0.0.1:{server.server_port}/hello?token=raw-token&course_id=123")
    finally:
        server.shutdown()

    payload = client.trace.to_dict()
    serialized = client.trace.to_json()

    assert response.status_code == 200
    assert payload["entries"][0]["step"] == "fetch_entry"
    assert payload["entries"][0]["method"] == "GET"
    assert payload["entries"][0]["status_code"] == 200
    assert payload["entries"][0]["cookie_names"] == ["SESSION"]
    assert "course_id=123" in payload["entries"][0]["url"]
    assert "raw-token" not in serialized
    assert "raw-cookie" not in serialized


def test_http_client_post_records_elapsed_status_and_cookie_names():
    server = run_server()
    client = HttpClient()

    try:
        response = client.post(
            "submit_login",
            f"http://127.0.0.1:{server.server_port}/login",
            data={"userid": "student", "pwd": "secret"},
            allow_redirects=False,
        )
    finally:
        server.shutdown()

    payload = client.trace.to_dict()
    serialized = client.trace.to_json()

    assert response.status_code == 302
    assert payload["entries"][0]["step"] == "submit_login"
    assert payload["entries"][0]["method"] == "POST"
    assert payload["entries"][0]["status_code"] == 302
    assert payload["entries"][0]["elapsed_ms"] >= 0
    assert payload["entries"][0]["cookie_names"] == ["xn_api_token"]
    assert "secret" not in serialized
    assert "raw-ticket" not in serialized
    assert "raw-token" not in serialized
