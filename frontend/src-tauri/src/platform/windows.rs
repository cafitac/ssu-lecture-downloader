//! Windows platform shell: compile-only stubs. Nothing here has behavior yet; fallible calls
//! return an error that says so, the rest are no-ops. S2 replaces each stub with a real adapter
//! (schtasks, toast notification, Job Object, explorer, tray tooltip, overlay icon, file lock).

use crate::reminders::{Notifier, APP_IDENTIFIER};
use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::time::Duration;

const NOT_SUPPORTED: &str = "Windows에서는 아직 지원되지 않습니다 (not supported yet)";

fn unsupported(what: &str) -> String {
    format!("{what}: {NOT_SUPPORTED}")
}

// App data and the real home directory.

pub(crate) fn real_user_home_dir() -> Option<PathBuf> {
    None
}

/// Placeholder until the Windows app data location is decided in S2.
pub(crate) fn app_data_dir_for_home(home: &Path) -> PathBuf {
    home.join(APP_IDENTIFIER)
}

// Reminder schedule.

pub(crate) fn reminder_schedule_supported() -> bool {
    false
}

pub(crate) fn reminder_schedule_installed() -> bool {
    false
}

pub(crate) fn install_reminder_schedule() -> Result<(), String> {
    Err(unsupported("reminder schedule"))
}

pub(crate) fn remove_reminder_schedule() -> Result<(), String> {
    Err(unsupported("reminder schedule"))
}

// Notifications.

pub(crate) struct PlatformNotifier;

impl Notifier for PlatformNotifier {
    fn notify(&self, _title: &str, _body: &str) -> Result<(), String> {
        Err(unsupported("notification"))
    }
}

// File lock.

pub(crate) fn lock_exclusive(_file: &File) {}

// Open a folder / URL.

pub(crate) fn open_path(_folder: &Path) -> Result<(), String> {
    Err(unsupported("open folder"))
}

pub(crate) fn open_url(_url: &str) -> Result<(), String> {
    Err(unsupported("open URL"))
}

// Tray status text and badge.

pub(crate) fn set_tray_title(_tray: &tauri::tray::TrayIcon, _title: Option<&str>) {}

pub(crate) fn set_badge(_window: &tauri::WebviewWindow, _badge: Option<i64>) {}

// Sidecar.

pub(crate) fn sidecar_executable_name() -> &'static str {
    "ssu-api.exe"
}

pub(crate) fn configure_sidecar_command(_command: &mut Command) {}

pub(crate) fn terminate_process_group(_child: &mut Child, _grace: Duration) {}

pub(crate) fn reap_stale_backend(_pidfile: &Path) {}

// API token source.

pub(crate) fn generate_api_token() -> std::io::Result<String> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        NOT_SUPPORTED,
    ))
}
