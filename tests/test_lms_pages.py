"""Hidden LMS items: page/board/other rows, wiki-page attachments, Canvas file download, page cookies."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ssu_lecture_downloader.service.api import create_app
from ssu_lecture_downloader.core.auth import SessionState
from ssu_lecture_downloader.core.canvas_files import (
    extract_file_links,
    file_id_from_address,
    parse_canvas_json,
    wiki_page_ref,
)
from ssu_lecture_downloader.service.credentials import CredentialPair
from ssu_lecture_downloader.core.lms import LearningXLister
from ssu_lecture_downloader.core.login_flow import LoginDiscoveryResult
from ssu_lecture_downloader.core.lms_service import SsuLectureService

PAGE_BODY = """
<p>이번 주 자료입니다.</p>
<p><a class="instructure_file_link instructure_scribd_file" title="1주차.pdf"
   href="https://canvas.ssu.ac.kr/courses/7/files/101?wrap=1"
   data-api-endpoint="https://canvas.ssu.ac.kr/api/v1/courses/7/files/101" data-api-returntype="File">1주차 강의자료.pdf</a></p>
<p><a href="/courses/7/files/102/download?download_frd=1">실습 파일</a></p>
<p><a href="/courses/7/files/102/preview">미리보기</a> (같은 파일)</p>
<p><a href="https://example.com/courses/7/files/999/download">외부 링크</a></p>
<p><a href="/courses/7/pages/other-page">다른 페이지</a></p>
<p><a href="/courses/7/files/folder/1">폴더</a></p>
<p><img src="/courses/7/files/103/preview" alt="그림"></p>
<p><a class="instructure_file_link" href="/files/104/download">홈 파일</a></p>
"""


# ------------------------------------------------------------------ parsing


def test_extract_file_links_finds_attachment_links_once_in_page_order():
    links = extract_file_links(PAGE_BODY)

    assert [link.file_id for link in links] == ["101", "102", "104"]
    assert links[0].link_text == "1주차 강의자료.pdf"
    assert links[1].link_text == "실습 파일"


def test_extract_file_links_uses_api_endpoint_attribute_and_ignores_other_hosts_and_images():
    html = '<span data-api-endpoint="/api/v1/courses/7/files/55"></span><a href="https://evil.example/files/66">x</a><img src="/files/77/preview">'

    assert [link.file_id for link in extract_file_links(html)] == ["55"]
    assert extract_file_links("") == []
    assert extract_file_links("<p>첨부 없음</p>") == []


def test_extract_file_links_is_bounded():
    html = "".join(f'<a href="/courses/7/files/{n}/download">f{n}</a>' for n in range(1, 120))

    assert len(extract_file_links(html, limit=5)) == 5


def test_file_id_from_address_rejects_foreign_hosts_and_schemes():
    assert file_id_from_address("https://canvas.ssu.ac.kr/api/v1/files/12") == "12"
    assert file_id_from_address("/courses/1/files/12/download") == "12"
    assert file_id_from_address("javascript:alert(1)//files/12") is None
    assert file_id_from_address("https://ssu.ac.kr.evil.com/files/12") is None
    assert file_id_from_address(None) is None


def test_wiki_page_ref_prefers_slug_then_address_then_numeric_id():
    assert wiki_page_ref(url=None, page_url="week-1", resource_id="5") == "week-1"
    assert wiki_page_ref(url="https://canvas.ssu.ac.kr/api/v1/courses/7/pages/1%EC%A3%BC%EC%B0%A8", page_url=None, resource_id=None) == "1주차"
    assert wiki_page_ref(url="/courses/7/modules/items/9", page_url=None, resource_id=1234) == "1234"
    assert wiki_page_ref(url=None, page_url=None, resource_id="abc") is None
    assert wiki_page_ref(url=None, page_url=None, resource_id=None) is None


def test_parse_canvas_json_strips_the_while_guard():
    class Guarded:
        text = 'while(1);{"a": 1}'

        def json(self):
            raise AssertionError("the guarded body is not valid JSON")

    class Plain:
        text = '{"a": 2}'

        def json(self):
            return {"a": 2}

    assert parse_canvas_json(Guarded()) == {"a": 1}
    assert parse_canvas_json(Plain()) == {"a": 2}


# ---------------------------------------------------------- module item rows


def module_item(**overrides):
    item = {"module_item_id": 1, "title": "항목", "content_data": {}, "published": True}
    item.update(overrides)
    return item


def test_hidden_item_kinds_are_extracted_with_their_page_reference():
    modules = [
        {
            "id": 1,
            "name": "1주차",
            "week_position": 1,
            "module_items": [
                module_item(module_item_id=11, title="1주차 강의자료", content_type="wiki_page", content_id=5001, url="https://canvas.ssu.ac.kr/api/v1/courses/7/pages/week-1"),
                module_item(module_item_id=12, title="질문 게시판", content_type="board", content_id=88, url="/courses/7/discussion_topics/88"),
                module_item(
                    module_item_id=13,
                    title="출석 보조",
                    content_type="attendance_item",
                    content_data={"item_content_data": {"content_type": "zip-archive"}},
                ),
                module_item(module_item_id=14, title="소제목", content_type="sub_header"),
            ],
        }
    ]

    items = {item.module_item_id: item for item in LearningXLister(None).extract_course_content_items(modules)}

    assert (items[11].kind, items[11].page_ref, items[11].resource_id, items[11].downloadable) == ("page", "week-1", "5001", False)
    assert (items[12].kind, items[12].page_ref, items[12].resource_id) == ("board", None, "88")
    assert (items[13].kind, items[13].inner_content_type, items[13].downloadable) == ("unknown", "zip-archive", False)
    assert items[14].kind == "section"
    # Existing rows are untouched by the new fields.
    video = LearningXLister(None).extract_course_content_items(
        [{"id": 1, "name": "w", "module_items": [module_item(content_type="attendance_item", content_data={"item_content_data": {"content_type": "mp4", "duration": 5, "content_id": "c1"}})]}]
    )[0]
    assert (video.kind, video.page_ref, video.content_id) == ("video", None, "c1")


# ------------------------------------------------------------ fake Canvas


class Response:
    def __init__(self, payload=None, *, status_code=200, headers=None, content=b"", text=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")
        self.url = "https://canvas.ssu.ac.kr/"

    def raise_for_status(self):
        if self.status_code >= 400:
            error = RuntimeError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1024):
        yield self.content


class CanvasHttp:
    """Answers the Canvas page/file API and the file download; records every request."""

    def __init__(self):
        self.cookies = {"xn_api_token": "tok", "canvas_session": "SESSION-SECRET"}
        self.trace = None
        self.calls = []
        self.guard = False
        self.files = {
            "101": {"id": "101", "display_name": "1주차 강의자료.pdf", "filename": "w1.pdf", "size": 11, "url": "https://canvas.ssu.ac.kr/files/101/download?download_frd=1&verifier=abc", "content-type": "application/pdf"},
            "102": {"id": "102", "display_name": "실습", "size": 4, "url": "https://canvas.ssu.ac.kr/files/102/download?verifier=def", "content-type": "application/zip"},
        }
        self.bodies = {"101": b"%PDF-1.4 abc", "102": b"1234"}
        self.locked = {"104"}
        self.expire_first_download = False

    def close(self):
        pass

    def get(self, step, url, **kwargs):
        self.calls.append((step, url, kwargs))
        if step == "get_canvas_page":
            payload = {"page_id": 5001, "url": "week-1", "title": "1주차 강의자료", "body": PAGE_BODY}
            if self.guard:
                return Response(payload, text="while(1);" + json.dumps(payload))
            return Response(payload)
        if step == "get_canvas_file":
            file_id = url.rsplit("/", 1)[-1]
            if file_id in self.locked:
                return Response({"status": "unauthorized"}, status_code=403)
            return Response(self.files[file_id])
        if step == "download_media":
            file_id = url.split("/files/")[1].split("/")[0]
            if self.expire_first_download:
                self.expire_first_download = False
                response = Response(status_code=403)
                response.url = "https://files.example-cdn.test/expired"
                return response
            body = self.bodies[file_id]
            return Response(content=body, headers={"content-length": str(len(body))})
        raise AssertionError(step)


def make_service():
    http = CanvasHttp()
    logins = []

    def login(username, password, client):
        logins.append(username)
        client.cookies.update({"xn_api_token": "tok", "canvas_session": "SESSION-SECRET"})
        return LoginDiscoveryResult(ok=True, error_code=None, session=SessionState(xn_api_token_present=True, cookie_names=["xn_api_token"]))

    service = SsuLectureService(http=http, login_func=login, current_term_candidates=[46])
    return service, http


# ----------------------------------------------------------------- service


def test_list_page_files_reads_the_page_body_then_each_file_through_the_canvas_api():
    service, http = make_service()

    result = service.list_page_files("u", "p", course_id="7", page_ref="week-1")

    assert result.ok and result.title == "1주차 강의자료"
    assert [(file.file_id, file.display_name, file.size) for file in result.files] == [
        ("101", "1주차 강의자료.pdf", 11),
        ("102", "실습", 4),
        ("104", "홈 파일", None),
    ]
    assert result.files[0].download_url == "https://canvas.ssu.ac.kr/files/101/download?download_frd=1&verifier=abc"
    assert result.files[2].download_url is None  # locked: listed, not downloadable
    assert [(step, url) for step, url, _ in http.calls] == [
        ("get_canvas_page", "https://canvas.ssu.ac.kr/api/v1/courses/7/pages/week-1"),
        ("get_canvas_file", "https://canvas.ssu.ac.kr/api/v1/files/101"),
        ("get_canvas_file", "https://canvas.ssu.ac.kr/api/v1/files/102"),
        ("get_canvas_file", "https://canvas.ssu.ac.kr/api/v1/files/104"),
    ]
    # Cookies authenticate the request but are never part of the result.
    assert "SESSION-SECRET" not in repr(result)


def test_list_page_files_quotes_the_page_reference_and_accepts_a_guarded_body():
    service, http = make_service()
    http.guard = True

    result = service.list_page_files("u", "p", course_id=7, page_ref="1주차 자료/..")

    assert result.ok and len(result.files) == 3
    assert http.calls[0][1] == "https://canvas.ssu.ac.kr/api/v1/courses/7/pages/1%EC%A3%BC%EC%B0%A8%20%EC%9E%90%EB%A3%8C%2F.."


def test_download_canvas_file_writes_atomically_into_the_week_folder_named_in_its_title(tmp_path: Path):
    service, http = make_service()

    result = service.download_canvas_file("u", "p", file_id="101", course_name="UX방법론", output_dir=tmp_path)

    assert result.ok and result.written and not result.skipped
    target = Path(result.output_path)
    assert target == tmp_path / "UX방법론" / "01주차" / "1주차 강의자료.pdf"
    assert (target.parent / ".ssu" / "materials.json").is_file()
    assert target.read_bytes() == b"%PDF-1.4 abc"
    assert not list(tmp_path.rglob("*.part")), "the .part file is renamed away"
    assert result.file_size == len(b"%PDF-1.4 abc")
    download_calls = [call for call in http.calls if call[0] == "download_media"]
    assert len(download_calls) == 1
    assert download_calls[0][2]["headers"]["Referer"] == "https://canvas.ssu.ac.kr/"


def test_download_canvas_file_skips_an_existing_file_and_keeps_it(tmp_path: Path):
    service, http = make_service()
    first = service.download_canvas_file("u", "p", file_id="102", course_name="c", output_dir=tmp_path)
    again = service.download_canvas_file("u", "p", file_id="102", course_name="c", output_dir=tmp_path)

    assert first.written and again.skipped and not again.written
    assert again.output_path == first.output_path
    assert len([call for call in http.calls if call[0] == "download_media"]) == 1
    assert Path(first.output_path) == tmp_path / "c" / "기타" / "실습.bin"


def test_download_canvas_file_reports_files_canvas_refuses(tmp_path: Path):
    service, _ = make_service()

    result = service.download_canvas_file("u", "p", file_id="104", course_name="c", output_dir=tmp_path)

    assert not result.ok and result.error_code == "canvas_file_unavailable"
    assert not list(tmp_path.rglob("*"))


def test_download_canvas_file_asks_canvas_for_a_fresh_address_when_the_signed_one_expired(tmp_path: Path):
    service, http = make_service()
    http.expire_first_download = True

    result = service.download_canvas_file("u", "p", file_id="102", course_name="c", output_dir=tmp_path)

    assert result.ok and Path(result.output_path).read_bytes() == b"1234"
    assert len([call for call in http.calls if call[0] == "get_canvas_file"]) == 2
    assert len([call for call in http.calls if call[0] == "download_media"]) == 2


def test_lms_session_cookies_return_the_bridge_cookies_without_touching_canvas_pages():
    service, http = make_service()

    result = service.lms_session_cookies("u", "p")

    assert result.ok
    assert {cookie.name for cookie in result.cookies} == {"xn_api_token", "canvas_session"}
    assert result.diagnostics["cookie_count"] == 2
    assert http.calls == []


# --------------------------------------------------------------------- API


class MemoryCredentials:
    def __init__(self):
        self.pair = CredentialPair(username="u", password="p")

    def get(self):
        return self.pair

    def status(self):
        return {"source": "test", "username_present": True, "password_present": True, "can_write": True}

    def save(self, pair):
        self.pair = pair

    def delete(self):
        self.pair = None


@pytest.fixture
def api(tmp_path: Path):
    service, http = make_service()
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=MemoryCredentials())
    return TestClient(app), http, tmp_path


def test_api_lists_page_attachments_and_marks_the_ones_already_saved(api):
    client, http, root = api

    first = client.get("/api/courses/7/pages/week-1/files", params={"course_name": "UX방법론"})
    assert first.status_code == 200
    payload = first.json()
    assert payload["ok"] is True and payload["file_count"] == 3 and payload["title"] == "1주차 강의자료"
    assert [file["file_id"] for file in payload["files"]] == ["101", "102", "104"]
    assert payload["files"][0]["download_url"].startswith("https://canvas.ssu.ac.kr/files/101/download")
    assert all(file["downloaded_path"] is None for file in payload["files"])
    assert "SESSION-SECRET" not in first.text and "cookie" not in first.text.lower()

    saved = client.post("/api/download/canvas-file", json={"course_id": 7, "file_id": "101", "course_name": "UX방법론"})
    assert saved.status_code == 200 and saved.json()["written"] is True
    assert saved.json()["output_path"].startswith(str(root / "UX방법론"))

    second = client.get("/api/courses/7/pages/week-1/files", params={"course_name": "UX방법론"}).json()
    assert second["files"][0]["downloaded_path"] == saved.json()["output_path"]
    assert second["files"][1]["downloaded_path"] is None


def test_api_download_honours_the_chosen_folder_and_conflict_policy(api, tmp_path: Path):
    client, _, _ = api
    chosen = tmp_path / "내 폴더"

    body = {"course_id": 7, "file_id": 102, "course_name": "c", "output_dir": str(chosen), "conflict_strategy": "rename"}
    first = client.post("/api/download/canvas-file", json=body).json()
    second = client.post("/api/download/canvas-file", json=body).json()

    assert Path(first["output_path"]).parent == chosen / "c" / "기타"
    assert second["output_path"] != first["output_path"] and second["written"]


def test_api_validates_ids_and_references(api):
    client, _, _ = api

    assert client.get("/api/courses/abc/pages/week-1/files").json()["error_code"] == "invalid_course_id"
    assert client.get("/api/courses/7/pages/%20/files").json()["error_code"] == "invalid_page_ref"
    assert client.get("/api/courses/7/pages/a%5Cb/files").json()["error_code"] == "invalid_page_ref"
    assert client.get("/api/courses/7/pages/" + "x" * 300 + "/files").json()["error_code"] == "invalid_page_ref"
    bad_file = client.post("/api/download/canvas-file", json={"course_id": 7, "file_id": "1/../2"})
    assert (bad_file.status_code, bad_file.json()["error_code"]) == (400, "invalid_file_id")
    bad_strategy = client.post("/api/download/canvas-file", json={"course_id": 7, "file_id": 1, "conflict_strategy": "explode"})
    assert (bad_strategy.status_code, bad_strategy.json()["error_code"]) == (400, "invalid_conflict_strategy")


def test_api_unavailable_canvas_file_is_a_clean_error(api):
    client, _, _ = api

    response = client.post("/api/download/canvas-file", json={"course_id": 7, "file_id": 104, "course_name": "c"})

    assert response.status_code == 502
    assert response.json()["error_code"] == "canvas_file_unavailable"


def test_api_session_bridge_returns_cookies_for_the_page_window(api):
    client, _, _ = api

    response = client.post("/api/lms/session-bridge")

    assert response.status_code == 200
    payload = response.json()
    assert {cookie["name"] for cookie in payload["cookies"]} == {"xn_api_token", "canvas_session"}
    assert payload["diagnostics"]["cookie_count"] == 2
    assert "SESSION-SECRET" not in json.dumps(payload["diagnostics"])


def test_api_session_bridge_is_protected_by_the_api_token(tmp_path: Path):
    service, _ = make_service()
    app = create_app(service_factory=lambda: service, default_output_dir=tmp_path, credential_store=MemoryCredentials(), api_token="secret-token")
    client = TestClient(app)

    assert client.post("/api/lms/session-bridge").status_code == 401
    assert client.get("/api/courses/7/pages/week-1/files").status_code == 401
    assert client.post("/api/lms/session-bridge", headers={"X-SSU-Token": "secret-token"}).status_code == 200


def test_api_health_advertises_the_lms_page_routes(api):
    client, _, _ = api

    assert "lms_pages" in client.get("/api/health").json()["capabilities"]


def test_api_canvas_file_lands_in_the_week_of_the_page_it_was_opened_from_and_the_second_download_is_skipped(api):
    client, _, root = api

    body = {"course_id": 7, "file_id": "101", "course_name": "UX방법론", "module_name": "3주차: 실습", "week_position": 3}
    first = client.post("/api/download/canvas-file", json=body).json()
    again = client.post("/api/download/canvas-file", json=body).json()
    other = client.post("/api/download/canvas-file", json={"course_id": 7, "file_id": "102", "course_name": "UX방법론"}).json()

    assert first["output_path"] == str(root / "UX방법론" / "03주차" / "1주차 강의자료.pdf")  # the page's week beats the "1주차" in the file title
    assert again["skipped"] is True and again["written"] is False and again["output_path"] == first["output_path"]
    assert other["output_path"] == str(root / "UX방법론" / "기타" / "실습.bin")
    assert (root / "UX방법론" / "03주차" / ".ssu" / "materials.json").is_file()


def test_canvas_files_with_one_title_in_a_week_are_numbered(tmp_path: Path):
    service, http = make_service()
    http.files["103"] = {**http.files["101"], "id": "103", "url": "https://canvas.ssu.ac.kr/files/103/download?verifier=ghi"}
    http.bodies["103"] = b"%PDF-1.4 other"

    first = service.download_canvas_file("u", "p", file_id="101", course_name="c", output_dir=tmp_path, week_position=1)
    second = service.download_canvas_file("u", "p", file_id="103", course_name="c", output_dir=tmp_path, week_position=1)
    first_again = service.download_canvas_file("u", "p", file_id="101", course_name="c", output_dir=tmp_path, week_position=1)

    assert Path(first.output_path).name == "1주차 강의자료.pdf"
    assert Path(second.output_path).name == "1주차 강의자료 (2).pdf"
    assert first_again.skipped is True and first_again.output_path == first.output_path
    assert Path(second.output_path).read_bytes() == b"%PDF-1.4 other"
