from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

from ssu_lecture_downloader.postprocess_media import ffmpeg_status
from ssu_lecture_downloader.stt import check_stt_provider

InstallProgress = Callable[[dict[str, object]], None]

SUPPORTED_COMPONENTS = {'ffmpeg', 'faster_whisper'}
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
        if value == 'faster_whisper' or value == 'ffmpeg':
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


def install_missing_media_components(components: list[str] | None = None, on_progress: InstallProgress | None = None) -> dict[str, object]:
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
        result = subprocess.run([*command.as_list(), package], capture_output=True, text=True, timeout=900)
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
