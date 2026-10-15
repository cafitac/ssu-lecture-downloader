from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from ssu_lecture_downloader.errors import LoginFlowError
from ssu_lecture_downloader.postprocess_media import AudioExtractionRequest, AudioExtractionResult, extract_audio, ffmpeg_install_hint, plan_audio_output_path


@dataclass(frozen=True)
class LtiLaunchForm:
    action: str
    fields: dict[str, str]


@dataclass(frozen=True)
class AttendanceItemMedia:
    item_id: int | str
    course_id: int | str
    title: str
    learningx_content_id: str | None
    content_id: str | None
    content_type: str | None
    duration: int | float | None
    viewer_url: str | None


@dataclass(frozen=True)
class CommonsDownload:
    content_id: str
    title: str
    download_url: str
    total_file_size: int | None


def extract_lti_launch_form(html: str) -> LtiLaunchForm:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="tool_form") or soup.find("form", attrs={"data-message-type": "tool_launch"})
    if not form:
        raise LoginFlowError("lti_launch_form_missing", "LTI tool launch form not found")
    action = form.get("action")
    if not isinstance(action, str) or not action:
        raise LoginFlowError("lti_launch_action_missing", "LTI tool launch form action missing")
    fields: dict[str, str] = {}
    for input_el in form.find_all("input"):
        name = input_el.get("name")
        if isinstance(name, str) and name:
            fields[name] = input_el.get("value", "")
    return LtiLaunchForm(action=action, fields=fields)


def extract_mp4_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in re.findall(r"https?://[^\"'<>\s]+?\.mp4", text):
        if match not in urls:
            urls.append(match)
    return urls


def parse_attendance_item_media(payload: dict[str, Any]) -> AttendanceItemMedia:
    item_content_data = payload.get("item_content_data") or {}
    if not isinstance(item_content_data, dict):
        item_content_data = {}
    return AttendanceItemMedia(
        item_id=payload.get("item_id"),
        course_id=payload.get("course_id"),
        title=payload.get("title") or "",
        learningx_content_id=payload.get("item_content_id"),
        content_id=item_content_data.get("content_id"),
        content_type=item_content_data.get("content_type"),
        duration=item_content_data.get("duration"),
        viewer_url=payload.get("viewer_url"),
    )


def parse_commons_download(payload: dict[str, Any]) -> CommonsDownload:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise LoginFlowError("commons_download_missing", "Commons content result missing")
    download_url = result.get("download_url")
    content_id = result.get("content_id")
    if not isinstance(download_url, str) or not download_url:
        raise LoginFlowError("commons_download_url_missing", "Commons download URL missing")
    if not isinstance(content_id, str) or not content_id:
        raise LoginFlowError("commons_content_id_missing", "Commons content id missing")
    return CommonsDownload(
        content_id=content_id,
        title=result.get("title") or "",
        download_url=download_url,
        total_file_size=result.get("total_file_size"),
    )
