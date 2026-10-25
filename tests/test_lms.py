from ssu_lecture_downloader.lms import LearningXLister, is_video_item
from ssu_lecture_downloader.models import LectureItem


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self):
        self.cookies = {"xn_api_token": "raw-token"}
        self.calls = []
        self.course_payload = {"courses": [{"id": 100, "name": "운영체제"}], "total_count": 1}
        self.course_payloads_by_term = {}

    def get(self, step, url, **kwargs):
        self.calls.append((step, url, kwargs))
        if step == "list_courses":
            term_ids = (kwargs.get("params") or {}).get("term_ids[]")
            if term_ids and term_ids[0] in self.course_payloads_by_term:
                return FakeResponse(self.course_payloads_by_term[term_ids[0]])
            return FakeResponse(self.course_payload)
        if step == "list_course_modules":
            return FakeResponse([
                {
                    "id": 1,
                    "name": "1주차",
                    "module_items": [
                        {
                            "id": 10,
                            "module_item_id": 99,
                            "title": "강의 1",
                            "content_type": "attendance_item",
                            "completed": False,
                            "content_data": {"item_content_data": {"content_type": "everlec", "duration": 123}},
                        }
                    ],
                }
            ])
        raise AssertionError(step)


def test_learningx_lister_uses_bearer_token_for_course_api():
    http = FakeHttpClient()
    lister = LearningXLister(http)

    courses = lister.list_courses(term_ids=[43, 40])

    assert courses == [{"id": 100, "name": "운영체제"}]
    step, url, kwargs = http.calls[0]
    assert step == "list_courses"
    assert url == "https://canvas.ssu.ac.kr/learningx/api/v1/learn_activities/courses"
    assert kwargs["headers"]["Authorization"] == "Bearer raw-token"
    assert kwargs["params"] == {"term_ids[]": [43, 40]}


def test_learningx_lister_defaults_to_latest_unended_term_courses():
    http = FakeHttpClient()
    http.course_payloads_by_term = {
        43: {"courses": [{"id": 1, "name": "과거", "term_id": 43, "ended": True}]},
        45: {"courses": [{"id": 4, "name": "비교과", "term_id": 45, "ended": False}]},
        46: {
            "courses": [
                {"id": 2, "name": "현재 A", "term_id": 46, "ended": False},
                {"id": 3, "name": "현재 B", "term_id": 46, "ended": False},
            ]
        },
    }
    lister = LearningXLister(http)

    courses = lister.list_current_courses(term_id_candidates=[43, 45, 46])

    # Still-open non-credit courses of other (yearly) terms follow the current term's courses.
    assert [course["name"] for course in courses] == ["현재 A", "현재 B", "비교과"]
    assert [call[2]["params"] for call in http.calls] == [
        {"term_ids[]": [43]},
        {"term_ids[]": [45]},
        {"term_ids[]": [46]},
    ]


def test_learningx_lister_extracts_video_items_from_modules():
    http = FakeHttpClient()
    lister = LearningXLister(http)

    modules = lister.list_course_modules(100)
    videos = lister.extract_lecture_items(modules)

    assert videos == [
        LectureItem(
            module_id=1,
            module_name="1주차",
            item_id=10,
            module_item_id=99,
            title="강의 1",
            completed=False,
            duration=123,
            content_type="everlec",
        )
    ]


def test_learningx_extracts_lecture_module_title_from_live_shape():
    lister = LearningXLister(http=object())

    lectures = lister.extract_lecture_items([
        {
            "module_id": 790135,
            "title": "1주차: 강의 소개",
            "week_position": 1,
            "module_items": [
                {
                    "module_item_id": 3268578,
                    "title": "26년1학기_UX방법론_1주차",
                    "content_type": "attendance_item",
                    "completed": True,
                    "content_data": {"item_content_data": {"content_type": "movie", "duration": 42}},
                }
            ],
        }
    ])

    assert lectures[0].module_id == 790135
    assert lectures[0].module_name == "1주차: 강의 소개"


def test_is_video_item_accepts_attendance_item_with_duration_or_known_video_type():
    assert is_video_item({"content_type": "attendance_item", "content_data": {"item_content_data": {"duration": 1}}})
    assert is_video_item({"content_type": "attendance_item", "content_data": {"item_content_data": {"content_type": "mp4"}}})
    assert not is_video_item({"content_type": "file", "content_data": {}})


def test_content_item_with_video_shape_but_no_commons_content_id_is_not_downloadable():
    lister = LearningXLister(http=object())

    items = lister.extract_course_content_items([
        {
            "id": 1,
            "name": "1주차",
            "module_items": [
                {
                    "id": 10,
                    "module_item_id": 99,
                    "title": "Zoom 수업 안내",
                    "content_type": "attendance_item",
                    "completed": True,
                    "content_data": {"item_content_data": {"content_type": "everlec", "duration": 5}},
                }
            ],
        }
    ])

    assert items[0].kind == "video"
    assert items[0].downloadable is False
    assert items[0].resolvable is False


def test_content_item_with_commons_content_id_is_downloadable():
    lister = LearningXLister(http=object())

    items = lister.extract_course_content_items([
        {
            "id": 1,
            "name": "1주차",
            "module_items": [
                {
                    "id": 10,
                    "module_item_id": 99,
                    "title": "강의 영상",
                    "content_type": "attendance_item",
                    "completed": True,
                    "content_data": {"item_content_data": {"content_type": "movie", "duration": 42, "content_id": "abc123"}},
                }
            ],
        }
    ])

    assert items[0].kind == "video"
    assert items[0].downloadable is True
    assert items[0].resolvable is True
