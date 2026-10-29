# SSU Lecture Downloader

숭실대 LMS(canvas.ssu.ac.kr) 강의를 내려받고 자막을 만들어 주는 데스크톱 앱입니다.

## 할 수 있는 것

- 강의 영상(MP4) 다운로드. 여러 개를 골라 한 번에 받을 수 있습니다.
- 영상에서 음성(MP3) 추출
- 음성을 글로 옮긴 자막(TXT) 만들기. 앱이 직접 만드는 방식(Apple 실리콘 빌드)과 따로 띄운 자막 서버에 맡기는 방식이 있습니다.
- 안 본 강의를 순서대로 자동 재생. 앱 안의 작은 창이나 Chrome 창에서 재생합니다.
- 영상, 과제, 퀴즈 마감 알림. 마감 하루 전과 3시간 전에 알려 줍니다.
- 성적 보기(u-SAINT). 처음 쓸 때 동의 화면이 나옵니다.

## 설치

macOS만 지원합니다. Intel Mac용으로 빌드되고, Apple 실리콘 Mac에서는 Rosetta로 실행됩니다. Windows는 준비 중입니다.

앱에 서명이 없어서 처음 열 때 "확인되지 않은 개발자" 경고가 뜹니다. 아래 둘 중 하나로 열면 됩니다.

- 시스템 설정 → 개인정보 보호 및 보안에서 "그래도 열기"를 누릅니다.
- 터미널에서 아래 명령을 한 번 실행합니다.

```bash
xattr -dr com.apple.quarantine "/Applications/SSU Lecture Downloader.app"
```

자동 업데이트는 아직 없습니다. 새 버전은 직접 받아서 바꿔 주세요.

자막은 Intel 빌드에 자막 엔진이 들어 있지 않아서, 설정의 "원격 자막 서버"를 지정해야 만들어집니다. 자세한 내용은 [docs/stt.md](docs/stt.md)에 있습니다.

## 쓰기 전에

- 본인 계정으로만, 개인 학습용으로만 쓰세요. 다른 사람에게 나눠 주면 안 됩니다.
- 학번과 비밀번호는 앱이 켜져 있는 동안 메모리에만 있습니다. 파일이나 키체인에 저장하지 않고, 로그아웃하거나 앱을 끄면 사라집니다.
- 성적은 메모리에만 30분 보관합니다. 디스크에 남는 것은 성적 보기에 동의했다는 기록뿐입니다.
- 마감 알림을 켜면 과목 이름과 마감 시각이 앱 데이터 폴더에 저장됩니다. 앱이 꺼져 있어도 알림을 보내려는 것입니다.

## 개발

Tauri(Rust) 셸 + React/Vite 화면 + Python FastAPI 백엔드로 되어 있습니다. 백엔드는 배포본에서 PyInstaller 사이드카로 앱에 들어갑니다.

```bash
uv sync
cd frontend
npm install
npm run tauri:dev
```

`tauri:dev`가 백엔드도 같이 띄웁니다. 테스트는 루트에서 `uv run pytest -q`, `frontend`에서 `npm run test:contract`, `frontend/src-tauri`에서 `cargo test`입니다.

자세한 내용은 아래 문서에 나눠 두었습니다.

- [개발 가이드](docs/development.md): CLI 명령, 실행, 빌드, 검증, CI
- [구조](docs/architecture.md): 서비스 계층, 다운로드 엔진 경계
- [로컬 API](docs/api.md): 접근 제한과 엔드포인트
- [음성 추출과 자막](docs/stt.md): ffmpeg, Whisper, 원격 자막 서버
- [프론트엔드](docs/frontend.md): 화면 구성, 시청 모드, 백엔드 감독
- [공식 플레이어 QA 경계](docs/qa.md)
