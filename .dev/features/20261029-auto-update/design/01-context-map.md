# Context map: update authorities

Status legend: CURRENT = exists today; PROPOSED = this design.

```mermaid
flowchart LR
    subgraph USER["사용자(소유자) 권한"]
        U["사용자님<br/>릴리스 게시(태그 / workflow_dispatch) · 업데이트 클릭"]
        KEYBK["서명 개인키 백업(PROPOSED)<br/>비밀번호 관리자 / 오프라인"]
    end
    subgraph SRC["공개 소스 저장소 cafitac/ssu-lecture-downloader (CURRENT, 2026-10-29 공개)"]
        CODE["소스 코드 · develop<br/>(비밀값 커밋 금지)"]
        WF["release.yml (PROPOSED)<br/>GITHUB_TOKEN, 릴리스 작업만 contents: write"]
        SEC["Secrets(PROPOSED)<br/>TAURI_SIGNING_PRIVATE_KEY(+_PASSWORD)"]
        GHR["GitHub Releases (PROPOSED)<br/>.app.tar.gz · .sig · .dmg · latest.json"]
    end
    subgraph APP["설치된 앱 (CURRENT 앱 + PROPOSED 업데이터)"]
        UI["업데이트 알림 UI"]
        GUARD["설치 가드 + 사이드카 정지 (Rust)"]
        PLUG["updater 플러그인<br/>공개키로 서명 검증"]
    end
    U -->|"태그 push / 수동 실행"| WF
    KEYBK -.->|"백업 확인 후 Secret 등록"| SEC
    SEC --> WF
    WF -->|"draft 업로드 → 모든 플랫폼 확인 후 게시"| GHR
    UI -->|"익명 HTTPS GET latest.json"| GHR
    UI --> GUARD --> PLUG
    PLUG -->|"서명 검증 후 교체 · 재시작"| APP
```

| Authority | Owner | Who may write | Notes |
|---|---|---|---|
| Source repo (public) | user | Claude via PRs to develop (standing grant `merge-ssu-develop`) | merge never publishes; nothing secret committed |
| Release publication | user | `release.yml` `finalize` job only, started by the user's tag/dispatch | drafts invisible to clients; `GITHUB_TOKEN` with `contents: write` on release jobs only |
| Signing private key | user | nobody after generation; read by CI from Secrets | never in repo, app, logs, or `.dev` |
| Public key | repo | AU-PR2 commits it in `tauri.conf.json` | changing it orphans installed apps |
| Install on a machine | that machine's user | updater plugin after the user's click and the guard | no silent install |
