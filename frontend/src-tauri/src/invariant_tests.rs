//! Characterization tests: the macOS behavior that later platform-split PRs must keep
//! byte-identical (design/07-consistency-migration.md, "macOS invariants" 1, 2 and 4).
//!
//! They pin what the code does today through its existing pub/pub(crate) surface. A failing test
//! here means a macOS behavior changed: fix the change, not the expectation, unless that change is
//! the explicit goal of the PR.

use crate::backend::{
    backend_launch_command_with, packaged_sidecar_executable, BACKEND_HOST, BACKEND_TOKEN_ENV,
    BACKEND_TOKEN_HEADER, DEFAULT_BACKEND_PORT,
};
use crate::launch_agent::{self, Launchctl};
use crate::reminders;
use std::cell::RefCell;
use std::fs;
use std::path::{Path, PathBuf};

const EXE: &str = "/Applications/SSU Lecture Downloader.app/Contents/MacOS/ssu-lecture-downloader";

fn temp_dir(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "ssu-invariant-{name}-{}-{}",
        std::process::id(),
        reminders::unix_now()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

// ------------------------------------------------------- invariant 1: app-data paths

#[test]
fn app_identifier_and_data_dir_for_a_given_home() {
    assert_eq!(
        reminders::APP_IDENTIFIER,
        "com.local.ssu-lecture-downloader"
    );
    assert_eq!(
        reminders::data_dir_for_home(Path::new("/Users/me")),
        Path::new("/Users/me/Library/Application Support/com.local.ssu-lecture-downloader")
    );
}

#[test]
fn reminder_data_file_names() {
    assert_eq!(reminders::CACHE_FILE, "deadlines.json");
    assert_eq!(reminders::NOTIFIED_FILE, "notified.json");
    assert_eq!(reminders::SETTINGS_FILE, "settings.json");
    assert_eq!(reminders::CHECK_ARG, "--check-reminders");
}

#[test]
fn tauri_conf_identifier_and_bundled_sidecar_resource() {
    let conf: serde_json::Value =
        serde_json::from_str(include_str!("../tauri.conf.json")).expect("tauri.conf.json");
    assert_eq!(conf["identifier"], "com.local.ssu-lecture-downloader");
    assert_eq!(conf["productName"], "SSU Lecture Downloader");
    assert_eq!(
        conf["bundle"]["resources"],
        serde_json::json!(["resources/backend-sidecar"])
    );
}

#[test]
fn atomic_writes_create_owner_only_files() {
    use std::os::unix::fs::PermissionsExt;
    let dir = temp_dir("atomic");
    let path = dir.join("deadlines.json");
    reminders::write_atomic(&path, b"{}").unwrap();
    assert_eq!(
        fs::metadata(&path).unwrap().permissions().mode() & 0o777,
        0o600
    );
    fs::remove_dir_all(dir).ok();
}

// ------------------------------------------------------- invariant 2: LaunchAgent

#[test]
fn launch_agent_label_interval_and_plist_location() {
    assert_eq!(
        launch_agent::LABEL,
        "com.local.ssu-lecture-downloader.reminders"
    );
    assert_eq!(launch_agent::START_INTERVAL_SECS, 1800);
    assert_eq!(
        launch_agent::plist_path(Path::new("/Users/me")),
        Path::new(
            "/Users/me/Library/LaunchAgents/com.local.ssu-lecture-downloader.reminders.plist"
        )
    );
}

#[test]
fn launch_agent_plist_is_byte_identical() {
    let expected = concat!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n",
        "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n",
        "<plist version=\"1.0\">\n",
        "<dict>\n",
        "    <key>Label</key>\n",
        "    <string>com.local.ssu-lecture-downloader.reminders</string>\n",
        "    <key>ProgramArguments</key>\n",
        "    <array>\n",
        "        <string>/Applications/SSU Lecture Downloader.app/Contents/MacOS/ssu-lecture-downloader</string>\n",
        "        <string>--check-reminders</string>\n",
        "    </array>\n",
        "    <key>StartInterval</key>\n",
        "    <integer>1800</integer>\n",
        "    <key>RunAtLoad</key>\n",
        "    <true/>\n",
        "    <key>ProcessType</key>\n",
        "    <string>Background</string>\n",
        "</dict>\n",
        "</plist>\n",
    );
    assert_eq!(launch_agent::render_plist(Path::new(EXE)), expected);
}

#[test]
fn packaged_exe_predicate_is_unchanged() {
    assert!(launch_agent::is_packaged_exe(Path::new(EXE)));
    assert!(!launch_agent::is_packaged_exe(Path::new(
        "/Users/me/p/frontend/src-tauri/target/debug/ssu-lecture-downloader"
    )));
    assert!(!launch_agent::is_packaged_exe(Path::new(
        "/Volumes/Disk/X.app/Contents/MacOS/x"
    )));
    assert!(!launch_agent::is_packaged_exe(Path::new(
        "/private/var/folders/AppTranslocation/ABC/d/X.app/Contents/MacOS/x"
    )));
}

/// Records every `launchctl` argv exactly as the production code builds it.
struct RecordingCtl {
    calls: RefCell<Vec<Vec<String>>>,
    loaded: RefCell<bool>,
    bootstrap_ok: bool,
}

impl RecordingCtl {
    fn new(loaded: bool, bootstrap_ok: bool) -> Self {
        RecordingCtl {
            calls: RefCell::new(Vec::new()),
            loaded: RefCell::new(loaded),
            bootstrap_ok,
        }
    }
    fn calls(&self) -> Vec<Vec<String>> {
        self.calls.borrow().clone()
    }
}

impl Launchctl for RecordingCtl {
    fn run(&self, args: &[&str]) -> bool {
        self.calls
            .borrow_mut()
            .push(args.iter().map(|arg| arg.to_string()).collect());
        match args[0] {
            "print" => *self.loaded.borrow(),
            "bootout" => std::mem::replace(&mut *self.loaded.borrow_mut(), false),
            "bootstrap" => self.bootstrap_ok,
            _ => true,
        }
    }
}

fn argv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|part| part.to_string()).collect()
}

#[test]
fn launchctl_calls_for_a_fresh_install_and_a_bootstrap_fallback() {
    let home = temp_dir("install");
    let plist = launch_agent::plist_path(&home)
        .to_string_lossy()
        .to_string();

    let ctl = RecordingCtl::new(false, true);
    launch_agent::install(&home, Path::new(EXE), 501, &ctl).unwrap();
    assert_eq!(
        ctl.calls(),
        vec![
            argv(&[
                "print",
                "gui/501/com.local.ssu-lecture-downloader.reminders"
            ]),
            argv(&["bootstrap", "gui/501", &plist]),
        ]
    );
    assert_eq!(
        fs::read_to_string(&plist).unwrap(),
        launch_agent::render_plist(Path::new(EXE))
    );

    // Same plist, not loaded, bootstrap refused: falls back to `load -w`.
    let ctl = RecordingCtl::new(false, false);
    launch_agent::install(&home, Path::new(EXE), 501, &ctl).unwrap();
    assert_eq!(
        ctl.calls(),
        vec![
            argv(&[
                "print",
                "gui/501/com.local.ssu-lecture-downloader.reminders"
            ]),
            argv(&["bootstrap", "gui/501", &plist]),
            argv(&["load", "-w", &plist]),
        ]
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn launchctl_calls_when_the_executable_moved_and_on_uninstall() {
    let home = temp_dir("moved");
    let plist = launch_agent::plist_path(&home)
        .to_string_lossy()
        .to_string();
    launch_agent::install(
        &home,
        Path::new("/Old/X.app/Contents/MacOS/x"),
        501,
        &RecordingCtl::new(false, true),
    )
    .unwrap();

    let ctl = RecordingCtl::new(true, true);
    launch_agent::install(&home, Path::new(EXE), 501, &ctl).unwrap();
    assert_eq!(
        ctl.calls(),
        vec![
            argv(&[
                "print",
                "gui/501/com.local.ssu-lecture-downloader.reminders"
            ]),
            argv(&[
                "bootout",
                "gui/501/com.local.ssu-lecture-downloader.reminders"
            ]),
            argv(&["bootstrap", "gui/501", &plist]),
        ]
    );

    let ctl = RecordingCtl::new(true, true);
    launch_agent::uninstall(&home, 501, &ctl).unwrap();
    assert_eq!(
        ctl.calls(),
        vec![argv(&[
            "bootout",
            "gui/501/com.local.ssu-lecture-downloader.reminders"
        ])]
    );
    assert!(!launch_agent::is_installed(&home));
    fs::remove_dir_all(home).ok();
}

#[test]
fn osascript_args_are_exact() {
    assert_eq!(
        reminders::osascript_args("제목", "내용"),
        argv(&[
            "-e",
            "on run argv",
            "-e",
            "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e",
            "end run",
            "제목",
            "내용",
        ])
    );
}

// ------------------------------------------------------- invariant 4: sidecar location and flags

#[test]
fn backend_connection_constants() {
    assert_eq!(BACKEND_HOST, "127.0.0.1");
    assert_eq!(DEFAULT_BACKEND_PORT, 8000);
    assert_eq!(BACKEND_TOKEN_ENV, "SSU_API_TOKEN");
    assert_eq!(BACKEND_TOKEN_HEADER, "X-SSU-Token");
}

#[test]
fn sidecar_is_found_at_the_onedir_path_under_the_resource_dir() {
    let resources = temp_dir("sidecar");
    let onedir = resources
        .join("resources")
        .join("backend-sidecar")
        .join("ssu-api");
    fs::create_dir_all(onedir.join("_internal")).unwrap();
    let executable = onedir.join("ssu-api");
    fs::write(&executable, b"#!/bin/sh\n").unwrap();

    assert_eq!(
        packaged_sidecar_executable(Path::new("/nowhere"), Some(&resources)),
        Some(executable.clone())
    );
    assert_eq!(
        executable.strip_prefix(&resources).unwrap(),
        Path::new("resources/backend-sidecar/ssu-api/ssu-api")
    );

    let launch =
        backend_launch_command_with(Path::new("/nowhere"), Some(&resources), 49200, None, true);
    assert!(launch.packaged_sidecar);
    assert_eq!(launch.program, executable.to_string_lossy());
    assert_eq!(launch.cwd, onedir);
    assert_eq!(
        launch.args,
        argv(&["--host", "127.0.0.1", "--port", "49200"])
    );
    fs::remove_dir_all(resources).ok();
}

#[test]
fn sidecar_is_found_in_the_dev_resources_dir_of_the_project_root() {
    let root = temp_dir("dev-sidecar");
    let onedir = root
        .join("frontend")
        .join("src-tauri")
        .join("resources")
        .join("backend-sidecar")
        .join("ssu-api");
    fs::create_dir_all(&onedir).unwrap();
    let executable = onedir.join("ssu-api");
    fs::write(&executable, b"#!/bin/sh\n").unwrap();
    assert_eq!(packaged_sidecar_executable(&root, None), Some(executable));
    fs::remove_dir_all(root).ok();
}

#[test]
fn without_a_sidecar_the_backend_runs_uvicorn_from_the_project_root() {
    let launch = backend_launch_command_with(Path::new("/repo"), None, 49201, None, true);
    assert!(!launch.packaged_sidecar);
    assert_eq!(launch.program, "uv");
    assert_eq!(launch.cwd, Path::new("/repo"));
    assert_eq!(
        launch.args,
        argv(&[
            "run",
            "uvicorn",
            "ssu_lecture_downloader.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            "49201",
        ])
    );
}

// ------------------------------------------------------- source-text tripwires for private items
//
// These items are private (or inline in a function) and pinning them through behavior would need
// a production change. The tripwires read the source, so they fail when the text changes; they do
// not prove behavior. S1-RS-PLATFORM must MOVE each assertion to where the code lands (the new
// file and any new constant name), not delete it.

const BACKEND_RS: &str = include_str!("backend.rs");
const REMINDERS_RS: &str = include_str!("reminders.rs");

/// The production part of a source file: everything before its `#[cfg(test)] mod tests`, so a
/// match inside the unit tests can never satisfy a tripwire.
fn production_part(source: &'static str) -> &'static str {
    const MARKER: &str = "#[cfg(test)]\nmod tests";
    let (production, _tests) = source
        .split_once(MARKER)
        .expect("test-module marker missing: the tripwire slice would be the whole file");
    production
}

#[test]
fn backend_private_constants_and_spawn_flags_are_unchanged() {
    let backend = production_part(BACKEND_RS);
    assert!(backend.contains(r#"const BACKEND_PID_FILE: &str = "backend.pid";"#));
    assert!(backend.contains("const BACKEND_TERMINATE_GRACE: Duration = Duration::from_secs(3);"));
    // Spawn: own process group so the whole tree can be signalled at once.
    assert!(backend.contains(".process_group(0)"));
    assert!(backend.contains(".env(BACKEND_TOKEN_ENV, &token)"));
    // Stop: the managed child is terminated with the grace constant, SIGTERM to the group, then
    // SIGKILL to what is left.
    assert!(backend.contains("terminate_process_group(&mut child, BACKEND_TERMINATE_GRACE)"));
    assert!(backend.contains("libc::killpg(pgid, libc::SIGTERM);"));
    assert!(backend.contains("libc::killpg(pgid, libc::SIGKILL);"));
}

#[test]
fn reminders_lock_file_name_is_unchanged() {
    assert!(production_part(REMINDERS_RS).contains(r#"const LOCK_FILE: &str = "reminders.lock";"#));
}
