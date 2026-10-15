//! Python API backend supervisor.
//!
//! The supervisor never blocks the UI thread: `start_in_background` runs the
//! whole startup on a worker thread and publishes `starting` / `ready` /
//! `failed` through [`BackendManager::status`]. A managed child is spawned in
//! its own process group so the entire tree (uv / PyInstaller bootloader and
//! the Python grandchildren) can be terminated together.

use serde::Serialize;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream, ToSocketAddrs};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub(crate) const BACKEND_HOST: &str = "127.0.0.1";
pub(crate) const DEFAULT_BACKEND_PORT: u16 = 8000;
const BACKEND_HEALTH_PATH: &str = "/api/health";
/// Env var that hands the per-launch API token to a managed backend.
pub(crate) const BACKEND_TOKEN_ENV: &str = "SSU_API_TOKEN";
/// Header the UI and native callers send so the backend accepts the request.
pub(crate) const BACKEND_TOKEN_HEADER: &str = "X-SSU-Token";
const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(90);
const BACKEND_POLL_INTERVAL: Duration = Duration::from_millis(300);
const BACKEND_TERMINATE_GRACE: Duration = Duration::from_secs(3);
const MAX_SPAWN_ATTEMPTS: u32 = 3;
const BACKEND_LOG_FILE: &str = "backend.log";
const BACKEND_PID_FILE: &str = "backend.pid";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum BackendState {
    Starting,
    Ready,
    Failed,
}

/// Snapshot returned by the `backend_supervision_status` command.
#[derive(Debug, Clone, Serialize)]
pub(crate) struct BackendStatus {
    pub(crate) state: BackendState,
    /// `external`, `managed`, `managed-sidecar`, `disabled`, `starting`, `failed`, ...
    pub(crate) mode: String,
    pub(crate) error: Option<String>,
    pub(crate) log_path: Option<String>,
}

/// Where and how the React app should reach the API.
#[derive(Debug, Clone, Serialize)]
pub(crate) struct BackendConnection {
    pub(crate) base_url: String,
    pub(crate) token: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct BackendLaunchCommand {
    pub(crate) program: String,
    pub(crate) args: Vec<String>,
    pub(crate) cwd: PathBuf,
    pub(crate) packaged_sidecar: bool,
}

struct Inner {
    state: BackendState,
    mode: String,
    port: u16,
    token: Option<String>,
    error: Option<String>,
    log_path: Option<PathBuf>,
    child: Option<Child>,
    worker_active: bool,
    shutting_down: bool,
}

type Launcher = Box<dyn Fn(u16) -> BackendLaunchCommand + Send + Sync>;

pub(crate) struct BackendManager {
    inner: Mutex<Inner>,
    launcher: Launcher,
    log_dir: PathBuf,
    state_dir: Option<PathBuf>,
    /// Port probed for an already-running external backend.
    external_probe_port: u16,
}

enum AttemptOutcome {
    Ready,
    /// The chosen port was taken between probing and binding; try another one.
    PortConflict(String),
    Failed(String),
    Aborted,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum WaitOutcome {
    Ready,
    Exited(String),
    TimedOut,
    Aborted,
}

impl BackendManager {
    pub(crate) fn new(
        project_root: PathBuf,
        resource_dir: Option<PathBuf>,
        log_dir: PathBuf,
        state_dir: Option<PathBuf>,
    ) -> Self {
        Self::with_launcher(
            Box::new(move |port| {
                backend_launch_command(&project_root, resource_dir.as_deref(), port)
            }),
            log_dir,
            state_dir,
        )
    }

    fn with_launcher(launcher: Launcher, log_dir: PathBuf, state_dir: Option<PathBuf>) -> Self {
        Self {
            inner: Mutex::new(Inner {
                state: BackendState::Starting,
                mode: "uninitialized".to_string(),
                port: DEFAULT_BACKEND_PORT,
                token: None,
                error: None,
                log_path: None,
                child: None,
                worker_active: false,
                shutting_down: false,
            }),
            launcher,
            log_dir,
            state_dir,
            external_probe_port: DEFAULT_BACKEND_PORT,
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        // A poisoned lock only means a worker panicked; the data is still usable.
        self.inner
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    pub(crate) fn status(&self) -> BackendStatus {
        let inner = self.lock();
        BackendStatus {
            state: inner.state,
            mode: inner.mode.clone(),
            error: inner.error.clone(),
            log_path: inner
                .log_path
                .as_ref()
                .map(|path| path.to_string_lossy().to_string()),
        }
    }

    pub(crate) fn connection(&self) -> BackendConnection {
        let inner = self.lock();
        BackendConnection {
            base_url: format!("http://{BACKEND_HOST}:{}", inner.port),
            token: inner.token.clone(),
        }
    }

    fn set_ready(&self, mode: &str, port: u16, token: Option<String>) {
        let mut inner = self.lock();
        inner.state = BackendState::Ready;
        inner.mode = mode.to_string();
        inner.port = port;
        inner.token = token;
        inner.error = None;
    }

    fn set_failed(&self, message: String) {
        let mut inner = self.lock();
        inner.state = BackendState::Failed;
        inner.mode = "failed".to_string();
        inner.token = None;
        inner.error = Some(message);
    }

    fn is_shutting_down(&self) -> bool {
        self.lock().shutting_down
    }

    /// Atomically claim the right to start. Flips the published state to
    /// `starting` before returning so a UI that polls right after a retry
    /// never observes the previous `failed` state.
    fn begin_start(&self) -> bool {
        let mut inner = self.lock();
        if inner.worker_active || inner.shutting_down {
            return false;
        }
        inner.worker_active = true;
        inner.state = BackendState::Starting;
        inner.mode = "starting".to_string();
        inner.error = None;
        inner.token = None;
        true
    }

    fn spawn_worker(self: &Arc<Self>, terminate_previous: bool) {
        let manager = Arc::clone(self);
        let spawned = std::thread::Builder::new()
            .name("ssu-backend-supervisor".to_string())
            .spawn(move || {
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    if terminate_previous {
                        manager.terminate_child();
                    }
                    manager.run_startup();
                }));
                if result.is_err() {
                    manager.terminate_child();
                    manager.set_failed("백엔드 시작 중 내부 오류가 발생했습니다.".to_string());
                }
                manager.lock().worker_active = false;
            });
        if let Err(error) = spawned {
            self.set_failed(format!("백엔드 시작 스레드를 만들 수 없습니다: {error}"));
            self.lock().worker_active = false;
        }
    }

    /// Start the backend on a worker thread. Returns `false` when a startup is
    /// already running or the app is shutting down.
    pub(crate) fn start_in_background(self: &Arc<Self>) -> bool {
        if !self.begin_start() {
            return false;
        }
        self.spawn_worker(false);
        true
    }

    /// Kill any previous managed child and start again, without blocking the
    /// caller. No-op (`false`) while a startup is already running.
    pub(crate) fn restart(self: &Arc<Self>) -> bool {
        if !self.begin_start() {
            return false;
        }
        self.spawn_worker(true);
        true
    }

    fn run_startup(&self) {
        if let Some(state_dir) = &self.state_dir {
            reap_stale_backend(&state_dir.join(BACKEND_PID_FILE));
        }
        if backend_health_check(self.external_probe_port, None, Duration::from_millis(250)) {
            self.set_ready("external", self.external_probe_port, None);
            return;
        }
        if std::env::var("SSU_BACKEND_AUTOSTART").ok().as_deref() == Some("0") {
            self.set_ready("disabled", DEFAULT_BACKEND_PORT, None);
            return;
        }
        let mut last_error = String::new();
        for attempt in 1..=MAX_SPAWN_ATTEMPTS {
            if self.is_shutting_down() {
                return;
            }
            match self.launch_attempt() {
                AttemptOutcome::Ready | AttemptOutcome::Aborted => return,
                AttemptOutcome::PortConflict(message) => {
                    last_error = message;
                    if attempt == MAX_SPAWN_ATTEMPTS {
                        break;
                    }
                }
                AttemptOutcome::Failed(message) => {
                    self.set_failed(message);
                    return;
                }
            }
        }
        self.set_failed(last_error);
    }

    fn launch_attempt(&self) -> AttemptOutcome {
        let port = match choose_backend_port() {
            Ok(port) => port,
            Err(error) => {
                return AttemptOutcome::Failed(format!(
                    "Python API 서버용 빈 포트를 찾을 수 없습니다: {error}"
                ))
            }
        };
        let token = match generate_api_token() {
            Ok(token) => token,
            Err(error) => {
                return AttemptOutcome::Failed(format!("API 토큰을 만들 수 없습니다: {error}"))
            }
        };
        let launch = (self.launcher)(port);
        let log_path = self.log_dir.join(BACKEND_LOG_FILE);
        let (stdout, stderr) = match open_backend_log(&log_path) {
            Ok(pair) => pair,
            Err(error) => {
                return AttemptOutcome::Failed(format!(
                    "백엔드 로그 파일을 열 수 없습니다 ({}): {error}",
                    log_path.display()
                ))
            }
        };

        let mut command = Command::new(&launch.program);
        command
            .args(&launch.args)
            .current_dir(&launch.cwd)
            .env(BACKEND_TOKEN_ENV, &token)
            .stdin(Stdio::null())
            .stdout(stdout)
            .stderr(stderr)
            // Own process group so the whole tree can be signalled at once.
            .process_group(0);
        let child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                let mut inner = self.lock();
                inner.log_path = Some(log_path);
                drop(inner);
                return AttemptOutcome::Failed(format!(
                    "Python API 서버를 시작할 수 없습니다 ({}): {error}",
                    launch.program
                ));
            }
        };
        if let Some(state_dir) = &self.state_dir {
            write_pidfile(&state_dir.join(BACKEND_PID_FILE), child.id());
        }
        {
            let mut inner = self.lock();
            inner.child = Some(child);
            inner.port = port;
            inner.log_path = Some(log_path.clone());
            inner.mode = if launch.packaged_sidecar {
                "managed-sidecar-starting".to_string()
            } else {
                "managed-starting".to_string()
            };
        }

        let outcome = wait_for_backend_ready(
            port,
            Some(&token),
            BACKEND_STARTUP_TIMEOUT,
            BACKEND_POLL_INTERVAL,
            || self.child_exit_description(),
            || self.is_shutting_down(),
        );
        match outcome {
            WaitOutcome::Ready => {
                self.set_ready(
                    if launch.packaged_sidecar {
                        "managed-sidecar"
                    } else {
                        "managed"
                    },
                    port,
                    Some(token),
                );
                AttemptOutcome::Ready
            }
            WaitOutcome::Aborted => {
                self.terminate_child();
                AttemptOutcome::Aborted
            }
            WaitOutcome::Exited(description) => {
                self.terminate_child();
                if log_indicates_port_in_use(&log_path) {
                    AttemptOutcome::PortConflict(format!(
                        "Python API 서버 포트 {port}를 사용할 수 없어 시작하지 못했습니다. ({description})"
                    ))
                } else {
                    AttemptOutcome::Failed(format!(
                        "Python API 서버가 시작 중 종료되었습니다. ({description})"
                    ))
                }
            }
            WaitOutcome::TimedOut => {
                self.terminate_child();
                AttemptOutcome::Failed(format!(
                    "Python API 서버가 {}초 안에 준비되지 않았습니다.",
                    BACKEND_STARTUP_TIMEOUT.as_secs()
                ))
            }
        }
    }

    /// `Some(description)` once the managed child is gone (or was never spawned).
    fn child_exit_description(&self) -> Option<String> {
        let mut inner = self.lock();
        match inner.child.as_mut() {
            None => Some("프로세스가 없습니다".to_string()),
            Some(child) => match child.try_wait() {
                Ok(Some(status)) => Some(format!("종료 상태: {status}")),
                Ok(None) => None,
                Err(error) => Some(format!("상태 확인 실패: {error}")),
            },
        }
    }

    /// Terminate the managed child's whole process group (SIGTERM, then SIGKILL).
    fn terminate_child(&self) {
        let child = self.lock().child.take();
        if let Some(mut child) = child {
            terminate_process_group(&mut child, BACKEND_TERMINATE_GRACE);
        }
        if let Some(state_dir) = &self.state_dir {
            let _ = fs::remove_file(state_dir.join(BACKEND_PID_FILE));
        }
    }

    /// App exit: stop supervising and kill the owned process tree. Idempotent.
    pub(crate) fn shutdown(&self) {
        self.lock().shutting_down = true;
        self.terminate_child();
    }
}

impl Drop for BackendManager {
    fn drop(&mut self) {
        self.shutdown();
    }
}

// ---------------------------------------------------------------------------
// Process-group helpers
// ---------------------------------------------------------------------------

fn process_group_alive(pgid: libc::pid_t) -> bool {
    // SAFETY: signal 0 only checks for existence/permission.
    unsafe { libc::killpg(pgid, 0) == 0 }
}

/// SIGTERM the whole process group of `child` (which must have been spawned
/// with `process_group(0)`), wait up to `grace`, then SIGKILL what is left and
/// reap the direct child.
pub(crate) fn terminate_process_group(child: &mut Child, grace: Duration) {
    let pgid = child.id() as libc::pid_t;
    // SAFETY: plain signal delivery to a process group we created.
    unsafe {
        libc::killpg(pgid, libc::SIGTERM);
    }
    let deadline = Instant::now() + grace;
    loop {
        let child_gone = !matches!(child.try_wait(), Ok(None));
        if child_gone && !process_group_alive(pgid) {
            return;
        }
        if Instant::now() >= deadline {
            break;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    if process_group_alive(pgid) {
        // SAFETY: as above.
        unsafe {
            libc::killpg(pgid, libc::SIGKILL);
        }
    }
    let _ = child.wait();
    // Give the kernel a moment to tear the group down so callers can rely on it.
    let kill_deadline = Instant::now() + Duration::from_millis(500);
    while process_group_alive(pgid) && Instant::now() < kill_deadline {
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn write_pidfile(path: &Path, pid: u32) {
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = fs::write(path, pid.to_string());
}

/// Whether a `ps` command line belongs to a backend this app would have launched.
pub(crate) fn command_looks_like_our_backend(command_line: &str) -> bool {
    command_line.contains("ssu_lecture_downloader.api:app") || command_line.contains("ssu-api")
}

fn process_command_line(pid: libc::pid_t) -> Option<String> {
    let output = Command::new("ps")
        .args(["-o", "command=", "-p", &pid.to_string()])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!text.is_empty()).then_some(text)
}

/// Kill a backend left behind by a previous app run that was force-quit.
/// Only acts when the pidfile's process is a group leader whose command line
/// matches our backend, so a recycled pid is never touched.
fn reap_stale_backend(pidfile: &Path) {
    let Some(pid) = fs::read_to_string(pidfile)
        .ok()
        .and_then(|text| text.trim().parse::<libc::pid_t>().ok())
    else {
        let _ = fs::remove_file(pidfile);
        return;
    };
    let _ = fs::remove_file(pidfile);
    if pid <= 1 {
        return;
    }
    // SAFETY: read-only queries about another process.
    let (alive, group_leader) = unsafe { (libc::kill(pid, 0) == 0, libc::getpgid(pid) == pid) };
    if !alive || !group_leader {
        return;
    }
    let Some(command_line) = process_command_line(pid) else {
        return;
    };
    if !command_looks_like_our_backend(&command_line) {
        return;
    }
    // SAFETY: the pid is a verified group leader running our backend.
    unsafe {
        libc::killpg(pid, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    while process_group_alive(pid) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    if process_group_alive(pid) {
        // SAFETY: as above.
        unsafe {
            libc::killpg(pid, libc::SIGKILL);
        }
    }
}

// ---------------------------------------------------------------------------
// Token, ports, logs
// ---------------------------------------------------------------------------

/// 32 random bytes from the OS, hex encoded.
pub(crate) fn generate_api_token() -> std::io::Result<String> {
    let mut bytes = [0_u8; 32];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn choose_backend_port() -> std::io::Result<u16> {
    TcpListener::bind((BACKEND_HOST, 0))
        .and_then(|listener| listener.local_addr().map(|addr| addr.port()))
}

fn open_backend_log(path: &Path) -> std::io::Result<(Stdio, Stdio)> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    // Keep the previous run's log so a crash before a restart stays diagnosable.
    if path.exists() {
        let _ = fs::rename(path, path.with_extension("previous.log"));
    }
    let file = File::create(path)?;
    let stderr = file.try_clone()?;
    Ok((Stdio::from(file), Stdio::from(stderr)))
}

/// Whether the tail of the backend log shows an address-in-use bind failure.
fn log_indicates_port_in_use(path: &Path) -> bool {
    let Ok(text) = fs::read_to_string(path) else {
        return false;
    };
    let tail: String = text
        .chars()
        .rev()
        .take(4000)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();
    let lower = tail.to_ascii_lowercase();
    lower.contains("address already in use")
        || lower.contains("errno 48")
        || lower.contains("errno 98")
}

// ---------------------------------------------------------------------------
// Health check
// ---------------------------------------------------------------------------

fn backend_addr(port: u16) -> String {
    format!("{BACKEND_HOST}:{port}")
}

pub(crate) fn backend_health_check(port: u16, token: Option<&str>, timeout: Duration) -> bool {
    let Some(addr) = backend_addr(port)
        .to_socket_addrs()
        .ok()
        .and_then(|mut addrs| addrs.next())
    else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, timeout) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let token_header = token
        .map(|token| format!("{BACKEND_TOKEN_HEADER}: {token}\r\n"))
        .unwrap_or_default();
    let request = format!(
        "GET {BACKEND_HEALTH_PATH} HTTP/1.1\r\nHost: {BACKEND_HOST}:{port}\r\n{token_header}Connection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut raw = Vec::new();
    if stream.read_to_end(&mut raw).is_err() && raw.is_empty() {
        return false;
    }
    backend_health_response_is_compatible(&String::from_utf8_lossy(&raw))
}

/// Split a raw HTTP/1.x response into (status code, decoded body).
fn parse_http_response(raw: &str) -> Option<(u16, String)> {
    let (head, body) = raw.split_once("\r\n\r\n")?;
    let mut lines = head.split("\r\n");
    let status_line = lines.next()?;
    let mut parts = status_line.split_whitespace();
    if !parts.next()?.starts_with("HTTP/1.") {
        return None;
    }
    let status = parts.next()?.parse::<u16>().ok()?;
    let chunked = lines.any(|line| {
        let lower = line.to_ascii_lowercase();
        lower.starts_with("transfer-encoding:") && lower.contains("chunked")
    });
    let body = if chunked {
        decode_chunked(body)?
    } else {
        body.to_string()
    };
    Some((status, body))
}

fn decode_chunked(mut body: &str) -> Option<String> {
    let mut out = String::new();
    loop {
        let (size_line, rest) = body.split_once("\r\n")?;
        let size = usize::from_str_radix(size_line.split(';').next()?.trim(), 16).ok()?;
        if size == 0 {
            return Some(out);
        }
        if rest.len() < size || !rest.is_char_boundary(size) {
            return None;
        }
        out.push_str(&rest[..size]);
        body = rest[size..].strip_prefix("\r\n")?;
    }
}

/// A healthy, feature-compatible backend answers 200 with
/// `{"ok": true, "app": "ssu-lecture-downloader", "capabilities": [...]}` and
/// advertises the artifact-download and STT capabilities this UI needs.
pub(crate) fn backend_health_response_is_compatible(response: &str) -> bool {
    let Some((status, body)) = parse_http_response(response) else {
        return false;
    };
    if status != 200 {
        return false;
    }
    let Ok(payload) = serde_json::from_str::<serde_json::Value>(&body) else {
        return false;
    };
    let capabilities = payload
        .get("capabilities")
        .and_then(|value| value.as_array())
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    payload.get("ok").and_then(|value| value.as_bool()) == Some(true)
        && payload.get("app").and_then(|value| value.as_str()) == Some("ssu-lecture-downloader")
        && capabilities.contains(&"artifact_download_jobs")
        && capabilities.contains(&"stt_provider_status")
}

/// Poll until the backend answers, the child exits, `timeout` elapses or the
/// caller aborts. The child exit check comes first so a dead child never
/// "passes" because something else answers on its port.
pub(crate) fn wait_for_backend_ready(
    port: u16,
    token: Option<&str>,
    timeout: Duration,
    poll_interval: Duration,
    mut child_exited: impl FnMut() -> Option<String>,
    mut aborted: impl FnMut() -> bool,
) -> WaitOutcome {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if aborted() {
            return WaitOutcome::Aborted;
        }
        if let Some(description) = child_exited() {
            return WaitOutcome::Exited(description);
        }
        if backend_health_check(port, token, Duration::from_millis(500)) {
            return WaitOutcome::Ready;
        }
        std::thread::sleep(poll_interval);
    }
    WaitOutcome::TimedOut
}

// ---------------------------------------------------------------------------
// Launch command
// ---------------------------------------------------------------------------

pub(crate) fn project_root_from_manifest_dir(manifest_dir: &Path) -> PathBuf {
    manifest_dir
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| manifest_dir.to_path_buf())
}

/// Production entry: debug builds always run from source, release builds
/// prefer the bundled sidecar. `SSU_BACKEND_EXECUTABLE` overrides both.
pub(crate) fn backend_launch_command(
    project_root: &Path,
    resource_dir: Option<&Path>,
    port: u16,
) -> BackendLaunchCommand {
    backend_launch_command_with(
        project_root,
        resource_dir,
        port,
        std::env::var_os("SSU_BACKEND_EXECUTABLE").map(PathBuf::from),
        !cfg!(debug_assertions),
    )
}

pub(crate) fn backend_launch_command_with(
    project_root: &Path,
    resource_dir: Option<&Path>,
    port: u16,
    executable_override: Option<PathBuf>,
    use_packaged_sidecar: bool,
) -> BackendLaunchCommand {
    let executable = match executable_override.and_then(|path| resolve_sidecar_executable(&path)) {
        Some(path) => Some(path),
        None if use_packaged_sidecar => packaged_sidecar_executable(project_root, resource_dir),
        None => None,
    };
    if let Some(executable) = executable {
        let cwd = executable
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| project_root.to_path_buf());
        return BackendLaunchCommand {
            program: executable.to_string_lossy().to_string(),
            args: vec![
                "--host".to_string(),
                BACKEND_HOST.to_string(),
                "--port".to_string(),
                port.to_string(),
            ],
            cwd,
            packaged_sidecar: true,
        };
    }

    BackendLaunchCommand {
        program: "uv".to_string(),
        args: vec![
            "run".to_string(),
            "uvicorn".to_string(),
            "ssu_lecture_downloader.api:app".to_string(),
            "--host".to_string(),
            BACKEND_HOST.to_string(),
            "--port".to_string(),
            port.to_string(),
        ],
        cwd: project_root.to_path_buf(),
        packaged_sidecar: false,
    }
}

/// A sidecar is either the PyInstaller `--onedir` layout
/// (`ssu-api/ssu-api` next to `_internal/`) or the legacy single-file `ssu-api`.
fn resolve_sidecar_executable(candidate: &Path) -> Option<PathBuf> {
    if candidate.is_dir() {
        let inner = candidate.join("ssu-api");
        return inner.is_file().then_some(inner);
    }
    candidate.is_file().then(|| candidate.to_path_buf())
}

pub(crate) fn packaged_sidecar_executable(
    project_root: &Path,
    resource_dir: Option<&Path>,
) -> Option<PathBuf> {
    let resource_candidates = resource_dir.into_iter().flat_map(|dir| {
        [
            dir.join("backend-sidecar").join("ssu-api"),
            dir.join("resources")
                .join("backend-sidecar")
                .join("ssu-api"),
        ]
    });
    let dev_candidate = project_root
        .join("frontend")
        .join("src-tauri")
        .join("resources")
        .join("backend-sidecar")
        .join("ssu-api");
    resource_candidates
        .chain(std::iter::once(dev_candidate))
        .find_map(|candidate| resolve_sidecar_executable(&candidate))
}

// ---------------------------------------------------------------------------
// Tauri commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub(crate) fn backend_supervision_status(
    state: tauri::State<'_, Arc<BackendManager>>,
) -> BackendStatus {
    state.status()
}

#[tauri::command]
pub(crate) fn backend_api_base_url(
    state: tauri::State<'_, Arc<BackendManager>>,
) -> Result<String, String> {
    Ok(state.connection().base_url)
}

#[tauri::command]
pub(crate) fn backend_api_connection(
    state: tauri::State<'_, Arc<BackendManager>>,
) -> BackendConnection {
    state.connection()
}

#[tauri::command]
pub(crate) fn backend_restart(state: tauri::State<'_, Arc<BackendManager>>) -> Result<(), String> {
    // Returns immediately; the kill-and-respawn runs on the supervisor thread.
    Arc::clone(&state).restart();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_root(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "ssu-backend-{name}-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create temp dir");
        root
    }

    #[test]
    fn project_root_resolves_two_levels_above_tauri_manifest_dir() {
        let manifest = Path::new("/repo/frontend/src-tauri");
        assert_eq!(project_root_from_manifest_dir(manifest), Path::new("/repo"));
    }

    #[test]
    fn backend_launch_command_runs_uvicorn_from_project_root_when_no_sidecar_exists() {
        let launch = backend_launch_command_with(Path::new("/repo"), None, 49152, None, true);
        assert_eq!(launch.program, "uv");
        assert_eq!(launch.cwd, Path::new("/repo"));
        assert!(!launch.packaged_sidecar);
        assert_eq!(
            launch.args,
            vec![
                "run",
                "uvicorn",
                "ssu_lecture_downloader.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                "49152"
            ]
        );
    }

    #[test]
    fn debug_style_launch_ignores_a_stale_packaged_sidecar() {
        let root = temp_root("stale-sidecar");
        let sidecar_dir = root.join("backend-sidecar");
        fs::create_dir_all(&sidecar_dir).unwrap();
        fs::write(sidecar_dir.join("ssu-api"), b"#!/bin/sh\n").unwrap();

        let dev = backend_launch_command_with(Path::new("/repo"), Some(&root), 49153, None, false);
        assert_eq!(dev.program, "uv");
        assert!(!dev.packaged_sidecar);

        let release =
            backend_launch_command_with(Path::new("/repo"), Some(&root), 49153, None, true);
        assert!(release.packaged_sidecar);
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn explicit_executable_override_wins_even_in_dev_mode() {
        let root = temp_root("override");
        let executable = root.join("custom-api");
        fs::write(&executable, b"#!/bin/sh\n").unwrap();
        let launch = backend_launch_command_with(
            Path::new("/repo"),
            None,
            49154,
            Some(executable.clone()),
            false,
        );
        assert_eq!(launch.program, executable.to_string_lossy());
        assert!(launch.packaged_sidecar);
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn release_launch_supports_onedir_and_legacy_single_file_sidecars() {
        let root = temp_root("onedir");
        // onedir: resources/backend-sidecar/ssu-api/ssu-api (+ _internal/)
        let onedir = root
            .join("resources")
            .join("backend-sidecar")
            .join("ssu-api");
        fs::create_dir_all(onedir.join("_internal")).unwrap();
        let executable = onedir.join("ssu-api");
        fs::write(&executable, b"#!/bin/sh\n").unwrap();

        assert_eq!(
            packaged_sidecar_executable(Path::new("/repo"), Some(&root)),
            Some(executable.clone())
        );
        let launch =
            backend_launch_command_with(Path::new("/repo"), Some(&root), 49155, None, true);
        assert_eq!(launch.program, executable.to_string_lossy());
        assert_eq!(launch.cwd, onedir);
        assert_eq!(launch.args, vec!["--host", "127.0.0.1", "--port", "49155"]);
        fs::remove_dir_all(&root).ok();

        // legacy single file: backend-sidecar/ssu-api
        let legacy_root = temp_root("legacy");
        let legacy_dir = legacy_root.join("backend-sidecar");
        fs::create_dir_all(&legacy_dir).unwrap();
        let legacy = legacy_dir.join("ssu-api");
        fs::write(&legacy, b"#!/bin/sh\n").unwrap();
        assert_eq!(
            packaged_sidecar_executable(Path::new("/repo"), Some(&legacy_root)),
            Some(legacy)
        );
        fs::remove_dir_all(legacy_root).ok();
    }

    #[test]
    fn empty_onedir_directory_is_not_mistaken_for_a_sidecar() {
        let root = temp_root("empty-dir");
        fs::create_dir_all(root.join("backend-sidecar").join("ssu-api")).unwrap();
        assert_eq!(
            packaged_sidecar_executable(Path::new("/repo"), Some(&root)),
            None
        );
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn backend_health_requires_artifact_and_stt_capabilities() {
        let old_backend = "HTTP/1.1 200 OK\r\n\r\n{\"ok\":true}";
        assert!(!backend_health_response_is_compatible(old_backend));

        let compatible_backend = "HTTP/1.1 200 OK\r\n\r\n{\"ok\":true,\"app\":\"ssu-lecture-downloader\",\"capabilities\":[\"runtime_memory_credentials\",\"download_jobs\",\"artifact_download_jobs\",\"stt_provider_status\"]}";
        assert!(backend_health_response_is_compatible(compatible_backend));
    }

    #[test]
    fn backend_health_parses_status_line_and_json_instead_of_substrings() {
        let body = "{\"ok\":true,\"app\":\"ssu-lecture-downloader\",\"capabilities\":[\"artifact_download_jobs\",\"stt_provider_status\"]}";
        // Spacing differences in the JSON are fine.
        let spaced = body.replace(":", ": ");
        assert!(backend_health_response_is_compatible(&format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{spaced}"
        )));
        // A non-200 status must fail even with a compatible body.
        assert!(!backend_health_response_is_compatible(&format!(
            "HTTP/1.1 503 Service Unavailable\r\n\r\n{body}"
        )));
        // "HTTP/1.1 200" must be the status line, not text inside the body.
        assert!(!backend_health_response_is_compatible(&format!(
            "HTTP/1.1 500 Error\r\n\r\nHTTP/1.1 200 {body}"
        )));
        // The capability must be a real array member, not a substring elsewhere.
        assert!(!backend_health_response_is_compatible(
            "HTTP/1.1 200 OK\r\n\r\n{\"ok\":true,\"app\":\"ssu-lecture-downloader\",\"note\":\"artifact_download_jobs stt_provider_status\"}"
        ));
        // Wrong app name.
        assert!(!backend_health_response_is_compatible(
            "HTTP/1.1 200 OK\r\n\r\n{\"ok\":true,\"app\":\"other\",\"capabilities\":[\"artifact_download_jobs\",\"stt_provider_status\"]}"
        ));
        // Chunked transfer encoding is decoded.
        let chunked = format!(
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n{:x}\r\n{body}\r\n0\r\n\r\n",
            body.len()
        );
        assert!(backend_health_response_is_compatible(&chunked));
    }

    #[test]
    fn health_check_sends_token_header_and_reads_json_response() {
        let listener = TcpListener::bind((BACKEND_HOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buf = [0_u8; 2048];
            let n = stream.read(&mut buf).unwrap();
            let request = String::from_utf8_lossy(&buf[..n]).to_string();
            let body = "{\"ok\":true,\"app\":\"ssu-lecture-downloader\",\"capabilities\":[\"artifact_download_jobs\",\"stt_provider_status\"]}";
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(response.as_bytes()).unwrap();
            request
        });
        assert!(backend_health_check(
            port,
            Some("secret-token"),
            Duration::from_secs(2)
        ));
        let request = server.join().unwrap().to_ascii_lowercase();
        assert!(request.starts_with("get /api/health http/1.1"), "{request}");
        assert!(request.contains("x-ssu-token: secret-token"), "{request}");
    }

    #[test]
    fn api_tokens_are_64_hex_chars_and_unique() {
        let first = generate_api_token().expect("token");
        let second = generate_api_token().expect("token");
        assert_eq!(first.len(), 64);
        assert!(first.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(first, second);
    }

    #[test]
    fn wait_fails_fast_when_child_exits_instead_of_waiting_for_the_deadline() {
        let port = choose_backend_port().unwrap();
        let started = Instant::now();
        let outcome = wait_for_backend_ready(
            port,
            None,
            Duration::from_secs(60),
            Duration::from_millis(20),
            || Some("exit 3".to_string()),
            || false,
        );
        assert_eq!(outcome, WaitOutcome::Exited("exit 3".to_string()));
        assert!(started.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn wait_times_out_when_nothing_answers_and_child_is_alive() {
        let port = choose_backend_port().unwrap();
        let outcome = wait_for_backend_ready(
            port,
            None,
            Duration::from_millis(300),
            Duration::from_millis(20),
            || None,
            || false,
        );
        assert_eq!(outcome, WaitOutcome::TimedOut);
    }

    #[test]
    fn wait_can_be_aborted_during_shutdown() {
        let port = choose_backend_port().unwrap();
        let outcome = wait_for_backend_ready(
            port,
            None,
            Duration::from_secs(60),
            Duration::from_millis(20),
            || None,
            || true,
        );
        assert_eq!(outcome, WaitOutcome::Aborted);
    }

    #[test]
    fn terminate_process_group_kills_grandchildren_too() {
        // sh stays alive as the group leader and backgrounds a long sleep (the "grandchild").
        let mut child = Command::new("sh")
            .args(["-c", "sleep 300 & echo $!; wait"])
            .stdout(Stdio::piped())
            .process_group(0)
            .spawn()
            .expect("spawn sh");
        let mut stdout = child.stdout.take().unwrap();
        let mut line = [0_u8; 32];
        let n = stdout.read(&mut line).unwrap();
        let grandchild: libc::pid_t = String::from_utf8_lossy(&line[..n]).trim().parse().unwrap();
        // SAFETY: existence probe.
        assert_eq!(
            unsafe { libc::kill(grandchild, 0) },
            0,
            "grandchild should be alive"
        );

        terminate_process_group(&mut child, Duration::from_secs(2));

        let deadline = Instant::now() + Duration::from_secs(3);
        let mut gone = false;
        while Instant::now() < deadline {
            // SAFETY: existence probe.
            if unsafe { libc::kill(grandchild, 0) } != 0 {
                gone = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        assert!(
            gone,
            "grandchild {grandchild} survived process group termination"
        );
    }

    #[test]
    fn terminate_process_group_escalates_to_sigkill_when_sigterm_is_ignored() {
        let mut child = Command::new("sh")
            .args(["-c", "trap '' TERM; while true; do sleep 1; done"])
            .process_group(0)
            .spawn()
            .expect("spawn sh");
        std::thread::sleep(Duration::from_millis(200));
        let started = Instant::now();
        terminate_process_group(&mut child, Duration::from_millis(300));
        assert!(started.elapsed() < Duration::from_secs(5));
        assert!(child.try_wait().unwrap().is_some(), "child must be reaped");
    }

    #[test]
    fn stale_backend_detection_only_matches_our_command_lines() {
        assert!(command_looks_like_our_backend(
            "uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port 5000"
        ));
        assert!(command_looks_like_our_backend(
            "/Applications/X.app/Contents/Resources/resources/backend-sidecar/ssu-api/ssu-api --host 127.0.0.1"
        ));
        assert!(!command_looks_like_our_backend(
            "/usr/bin/python3 -m http.server"
        ));
        assert!(!command_looks_like_our_backend("sleep 300"));
    }

    #[test]
    fn reap_stale_backend_leaves_unrelated_pidfile_processes_alone() {
        let root = temp_root("pidfile");
        let mut child = Command::new("sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .expect("spawn sleep");
        let pidfile = root.join("backend.pid");
        write_pidfile(&pidfile, child.id());

        reap_stale_backend(&pidfile);

        assert!(!pidfile.exists(), "pidfile is always cleared");
        assert!(
            child.try_wait().unwrap().is_none(),
            "unrelated process must survive"
        );
        let _ = child.kill();
        let _ = child.wait();
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn port_in_use_is_detected_from_the_log_tail() {
        let root = temp_root("log");
        let log = root.join("backend.log");
        fs::write(&log, "ERROR: [Errno 48] error while attempting to bind on address ('127.0.0.1', 8000): address already in use\n").unwrap();
        assert!(log_indicates_port_in_use(&log));
        fs::write(&log, "ModuleNotFoundError: No module named 'x'\n").unwrap();
        assert!(!log_indicates_port_in_use(&log));
        assert!(!log_indicates_port_in_use(&root.join("missing.log")));
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn failed_spawn_reports_failure_and_log_path_without_panicking() {
        let root = temp_root("spawn-fail");
        let manager =
            BackendManager::new(root.join("no-such-project"), None, root.join("logs"), None);
        // The nonexistent cwd makes Command::spawn fail immediately.
        let outcome = manager.launch_attempt();
        assert!(matches!(outcome, AttemptOutcome::Failed(_)));
        let status = manager.status();
        assert!(status.log_path.is_some());
        fs::remove_dir_all(root).ok();
    }

    const FAKE_BACKEND: &str = r#"
import json, os, subprocess, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])
token = os.environ.get("SSU_API_TOKEN")
with open(os.path.join(os.path.dirname(__file__), "starts.log"), "a") as handle:
    handle.write("start\n")
MODE = open(os.path.join(os.path.dirname(__file__), "mode")).read().strip()
if MODE == "exit":
    sys.exit(3)
if MODE == "port-conflict":
    sys.stderr.write("ERROR: [Errno 48] address already in use\n")
    sys.exit(1)
grandchild = subprocess.Popen(["sleep", "300"])
with open(os.path.join(os.path.dirname(__file__), "grandchild.pid"), "w") as handle:
    handle.write(str(grandchild.pid))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("X-SSU-Token") != token:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps({"ok": True, "app": "ssu-lecture-downloader",
                           "capabilities": ["artifact_download_jobs", "stt_provider_status"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"#;

    /// Supervisor wired to a fake python backend in `dir`; `mode` picks its behavior.
    fn fake_backend_manager(dir: &Path, mode: &str) -> Arc<BackendManager> {
        fs::write(dir.join("fake_backend.py"), FAKE_BACKEND).unwrap();
        fs::write(dir.join("mode"), mode).unwrap();
        let script = dir.join("fake_backend.py");
        let cwd = dir.to_path_buf();
        let mut manager = BackendManager::with_launcher(
            Box::new(move |port| BackendLaunchCommand {
                program: "python3".to_string(),
                args: vec![
                    script.to_string_lossy().to_string(),
                    "--host".to_string(),
                    BACKEND_HOST.to_string(),
                    "--port".to_string(),
                    port.to_string(),
                ],
                cwd: cwd.clone(),
                packaged_sidecar: false,
            }),
            dir.join("logs"),
            Some(dir.join("state")),
        );
        // Never adopt a developer's backend that happens to be running on :8000.
        manager.external_probe_port = choose_backend_port().unwrap();
        Arc::new(manager)
    }

    fn wait_while_starting(manager: &BackendManager, limit: Duration) {
        let deadline = Instant::now() + limit;
        while manager.status().state == BackendState::Starting && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(50));
        }
    }

    fn pid_alive(pid: libc::pid_t) -> bool {
        // SAFETY: existence probe.
        unsafe { libc::kill(pid, 0) == 0 }
    }

    #[test]
    fn managed_backend_becomes_ready_with_token_and_shutdown_kills_the_whole_tree() {
        let dir = temp_root("e2e-ready");
        let manager = fake_backend_manager(&dir, "serve");
        assert!(manager.start_in_background());
        assert_eq!(manager.status().state, BackendState::Starting);
        wait_while_starting(&manager, Duration::from_secs(30));

        let status = manager.status();
        assert_eq!(status.state, BackendState::Ready, "{status:?}");
        assert_eq!(status.mode, "managed");
        let connection = manager.connection();
        let token = connection
            .token
            .clone()
            .expect("managed backend gets a token");
        assert_eq!(token.len(), 64);
        let port: u16 = connection
            .base_url
            .rsplit(':')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        assert!(backend_health_check(
            port,
            Some(&token),
            Duration::from_secs(2)
        ));
        assert!(!backend_health_check(port, None, Duration::from_secs(2)));
        assert!(!backend_health_check(
            port,
            Some("wrong"),
            Duration::from_secs(2)
        ));
        assert!(status.log_path.is_some());

        let grandchild: libc::pid_t = fs::read_to_string(dir.join("grandchild.pid"))
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        let child_pid = manager.lock().child.as_ref().unwrap().id() as libc::pid_t;
        assert!(dir.join("state").join(BACKEND_PID_FILE).exists());
        assert!(pid_alive(grandchild));

        manager.shutdown();

        let deadline = Instant::now() + Duration::from_secs(3);
        while (pid_alive(grandchild) || pid_alive(child_pid)) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(25));
        }
        assert!(!pid_alive(child_pid), "backend leader survived shutdown");
        assert!(
            !pid_alive(grandchild),
            "backend grandchild orphaned by shutdown"
        );
        assert!(!dir.join("state").join(BACKEND_PID_FILE).exists());
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn child_exiting_during_startup_fails_fast_with_log_path() {
        let dir = temp_root("e2e-exit");
        let manager = fake_backend_manager(&dir, "exit");
        let started = Instant::now();
        assert!(manager.start_in_background());
        wait_while_starting(&manager, Duration::from_secs(30));

        let status = manager.status();
        assert_eq!(status.state, BackendState::Failed, "{status:?}");
        assert!(
            started.elapsed() < Duration::from_secs(20),
            "must not wait for the 90s deadline"
        );
        assert!(status.error.unwrap().contains("종료"));
        assert!(status.log_path.unwrap().ends_with("backend.log"));
        assert!(manager.connection().token.is_none());
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn bind_failure_is_retried_on_another_port_before_giving_up() {
        let dir = temp_root("e2e-port-conflict");
        let manager = fake_backend_manager(&dir, "port-conflict");
        assert!(manager.start_in_background());
        wait_while_starting(&manager, Duration::from_secs(60));

        let status = manager.status();
        assert_eq!(status.state, BackendState::Failed, "{status:?}");
        let starts = fs::read_to_string(dir.join("starts.log")).unwrap();
        assert_eq!(starts.lines().count(), MAX_SPAWN_ATTEMPTS as usize);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn failed_backend_can_be_restarted_and_then_becomes_ready() {
        let dir = temp_root("e2e-restart");
        let manager = fake_backend_manager(&dir, "exit");
        assert!(manager.start_in_background());
        wait_while_starting(&manager, Duration::from_secs(30));
        assert_eq!(manager.status().state, BackendState::Failed);
        // Wait for the worker to release its slot, then fix the "bug" and retry.
        let deadline = Instant::now() + Duration::from_secs(5);
        while manager.lock().worker_active && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        fs::write(dir.join("mode"), "serve").unwrap();
        assert!(manager.restart());
        wait_while_starting(&manager, Duration::from_secs(30));
        assert_eq!(
            manager.status().state,
            BackendState::Ready,
            "{:?}",
            manager.status()
        );
        manager.shutdown();
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn restart_is_refused_while_a_startup_is_running() {
        let root = temp_root("restart-guard");
        let manager = Arc::new(BackendManager::new(
            root.join("project"),
            None,
            root.join("logs"),
            None,
        ));
        manager.lock().worker_active = true;
        assert!(!manager.restart());
        assert!(!manager.start_in_background());
        fs::remove_dir_all(root).ok();
    }
}
