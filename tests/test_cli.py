import json
import subprocess
import sys

from ssu_lecture_downloader.cli import format_progress_event


def test_cli_help_mentions_json_and_trace_options():
    result = subprocess.run(
        [sys.executable, "-m", "ssu_lecture_downloader.cli", "login", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--json" in result.stdout
    assert "--trace" in result.stdout


def test_discover_lms_help_mentions_term_id_json_and_trace_options():
    result = subprocess.run(
        [sys.executable, "-m", "ssu_lecture_downloader.cli", "discover-lms", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--json" in result.stdout
    assert "--trace" in result.stdout
    assert "--term-id" in result.stdout
    assert "current" in result.stdout
    assert "ended=False" in result.stdout


def test_course_detail_help_mentions_course_id_and_json_options():
    result = subprocess.run(
        [sys.executable, "-m", "ssu_lecture_downloader.cli", "course-detail", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--course-id" in result.stdout
    assert "--course-name" in result.stdout
    assert "--json" in result.stdout
    assert "lecture" in result.stdout.lower()


def test_cli_progress_formatter_reports_byte_percent_for_streaming_download():
    line = format_progress_event({
        'stage': 'download_progress',
        'bytes_written': 2,
        'total_bytes': 4,
        'index': 1,
        'total': 3,
        'title': '강의 1',
    })

    assert line == '[1/3] download_progress: 강의 1 2/4 bytes (50.0%)'


def test_batch_download_help_mentions_selection_and_uncompleted_options():
    result = subprocess.run(
        [sys.executable, "-m", "ssu_lecture_downloader.cli", "batch-download", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--module-item-id" in result.stdout
    assert "--uncompleted" in result.stdout
    assert "--dry-run" in result.stdout


def test_resolve_media_help_mentions_required_ids_and_trace_options():
    result = subprocess.run(
        [sys.executable, "-m", "ssu_lecture_downloader.cli", "resolve-media", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--course-id" in result.stdout
    assert "--module-item-id" in result.stdout
    assert "--trace" in result.stdout


def test_download_help_mentions_output_dir_and_dry_run_options():
    result = subprocess.run(
        [sys.executable, "-m", "ssu_lecture_downloader.cli", "download", "--help"],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "--course-id" in result.stdout
    assert "--module-item-id" in result.stdout
    assert "--output-dir" in result.stdout
    assert "--dry-run" in result.stdout


def test_cli_json_status_reports_missing_env_without_values(tmp_path):
    src_path = str((__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(
        [sys.executable, "-m", "ssu_lecture_downloader.cli", "login", "--json"],
        check=False,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env={"PYTHONPATH": src_path},
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == "missing_credentials"
    assert payload["credentials"]["SSU_USERNAME"] == "missing"
    assert payload["credentials"]["SSU_PASSWORD"] == "missing"
    assert "SSU_PASSWORD=" not in result.stdout


def test_download_json_status_reports_missing_env_without_values(tmp_path):
    src_path = str((__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ssu_lecture_downloader.cli",
            "download",
            "--course-id",
            "1",
            "--module-item-id",
            "2",
            "--output-dir",
            str(tmp_path),
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env={"PYTHONPATH": src_path},
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == "missing_credentials"
    assert payload["credentials"]["SSU_USERNAME"] == "missing"
    assert payload["credentials"]["SSU_PASSWORD"] == "missing"
    assert "SSU_PASSWORD=" not in result.stdout
