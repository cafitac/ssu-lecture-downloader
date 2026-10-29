# 음성 추출과 자막

The desktop UI includes two related paths:

- In the main `다운로드` panel, `저장할 파일 선택` lets users multi-select `MP4`, `MP3`, and `TXT` before starting the selected lecture download. `MP4` keeps the source video, `MP3` extracts audio, and `TXT` runs STT after audio extraction. If `MP4` is unchecked, the temporary source video is removed after MP3/TXT artifacts are produced.
- The separate `후처리` card still supports a local already-downloaded video path for manual audio/STT work.

Audio extraction uses system `ffmpeg` when available, otherwise it uses the `imageio-ffmpeg` binary bundled in the Python runtime. For a distributed desktop app this must be included in the app package; end users should not have to install Homebrew or run terminal setup commands. The UI checks `/api/media/dependencies/status` and shows whether ffmpeg is available from the app bundle or the system.

STT uses packaged `faster-whisper` when available. The default is `large-v3` with Korean language forced, beam search, VAD, and a Korean lecture prompt because Korean lecture accuracy is prioritized over speed. This is the local/offline path closest to a Clova-class experience without sending lecture audio to an external API. It writes sibling transcript files:

- `.transcript.txt` for direct AI paste/upload
- `.transcript.md` with metadata and timestamps
- `.transcript.json` with structured segments

If Whisper/faster-whisper is missing, `/api/stt/providers/status`, `/api/media/dependencies/status`, and the UI return an explicit packaged-component reinstall/development-sync hint instead of silently falling back. In non-frozen development/runtime environments the UI also shows `필요한 구성요소 설치`, which calls `/api/media/dependencies/install/jobs` and installs only missing supported components through the backend (`imageio-ffmpeg` for ffmpeg fallback and `faster-whisper` for STT). Distributed frozen sidecars should still bundle target-compatible wheels up front; if the sidecar cannot safely self-modify, the app reports the reinstall path. The first model run can take longer because the Whisper model is downloaded/prepared locally.

## 원격 자막 서버

Where faster-whisper cannot run (Intel Macs) subtitles come from a remote STT server configured in Settings, under "원격 자막 서버". Two kinds are supported: `whisper_cpp_server` (whisper.cpp `whisper-server`, `POST {base}/inference`) and `openai_compatible` (`POST {base}/v1/audio/transcriptions`, optional Bearer key). The settings live in `~/Library/Application Support/com.local.ssu-lecture-downloader/stt.json` (mode 0600, override with `SSU_STT_CONFIG_PATH`); the API key is write-only and never returned or logged. An optional SSH tunnel (`ssh -N -L <local>:127.0.0.1:<remote> <host alias>`) is opened on demand, reused while alive and closed with the backend. When a remote server is enabled it is preferred over the local engine for every transcription.

The audio is converted to 16 kHz mono WAV and sent to the server. Windows where Whisper looped (4 or more identical consecutive cues) are cut out with a 10 s pad, re-transcribed at a higher temperature and spliced back; then filler-only cues are dropped and repeated cues are collapsed. Besides the `.transcript.txt/.md/.json` files above, `<name>.srt` is written next to the video.
