//! App-owned LMS windows.
//!
//! Entry points sharing the session-bridge window builder:
//! - `open_official_viewer_with_session`: manual row-level `시청`. Must stay
//!   minimal (session bridge only, no scripts, no navigation hooks); see the
//!   regression tests at the bottom and `frontend/README.md`.
//! - `open_lms_page_with_session`: a normal window for an LMS page (wiki page, board,
//!   assignment, quiz). Session bridge only, no scripts.
//!
//! The sequential-watch window (small, always on top, one window for the whole queue) is
//! owned by `watch_runtime.rs`; it reuses `with_watch_assist` and the bridge helpers below.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::Manager;

const VIEWER_LABEL_PREFIX: &str = "official-viewer";
const VIEWER_DEFAULT_TITLE: &str = "숭실 LMS 공식 시청 창";
const PAGE_LABEL_PREFIX: &str = "lms-page";
const PAGE_DEFAULT_TITLE: &str = "숭실 LMS";
pub(crate) const VIEWER_USER_AGENT: &str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36";

/// Tracks the currently open viewer. Every open uses a fresh label so building
/// the new window never races the asynchronous close of the previous one.
pub(crate) struct ViewerState {
    current: Mutex<Option<String>>,
    next_id: AtomicU64,
    next_page_id: AtomicU64,
}

impl ViewerState {
    pub(crate) fn new() -> Self {
        Self {
            current: Mutex::new(None),
            next_id: AtomicU64::new(1),
            next_page_id: AtomicU64::new(1),
        }
    }

    fn next_label(&self) -> String {
        viewer_label(self.next_id.fetch_add(1, Ordering::Relaxed))
    }

    /// LMS page windows are independent of the viewer: any number may be open.
    fn next_page_label(&self) -> String {
        page_label(self.next_page_id.fetch_add(1, Ordering::Relaxed))
    }

    fn take_current(&self) -> Option<String> {
        self.current.lock().ok().and_then(|mut guard| guard.take())
    }

    fn set_current(&self, label: String) {
        if let Ok(mut guard) = self.current.lock() {
            *guard = Some(label);
        }
    }
}

fn viewer_label(id: u64) -> String {
    format!("{VIEWER_LABEL_PREFIX}-{id}")
}

fn page_label(id: u64) -> String {
    format!("{PAGE_LABEL_PREFIX}-{id}")
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct WatchDebugEvent {
    stage: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    frame: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    host: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    ready_state: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    videos: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    video_state: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    buttons: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    selector: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub(crate) struct OfficialViewerBridgeCookie {
    name: String,
    value: String,
    domain: String,
    path: String,
    secure: bool,
    http_only: bool,
    same_site: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub(crate) struct OfficialViewerSessionBridgeRequest {
    pub(crate) title: Option<String>,
    pub(crate) viewer_url: Option<String>,
    pub(crate) launch_url: Option<String>,
    pub(crate) cookies: Vec<OfficialViewerBridgeCookie>,
}

/// A normal LMS page window: the page address plus the Python-authenticated cookies.
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct LmsPageRequest {
    title: Option<String>,
    url: String,
    cookies: Vec<OfficialViewerBridgeCookie>,
}

pub(crate) fn official_viewer_bootstrap_url() -> tauri::Url {
    tauri::Url::parse("https://commons.ssu.ac.kr/")
        .expect("static official viewer bootstrap URL must parse")
}

fn validate_official_watch_url(url: &str) -> Result<tauri::Url, String> {
    let parsed = tauri::Url::parse(url).map_err(|_| "invalid_watch_url".to_string())?;
    if parsed.scheme() != "https" {
        return Err("unsupported_watch_url_scheme".to_string());
    }
    let host = parsed.host_str().unwrap_or_default();
    if host == "commons.ssu.ac.kr"
        || host.ends_with(".commons.ssu.ac.kr")
        || host == "ssu-toast.commonscdn.com"
        || host == "canvas.ssu.ac.kr"
    {
        return Ok(parsed);
    }
    Err("unsupported_watch_url_host".to_string())
}

/// Only https pages on the Soongsil domain (`ssu.ac.kr` and its subdomains) may open in an
/// LMS page window; credentials in the address and other ports are refused too.
fn validate_lms_page_url(url: &str) -> Result<tauri::Url, String> {
    let parsed = tauri::Url::parse(url.trim()).map_err(|_| "invalid_lms_page_url".to_string())?;
    if parsed.scheme() != "https" {
        return Err("unsupported_lms_page_scheme".to_string());
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err("unsupported_lms_page_credentials".to_string());
    }
    if parsed.port().is_some() {
        return Err("unsupported_lms_page_port".to_string());
    }
    let host = parsed.host_str().unwrap_or_default().to_ascii_lowercase();
    if host == "ssu.ac.kr" || host.ends_with(".ssu.ac.kr") {
        return Ok(parsed);
    }
    Err("unsupported_lms_page_host".to_string())
}

pub(crate) fn watch_debug_event_from_url(url: &tauri::Url) -> WatchDebugEvent {
    let mut values: HashMap<String, String> = HashMap::new();
    for (key, value) in url.query_pairs() {
        values.insert(key.into_owned(), value.into_owned());
    }
    WatchDebugEvent {
        stage: values
            .get("stage")
            .cloned()
            .unwrap_or_else(|| "unknown".to_string()),
        mode: values.get("mode").cloned(),
        frame: values.get("frame").cloned(),
        host: values.get("host").cloned(),
        ready_state: values.get("ready").cloned(),
        videos: values.get("videos").cloned(),
        video_state: values.get("video").cloned(),
        buttons: values.get("buttons").cloned(),
        selector: values.get("selector").cloned(),
        error_name: values.get("error").cloned(),
        error_message: values.get("message").cloned(),
    }
}

fn bridge_cookie_to_tauri(
    cookie: &OfficialViewerBridgeCookie,
) -> Result<tauri::webview::Cookie<'static>, String> {
    if cookie.name.trim().is_empty() || cookie.domain.trim().is_empty() {
        return Err("invalid_bridge_cookie".to_string());
    }
    let mut builder = tauri::webview::Cookie::build((cookie.name.clone(), cookie.value.clone()))
        .domain(cookie.domain.trim_start_matches('.').to_string())
        .path(if cookie.path.is_empty() {
            "/".to_string()
        } else {
            cookie.path.clone()
        })
        .secure(cookie.secure)
        .http_only(cookie.http_only);
    if let Some(same_site) = &cookie.same_site {
        let parsed_same_site = match same_site.to_ascii_lowercase().as_str() {
            "lax" => Some(tauri::webview::cookie::SameSite::Lax),
            "strict" => Some(tauri::webview::cookie::SameSite::Strict),
            "none" => Some(tauri::webview::cookie::SameSite::None),
            _ => None,
        };
        if let Some(parsed_same_site) = parsed_same_site {
            builder = builder.same_site(parsed_same_site);
        }
    }
    Ok(builder.build())
}

/// The sequential-watch helper: tries visible `video.play()` and signals
/// `ssu-watch://ended` when the main video really ends.
pub(crate) fn official_watch_assist_script() -> &'static str {
    include_str!("assets/watch_assist.js")
}

/// Close the previously opened viewer (if any) and reserve a fresh label.
fn begin_viewer(app: &tauri::AppHandle, state: &ViewerState) -> Result<String, String> {
    if let Some(previous) = state.take_current() {
        if let Some(existing) = app.get_webview_window(&previous) {
            existing.close().map_err(|error| error.to_string())?;
        }
    }
    Ok(state.next_label())
}

/// Window geometry and title shared by every viewer flavour.
fn base_viewer_builder<'a>(
    app: &'a tauri::AppHandle,
    label: &str,
    url: tauri::WebviewUrl,
    title: Option<String>,
) -> tauri::WebviewWindowBuilder<'a, tauri::Wry, tauri::AppHandle> {
    tauri::WebviewWindowBuilder::new(app, label, url)
        .title(title.unwrap_or_else(|| VIEWER_DEFAULT_TITLE.to_string()))
        .inner_size(1180.0, 760.0)
        .min_inner_size(900.0, 620.0)
        .center()
        .focused(true)
        .resizable(true)
        .visible(true)
        .decorations(true)
}

/// Builder for the session-bridge flows: starts at the safe bootstrap URL and
/// presents a desktop user agent; cookies are injected after the window exists.
fn session_viewer_builder<'a>(
    app: &'a tauri::AppHandle,
    label: &str,
    title: Option<String>,
) -> tauri::WebviewWindowBuilder<'a, tauri::Wry, tauri::AppHandle> {
    base_viewer_builder(
        app,
        label,
        tauri::WebviewUrl::External(official_viewer_bootstrap_url()),
        title,
    )
    .user_agent(VIEWER_USER_AGENT)
}

/// Sequential-watch only: install the playback helper and route its `ssu-watch://` signals
/// (debug, progress, heartbeat, ended, stalled) to `on_signal`. Such navigations never load.
pub(crate) fn with_watch_assist<'a>(
    builder: tauri::WebviewWindowBuilder<'a, tauri::Wry, tauri::AppHandle>,
    on_signal: impl Fn(&tauri::Url) + Send + 'static,
) -> tauri::WebviewWindowBuilder<'a, tauri::Wry, tauri::AppHandle> {
    builder
        .initialization_script_for_all_frames(official_watch_assist_script())
        .on_navigation(move |nav_url| {
            if nav_url.scheme() == "ssu-watch" {
                on_signal(nav_url);
                return false;
            }
            true
        })
}

/// Inject the Python-authenticated LMS cookies, then go to the validated target.
pub(crate) fn bridge_session_and_navigate(
    window: &tauri::WebviewWindow,
    cookies: &[OfficialViewerBridgeCookie],
    target: tauri::Url,
) -> Result<(), String> {
    for cookie in cookies {
        window
            .set_cookie(bridge_cookie_to_tauri(cookie)?)
            .map_err(|error| error.to_string())?;
    }
    window.navigate(target).map_err(|error| error.to_string())
}

pub(crate) fn session_bridge_target(
    request: &OfficialViewerSessionBridgeRequest,
) -> Result<tauri::Url, String> {
    let fallback_viewer_url = request
        .viewer_url
        .as_deref()
        .ok_or_else(|| "missing_watch_url".to_string())?;
    let navigation_url = request.launch_url.as_deref().unwrap_or(fallback_viewer_url);
    validate_official_watch_url(navigation_url)
}

#[tauri::command]
pub(crate) fn open_official_viewer_with_session(
    app: tauri::AppHandle,
    state: tauri::State<'_, ViewerState>,
    request: OfficialViewerSessionBridgeRequest,
) -> Result<(), String> {
    let parsed = session_bridge_target(&request)?;
    let label = begin_viewer(&app, &state)?;
    let window = session_viewer_builder(&app, &label, request.title.clone())
        .build()
        .map_err(|error| error.to_string())?;
    state.set_current(label);
    bridge_session_and_navigate(&window, &request.cookies, parsed)
}

#[tauri::command]
pub(crate) fn open_lms_page_with_session(
    app: tauri::AppHandle,
    state: tauri::State<'_, ViewerState>,
    request: LmsPageRequest,
) -> Result<(), String> {
    let target = validate_lms_page_url(&request.url)?;
    let label = state.next_page_label();
    let title = request
        .title
        .as_deref()
        .map(str::trim)
        .filter(|title| !title.is_empty())
        .unwrap_or(PAGE_DEFAULT_TITLE)
        .to_string();
    let window = session_viewer_builder(&app, &label, Some(title))
        .build()
        .map_err(|error| error.to_string())?;
    bridge_session_and_navigate(&window, &request.cookies, target)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Production code of every Rust module (test sections stripped).
    fn production_sources() -> Vec<(&'static str, &'static str)> {
        [
            ("lib.rs", include_str!("lib.rs")),
            ("backend.rs", include_str!("backend.rs")),
            ("native_download.rs", include_str!("native_download.rs")),
            ("viewer.rs", include_str!("viewer.rs")),
            ("watch_queue.rs", include_str!("watch_queue.rs")),
            ("watch_runtime.rs", include_str!("watch_runtime.rs")),
        ]
        .into_iter()
        .map(|(name, source)| {
            (
                name,
                source
                    .split("#[cfg(test)]")
                    .next()
                    .expect("source has production section"),
            )
        })
        .collect()
    }

    #[test]
    fn source_no_longer_installs_macos_webview_configuration_hook() {
        for (name, production_source) in production_sources() {
            assert!(
                !production_source.contains("with_webview_configuration"),
                "{name}"
            );
            assert!(
                !production_source.contains("WKWebViewConfiguration"),
                "{name}"
            );
            assert!(!production_source.contains("objc2_web_kit"), "{name}");
        }
    }

    #[test]
    fn watch_viewer_script_plays_real_video_and_signals_only_on_main_video_ended() {
        let script = official_watch_assist_script();

        assert!(script.contains("querySelectorAll('video')"), "{script}");
        assert!(script.contains("video.play()"), "{script}");
        assert!(
            script.contains("video.addEventListener('ended'"),
            "{script}"
        );
        assert!(script.contains("SHORT_INTRO_MAX_SECONDS = 10"), "{script}");
        assert!(script.contains("shouldEmitEnded = true"), "{script}");
        assert!(script.contains("isLikelyMainVideo(video)"), "{script}");
        assert!(script.contains("short-video-ended"), "{script}");
        assert!(script.contains("ssu-watch://ended"), "{script}");
        // Sequential mode reports how far the video is and when it stalls.
        assert!(script.contains("ssu-watch://progress"), "{script}");
        assert!(script.contains("ssu-watch://stalled"), "{script}");
        assert!(script.contains("STALL_AFTER_MS = 60000"), "{script}");
    }

    #[test]
    fn watch_viewer_script_mutes_and_answers_the_resume_dialog_with_yes() {
        let script = official_watch_assist_script();

        assert!(script.contains("video.muted = true"), "{script}");
        assert!(script.contains("이어서 보시겠습니까"), "{script}");
        assert!(script.contains("RESUME_YES_LABEL = '예'"), "{script}");
        assert!(
            script.contains("controlLabel(control) !== RESUME_YES_LABEL"),
            "exact button text match: {script}"
        );
        assert!(script.contains("PLAY_RETRY_MAX_MS"), "{script}");
    }

    #[test]
    fn watch_viewer_script_never_fakes_visibility_speed_or_position() {
        let script = official_watch_assist_script();

        for forbidden in [
            "visibilityState",
            "document.hidden",
            "visibilitychange",
            "playbackRate",
            "defaultPlaybackRate",
            "Object.defineProperty",
            "stopImmediatePropagation",
            "stopPropagation",
            "preventDefault",
            "currentTime =",
            "currentTime=",
            "fastSeek",
            "'blur'",
            "\"blur\"",
        ] {
            assert!(!script.contains(forbidden), "{forbidden}");
        }
    }

    #[test]
    fn manual_official_viewer_stays_minimal_without_assist_or_debug_hooks() {
        let source = include_str!("viewer.rs");
        let start = source
            .find("pub(crate) fn open_official_viewer_with_session(")
            .expect("manual viewer function exists");
        let tail = &source[start..];
        let end = tail
            .find("\n}\n")
            .expect("manual viewer function boundary exists");
        let body = &tail[..end];

        assert!(body.contains("session_bridge_target(&request)"), "{body}");
        assert!(body.contains("bridge_session_and_navigate("), "{body}");
        assert!(!body.contains("initialization_script"), "{body}");
        assert!(!body.contains("on_navigation"), "{body}");
        assert!(!body.contains("with_watch_assist"), "{body}");
        assert!(!body.contains("official_watch_assist_script"), "{body}");
        assert!(
            !body.contains("official_viewer_autoplay_configuration"),
            "{body}"
        );
        assert!(!body.contains("ssu-watch://debug"), "{body}");

        // The shared builders it uses must not carry assist hooks either.
        for builder in ["fn base_viewer_builder<", "fn session_viewer_builder<"] {
            let start = source.find(builder).expect("builder exists");
            let tail = &source[start..];
            let end = tail.find("\n}\n").expect("builder boundary exists");
            let builder_body = &tail[..end];
            assert!(
                !builder_body.contains("initialization_script"),
                "{builder_body}"
            );
            assert!(!builder_body.contains("on_navigation"), "{builder_body}");
        }
    }

    #[test]
    fn each_open_gets_a_unique_label_so_close_never_races_the_next_build() {
        let state = ViewerState::new();
        let first = state.next_label();
        let second = state.next_label();
        assert_ne!(first, second);
        assert!(first.starts_with("official-viewer-"));
        assert_eq!(viewer_label(7), "official-viewer-7");

        state.set_current(first.clone());
        assert_eq!(state.take_current(), Some(first));
        assert_eq!(state.take_current(), None, "current is consumed on close");
    }

    #[test]
    fn lms_pages_open_only_for_https_ssu_hosts() {
        for ok in [
            "https://canvas.ssu.ac.kr/courses/1/pages/week-1",
            "https://canvas.ssu.ac.kr/courses/1/modules/items/9",
            "https://lms.ssu.ac.kr/x?y=1#z",
            "https://ssu.ac.kr/",
        ] {
            assert!(validate_lms_page_url(ok).is_ok(), "{ok}");
        }
        for rejected in [
            "http://canvas.ssu.ac.kr/x",
            "https://example.com/ssu.ac.kr",
            "https://ssu.ac.kr.evil.com/",
            "https://evilssu.ac.kr/",
            "https://canvas.ssu.ac.kr@evil.com/",
            "https://user:pw@canvas.ssu.ac.kr/",
            "https://canvas.ssu.ac.kr:8443/",
            "javascript:alert(1)",
            "ssu-watch://debug",
            "/courses/1/pages/x",
            "",
        ] {
            assert!(validate_lms_page_url(rejected).is_err(), "{rejected}");
        }
    }

    #[test]
    fn lms_page_windows_get_their_own_unique_labels() {
        let state = ViewerState::new();
        let first = state.next_page_label();
        let second = state.next_page_label();
        assert_ne!(first, second);
        assert_eq!(page_label(3), "lms-page-3");
        assert_ne!(
            state.next_label(),
            first,
            "viewer and page labels never collide"
        );

        let source = include_str!("viewer.rs");
        let start = source
            .find("pub(crate) fn open_lms_page_with_session(")
            .expect("page command exists");
        let tail = &source[start..];
        let body = &tail[..tail.find("\n}\n").expect("page command boundary")];
        assert!(body.contains("validate_lms_page_url("), "{body}");
        assert!(body.contains("bridge_session_and_navigate("), "{body}");
        assert!(
            !body.contains("with_watch_assist"),
            "no watch script: {body}"
        );
        assert!(!body.contains("initialization_script"), "{body}");
        assert!(!body.contains("on_navigation"), "{body}");
    }

    #[test]
    fn official_watch_url_validation_rejects_non_https_and_unrelated_hosts() {
        assert!(validate_official_watch_url("https://commons.ssu.ac.kr/viewer").is_ok());
        assert!(
            validate_official_watch_url("https://ssu-toast.commonscdn.com/contents/media.mp4")
                .is_ok()
        );
        assert!(validate_official_watch_url("http://commons.ssu.ac.kr/viewer").is_err());
        assert!(validate_official_watch_url("https://example.com/viewer").is_err());
    }
}
