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


from curl_cffi.requests import exceptions as curl_exceptions

from ssu_lecture_downloader import cli
from ssu_lecture_downloader.errors import LoginFlowError


def run_main_with_failure(monkeypatch, error, argv):
    def failing(args):
        raise error

    monkeypatch.setitem(cli.COMMANDS, 'login', failing)
    return cli.main(argv)


def test_cli_unexpected_exception_exits_nonzero_with_concise_stderr_message(monkeypatch, capsys):
    code = run_main_with_failure(monkeypatch, RuntimeError('boom while parsing'), ['login'])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ''
    assert captured.err.strip() == 'Error: RuntimeError: boom while parsing'
    assert 'Traceback' not in captured.err


def test_cli_unexpected_exception_prints_json_error_with_json_flag(monkeypatch, capsys):
    code = run_main_with_failure(monkeypatch, curl_exceptions.Timeout('Operation timed out after 30000 ms'), ['login', '--json'])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload['ok'] is False
    assert payload['error_code'] == 'network_timeout'
    assert payload['message']


def test_cli_login_flow_errors_report_their_own_code(monkeypatch, capsys):
    code = run_main_with_failure(monkeypatch, LoginFlowError('lti_launch_form_missing', 'LTI tool launch form not found'), ['login', '--json'])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload == {'ok': False, 'error_code': 'lti_launch_form_missing', 'message': 'LTI tool launch form not found'}


def test_cli_error_message_redacts_signed_urls_and_is_truncated(monkeypatch, capsys):
    error = ValueError('failed https://cdn.example/video.mp4?token=super-secret&x=1 ' + 'z' * 1000)

    run_main_with_failure(monkeypatch, error, ['login', '--json'])

    payload = json.loads(capsys.readouterr().out)
    assert 'super-secret' not in payload['message']
    assert len(payload['message']) <= cli.MAX_ERROR_MESSAGE_CHARS


def test_cli_keyboard_interrupt_exits_130(monkeypatch, capsys):
    assert run_main_with_failure(monkeypatch, KeyboardInterrupt(), ['login']) == 130


def test_cli_still_returns_the_handler_exit_code(monkeypatch):
    monkeypatch.setitem(cli.COMMANDS, 'login', lambda args: 2)

    assert cli.main(['login']) == 2
