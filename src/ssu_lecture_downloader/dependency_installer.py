from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from ssu_lecture_downloader.postprocess_media import ffmpeg_status
from ssu_lecture_downloader.stt import check_stt_provider

InstallProgress = Callable[[dict[str, object]], None]
INSTALL_TIMEOUT_SECONDS = 900
INSTALL_POLL_SECONDS = 0.5

PACKAGE_BY_COMPONENT = {
    'ffmpeg': 'imageio-ffmpeg',
    'faster_whisper': 'faster-whisper',
}


@dataclass(frozen=True)
class InstallCommand:
    executable: str
    args: list[str]
    label: str

    def as_list(self) -> list[str]:
        return [self.executable, *self.args]


def normalize_components(components: list[str] | None) -> list[str]:
    values = components or ['ffmpeg', 'faster_whisper']
    normalized: list[str] = []
    for component in values:
        value = component.strip().lower().replace('-', '_')
        if value in PACKAGE_BY_COMPONENT:
            if value not in normalized:
                normalized.append(value)
            continue
        raise ValueError(f'unsupported_component:{component}')
    return normalized


def missing_components(components: list[str] | None = None) -> list[str]:
    missing: list[str] = []
    for component in normalize_components(components):
        if component == 'ffmpeg' and not bool(ffmpeg_status().get('available')):
            missing.append(component)
        if component == 'faster_whisper' and not check_stt_provider('faster_whisper').available:
            missing.append(component)
    return missing


def install_supported() -> tuple[bool, str | None]:
    if getattr(sys, 'frozen', False):
        return False, '현재 실행 중인 배포 앱은 내부 Python 패키지를 안전하게 수정할 수 없습니다. 최신 앱을 다시 설치해 주세요.'
    if _install_command() is None:
        return False, '자동 설치 도구(uv 또는 pip)를 찾지 못했습니다. 개발 환경을 동기화하거나 앱을 다시 설치해 주세요.'
    return True, None


def _install_command() -> InstallCommand | None:
    uv = shutil.which('uv')
    if uv:
        return InstallCommand(uv, ['pip', 'install', '--python', sys.executable], 'uv pip install')
    if importlib.util.find_spec('pip') is not None:
        return InstallCommand(sys.executable, ['-m', 'pip', 'install'], 'python -m pip install')
    return None


class InstallCancelled(Exception):
    """The caller asked to stop while the installer subprocess was running."""


def _run_install_command(command: list[str], *, timeout: float, should_cancel: Callable[[], bool] | None) -> subprocess.CompletedProcess:
    """Run the installer, polling for cancel and enforcing an overall deadline."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=INSTALL_POLL_SECONDS)
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if should_cancel and should_cancel():
                    raise InstallCancelled()
                if time.monotonic() >= deadline:
                    raise
    except BaseException:
        process.kill()
        process.communicate()
        raise


def install_missing_media_components(
    components: list[str] | None = None,
    on_progress: InstallProgress | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, object]:
    requested = normalize_components(components)
    missing = missing_components(requested)
    supported, reason = install_supported()
    if not missing:
        payload: dict[str, object] = {'ok': True, 'installed_components': [], 'missing_components': [], 'message': '필요한 MP3/TXT 구성요소가 이미 준비되어 있습니다.'}
        if on_progress:
            on_progress({'stage': 'dependencies_already_ready', 'components': requested})
        return payload
    if not supported:
        return {'ok': False, 'error_code': 'dependency_auto_install_unavailable', 'missing_components': missing, 'installed_components': [], 'message': reason}

    command = _install_command()
    if command is None:
        return {'ok': False, 'error_code': 'dependency_auto_install_unavailable', 'missing_components': missing, 'installed_components': [], 'message': '자동 설치 도구를 찾지 못했습니다.'}

    installed: list[str] = []
    for component in missing:
        package = PACKAGE_BY_COMPONENT[component]
        if on_progress:
            on_progress({'stage': 'dependency_install_started', 'component': component, 'package': package, 'installer': command.label})
        try:
            result = _run_install_command([*command.as_list(), package], timeout=INSTALL_TIMEOUT_SECONDS, should_cancel=should_cancel)
        except InstallCancelled:
            return {'ok': False, 'error_code': 'cancelled', 'failed_component': component, 'missing_components': missing_components(requested), 'installed_components': installed, 'message': '설치가 취소되었습니다.'}
        except subprocess.TimeoutExpired:
            return {'ok': False, 'error_code': 'dependency_install_timeout', 'failed_component': component, 'missing_components': missing_components(requested), 'installed_components': installed, 'message': f'{package} 설치가 {INSTALL_TIMEOUT_SECONDS}초 안에 끝나지 않았습니다.'}
        except OSError as exc:
            return {'ok': False, 'error_code': 'dependency_install_failed', 'failed_component': component, 'missing_components': missing_components(requested), 'installed_components': installed, 'message': str(exc)[:1000]}
        if result.returncode != 0:
            stderr_tail = '\n'.join((result.stderr or result.stdout or '').strip().splitlines()[-8:])
            return {
                'ok': False,
                'error_code': 'dependency_install_failed',
                'failed_component': component,
                'missing_components': missing_components(requested),
                'installed_components': installed,
                'message': stderr_tail[:1000] or f'{package} 설치에 실패했습니다.',
            }
        installed.append(component)
        if on_progress:
            on_progress({'stage': 'dependency_install_done', 'component': component, 'package': package})

    still_missing = missing_components(requested)
    ok = len(still_missing) == 0
    return {
        'ok': ok,
        'error_code': None if ok else 'dependency_install_verification_failed',
        'installed_components': installed,
        'missing_components': still_missing,
        'message': '필요한 구성요소 설치가 끝났습니다.' if ok else '설치는 실행됐지만 아직 인식되지 않는 구성요소가 있습니다. 앱/API 서버를 재시작한 뒤 다시 확인해 주세요.',
    }
