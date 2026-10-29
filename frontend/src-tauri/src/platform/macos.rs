//! macOS platform shell. Every body here was moved verbatim from the shared modules
//! (`lib.rs`, `backend.rs`, `reminders.rs`, `watch_runtime.rs`); only visibility and paths
//! changed. The behavior pinned by `invariant_tests.rs` must stay byte-identical.

use crate::reminders::{Notifier, APP_IDENTIFIER};
use std::ffi::CStr;
use std::fs::{self, File};
use std::io::Read;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::time::{Duration, Instant};

pub(crate) mod launch_agent;

// ---------------------------------------------------------------------------
// App data and the real home directory (was lib.rs / reminders.rs)
// ---------------------------------------------------------------------------

#[cfg(target_os = "macos")]
pub(crate) fn real_user_home_dir() -> Option<PathBuf> {
    unsafe {
        let passwd = libc::getpwuid(libc::getuid());
        if passwd.is_null() {
            return fallback_home_dir();
        }
        let home = CStr::from_ptr((*passwd).pw_dir)
            .to_string_lossy()
            .to_string();
        if home.is_empty() {
            fallback_home_dir()
        } else {
            Some(PathBuf::from(home))
        }
    }
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn real_user_home_dir() -> Option<PathBuf> {
    fallback_home_dir()
}

fn fallback_home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

/// `<home>/Library/Application Support/<identifier>`, where Tauri keeps the app data on macOS.
pub(crate) fn app_data_dir_for_home(home: &Path) -> PathBuf {
    home.join("Library")
        .join("Application Support")
        .join(APP_IDENTIFIER)
}

// ---------------------------------------------------------------------------
// Reminder schedule: the per-user LaunchAgent (was lib.rs)
// ---------------------------------------------------------------------------

/// Whether the background (LaunchAgent) part can work here: only in a packaged release app.
pub(crate) fn reminder_schedule_supported() -> bool {
    launch_agent::supported()
}

pub(crate) fn reminder_schedule_installed() -> bool {
    real_user_home_dir()
        .map(|home| launch_agent::is_installed(&home))
        .unwrap_or(false)
}

/// Installs (or refreshes) the LaunchAgent for the running executable.
pub(crate) fn install_reminder_schedule() -> Result<(), String> {
    let home = real_user_home_dir().ok_or("홈 폴더를 찾을 수 없습니다.")?;
    let exe = std::env::current_exe().map_err(|error| error.to_string())?;
    launch_agent::install(
        &home,
        &exe,
        unsafe { libc::getuid() },
        &launch_agent::SystemLaunchctl,
    )
}

pub(crate) fn remove_reminder_schedule() -> Result<(), String> {
    let home = real_user_home_dir().ok_or("홈 폴더를 찾을 수 없습니다.")?;
    launch_agent::uninstall(
        &home,
        unsafe { libc::getuid() },
        &launch_agent::SystemLaunchctl,
    )
}

// ---------------------------------------------------------------------------
// Notifications (was reminders.rs)
// ---------------------------------------------------------------------------

/// `osascript` arguments for one notification. The text travels as separate argv items read by
/// an `on run argv` handler, never interpolated into the script, so quotes, backslashes or
/// AppleScript syntax in a title cannot change what runs.
pub(crate) fn osascript_args(title: &str, body: &str) -> Vec<String> {
    vec![
        "-e".to_string(),
        "on run argv".to_string(),
        "-e".to_string(),
        "display notification (item 2 of argv) with title (item 1 of argv)".to_string(),
        "-e".to_string(),
        "end run".to_string(),
        title.to_string(),
        body.to_string(),
    ]
}

pub(crate) struct PlatformNotifier;

impl Notifier for PlatformNotifier {
    fn notify(&self, title: &str, body: &str) -> Result<(), String> {
        let output = std::process::Command::new("/usr/bin/osascript")
            .args(osascript_args(title, body))
            .output()
            .map_err(|error| format!("알림을 보낼 수 없습니다: {error}"))?;
        if output.status.success() {
            Ok(())
        } else {
            Err(format!(
                "알림을 보낼 수 없습니다: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            ))
        }
    }
}

// ---------------------------------------------------------------------------
// File lock (was reminders.rs `DirLock::acquire`)
// ---------------------------------------------------------------------------

/// Takes an exclusive advisory lock on `file`; released when the file is closed.
pub(crate) fn lock_exclusive(file: &File) {
    use std::os::fd::AsRawFd;
    // Blocks until the other checker is done; failing to lock must not block reminders.
    let _ = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
}

// ---------------------------------------------------------------------------
// Open a folder / URL (was lib.rs)
// ---------------------------------------------------------------------------

pub(crate) fn open_path(folder: &Path) -> Result<(), String> {
    Command::new("open")
        .arg(folder)
        .status()
        .map_err(|error| format!("Finder를 열 수 없습니다: {error}"))
        .and_then(|status| {
            if status.success() {
                Ok(())
            } else {
                Err(format!("Finder 열기 명령이 실패했습니다: {status}"))
            }
        })
}

pub(crate) fn open_url(url: &str) -> Result<(), String> {
    Command::new("open")
        .arg(url)
        .status()
        .map_err(|error| format!("브라우저를 열 수 없습니다: {error}"))
        .and_then(|status| {
            if status.success() {
                Ok(())
            } else {
                Err(format!("브라우저 열기 명령이 실패했습니다: {status}"))
            }
        })
}

// ---------------------------------------------------------------------------
// Tray status text and Dock badge (was watch_runtime.rs)
// ---------------------------------------------------------------------------

pub(crate) fn set_tray_title(tray: &tauri::tray::TrayIcon, title: Option<&str>) {
    let _ = tray.set_title(title);
}

pub(crate) fn set_badge(window: &tauri::WebviewWindow, badge: Option<i64>) {
    let _ = window.set_badge_count(badge);
}

// ---------------------------------------------------------------------------
// Sidecar: file name, process group, stop (was backend.rs)
// ---------------------------------------------------------------------------

/// File name of the backend executable inside the bundled `ssu-api` directory.
pub(crate) fn sidecar_executable_name() -> &'static str {
    "ssu-api"
}

/// Spawn-time setup of the managed backend command.
pub(crate) fn configure_sidecar_command(command: &mut Command) {
    // Own process group so the whole tree can be signalled at once.
    command.process_group(0);
}

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
pub(crate) fn reap_stale_backend(pidfile: &Path) {
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
// API token source (was backend.rs)
// ---------------------------------------------------------------------------

/// 32 random bytes from the OS, hex encoded.
///
/// Reads `/dev/urandom`, so it is unix-only. Windows (S2) needs another source.
pub(crate) fn generate_api_token() -> std::io::Result<String> {
    let mut bytes = [0_u8; 32];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn osascript_args_keep_text_out_of_the_script() {
        let title = r#"기한 "임박": \ do shell script "rm -rf ~""#;
        let body = "강의 · a\"b'c\\d";
        let args = osascript_args(title, body);
        let script: Vec<&String> = args
            .iter()
            .enumerate()
            .filter(|(i, _)| *i < 6 && i % 2 == 1)
            .map(|(_, a)| a)
            .collect();
        for line in &script {
            assert!(!line.contains("rm -rf") && !line.contains("강의"), "{line}");
        }
        assert_eq!(args[args.len() - 2], title);
        assert_eq!(args[args.len() - 1], body);
        assert_eq!(args[0], "-e");
        assert_eq!(args[1], "on run argv");
    }

    #[test]
    fn data_dir_is_application_support_under_the_tauri_identifier() {
        assert_eq!(
            app_data_dir_for_home(Path::new("/Users/x")),
            Path::new("/Users/x/Library/Application Support/com.local.ssu-lecture-downloader")
        );
    }

    #[test]
    fn sidecar_executable_name_is_ssu_api() {
        assert_eq!(sidecar_executable_name(), "ssu-api");
    }
}
