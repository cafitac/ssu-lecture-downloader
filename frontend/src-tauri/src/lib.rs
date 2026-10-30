#[cfg(test)]
mod acl_tests;
mod backend;
mod diagnostics;
#[cfg(all(test, target_os = "macos"))]
mod invariant_tests;
mod log_rotation;
mod native_download;
mod platform;
mod reminders;
mod session_marker;
mod update;
mod viewer;
mod watch_external;
mod watch_queue;
mod watch_runtime;

use std::path::{Path, PathBuf};
use std::sync::Arc;
use tauri::Manager;

use backend::BackendManager;
use reminders::ReminderService;

#[derive(serde::Serialize)]
struct RemindersStatus {
    enabled: bool,
    agent_installed: bool,
    last_saved_at: Option<i64>,
    /// The background (LaunchAgent) part works only in a packaged release app.
    supported: bool,
}

fn reminders_status_for(service: &ReminderService) -> RemindersStatus {
    RemindersStatus {
        enabled: service.enabled(),
        agent_installed: platform::reminder_schedule_installed(),
        last_saved_at: reminders::read_cache(service.dir()).map(|cache| cache.saved_at),
        supported: platform::reminder_schedule_supported(),
    }
}

#[tauri::command]
fn deadline_cache_write(
    service: tauri::State<'_, Arc<ReminderService>>,
    items: Vec<reminders::CachedItem>,
) -> Result<(), String> {
    reminders::write_cache(service.dir(), items, reminders::unix_now())?;
    service.check_soon();
    Ok(())
}

#[tauri::command]
fn reminders_status(service: tauri::State<'_, Arc<ReminderService>>) -> RemindersStatus {
    reminders_status_for(&service)
}

#[tauri::command]
fn reminders_set_enabled(
    service: tauri::State<'_, Arc<ReminderService>>,
    enabled: bool,
) -> Result<RemindersStatus, String> {
    reminders::write_enabled_setting(service.dir(), enabled)?;
    if platform::reminder_schedule_supported() {
        if enabled {
            platform::install_reminder_schedule()?;
        } else {
            platform::remove_reminder_schedule()?;
        }
    }
    if enabled {
        service.check_soon();
    }
    Ok(reminders_status_for(&service))
}

#[tauri::command]
fn reminders_test() -> Result<(), String> {
    use reminders::Notifier;
    platform::PlatformNotifier.notify(
        "기한 임박: 알림 테스트",
        "과제 · 샘플 알림 · 실제 기한은 이렇게 알려 드려요",
    )
}

#[tauri::command]
fn real_download_dir() -> Result<String, String> {
    platform::real_user_home_dir()
        .map(|home| home.join("Downloads").to_string_lossy().to_string())
        .ok_or_else(|| "Could not resolve the macOS user Downloads folder".to_string())
}

fn folder_to_open(path: &Path) -> PathBuf {
    if path.is_file() {
        path.parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| path.to_path_buf())
    } else {
        path.to_path_buf()
    }
}

#[tauri::command]
fn open_path_in_finder(path: String) -> Result<(), String> {
    if path.trim().is_empty() {
        return Err("다운로드 폴더 경로가 비어 있습니다.".to_string());
    }
    let folder = folder_to_open(Path::new(&path));
    platform::open_path(&folder)
}

/// Whether the previous run of the app ended abnormally (no clean exit). Reads and clears the
/// flag: `true` only on the first call after a crashed run.
#[tauri::command]
fn previous_run_crashed(marker: tauri::State<'_, session_marker::SessionMarker>) -> bool {
    marker.take_previous_crashed()
}

#[tauri::command]
fn open_url_in_browser(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("공식 시청 주소가 올바르지 않습니다.".to_string());
    }
    platform::open_url(&url)
}

/// The app's context (config, assets and the capabilities compiled in by `build.rs`). The macro
/// may expand only once per crate, so the tests take it from here too.
fn app_context<R: tauri::Runtime>() -> tauri::Context<R> {
    tauri::generate_context!()
}

fn shutdown_backend(app: &tauri::AppHandle) {
    if let Some(manager) = app.try_state::<Arc<BackendManager>>() {
        manager.shutdown();
    }
}

pub fn run() {
    // Started by the reminders LaunchAgent: check the cache, notify, exit. No Tauri, no
    // backend, no window.
    if reminders::is_check_invocation(std::env::args()) {
        std::process::exit(reminders::run_check_cli(platform::real_user_home_dir()));
    }
    let project_root =
        backend::project_root_from_manifest_dir(Path::new(env!("CARGO_MANIFEST_DIR")));
    let builder = tauri::Builder::default().plugin(tauri_plugin_dialog::init());
    // Inert until the frontend calls the updater: nothing here checks for or installs updates.
    #[cfg(desktop)]
    let builder = builder
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init());
    let built = builder
        .on_window_event(|window, event| {
            // While the playback window runs, closing the main window only hides it, so the
            // menu bar item's "앱 열기" can bring it back.
            if window.label() == "main" {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    if watch_runtime::is_queue_active(window.app_handle()) {
                        api.prevent_close();
                        let _ = window.hide();
                    }
                }
            }
        })
        .setup(move |app| {
            // Manage everything first and start the backend off the main thread:
            // a slow or failing backend must never block or abort window creation.
            let log_dir = app
                .path()
                .app_log_dir()
                .unwrap_or_else(|_| std::env::temp_dir().join("ssu-lecture-downloader-logs"));
            let manager = Arc::new(BackendManager::new(
                project_root.clone(),
                app.path().resource_dir().ok(),
                log_dir,
                app.path().app_data_dir().ok(),
            ));
            app.manage(Arc::clone(&manager));
            app.manage(native_download::NativeDownloadJobs::new());
            app.manage(viewer::ViewerState::new());
            // Sequential watch: queue state, menu bar item and the periodic refresh/watchdog.
            app.manage(watch_runtime::WatchRuntime::new());
            watch_runtime::install_tray(app.handle());
            watch_runtime::spawn_ticker(app.handle().clone());
            manager.start_in_background();

            // Reminders: on by default in a packaged app, in-app checks every 10 minutes.
            let data_dir = app
                .path()
                .app_data_dir()
                .ok()
                .or_else(|| {
                    platform::real_user_home_dir()
                        .map(|home| platform::app_data_dir_for_home(&home))
                })
                .unwrap_or_else(|| std::env::temp_dir().join("ssu-lecture-downloader"));
            let marker_dir = data_dir.clone();
            let packaged = platform::reminder_schedule_supported();
            let service =
                ReminderService::start(data_dir, packaged, Arc::new(platform::PlatformNotifier));
            if packaged && service.enabled() {
                // Also repairs the agent when the app was moved or the plist went missing.
                if let Err(error) = platform::install_reminder_schedule() {
                    eprintln!("reminders: {error}");
                }
            }
            app.manage(service);
            // Last: from here on the run counts as started; only a clean exit removes the marker.
            app.manage(session_marker::SessionMarker::begin(marker_dir));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            real_download_dir,
            deadline_cache_write,
            reminders_status,
            reminders_set_enabled,
            reminders_test,
            open_path_in_finder,
            open_url_in_browser,
            viewer::open_official_viewer_with_session,
            viewer::open_lms_page_with_session,
            watch_runtime::watch_queue_start,
            watch_runtime::watch_queue_next,
            watch_runtime::watch_queue_stop,
            watch_runtime::watch_queue_status,
            watch_runtime::watch_queue_show_window,
            watch_runtime::watch_external_status,
            watch_runtime::watch_signal,
            backend::backend_supervision_status,
            backend::backend_api_base_url,
            backend::backend_api_connection,
            backend::backend_restart,
            native_download::native_start_download_job,
            native_download::native_start_batch_download_job,
            native_download::native_download_job_status,
            native_download::native_cancel_download_job,
            update::update_install_guard,
            update::prepare_for_update,
            diagnostics::save_diagnostics,
            diagnostics::diagnostics_auto_destination,
            previous_run_crashed
        ])
        .build(app_context());
    match built {
        Ok(app) => app.run(|app_handle, event| {
            // Kill the managed backend's whole process group on every exit path;
            // `shutdown` is idempotent.
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                watch_runtime::shutdown(app_handle);
                shutdown_backend(app_handle);
                // A normal quit and the updater's relaunch both pass here: the next launch must
                // not ask about a crash. (A kill or crash never does.)
                if let Some(marker) = app_handle.try_state::<session_marker::SessionMarker>() {
                    marker.end();
                }
            }
        }),
        Err(error) => {
            eprintln!("error while building SSU Lecture Downloader Tauri app: {error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{folder_to_open, real_download_dir};
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn real_download_dir_uses_login_user_downloads_not_hermes_profile_home() {
        let downloads = real_download_dir().expect("downloads path");
        assert!(downloads.ends_with("/Downloads"), "{downloads}");
        assert!(!downloads.contains("/.hermes/profiles/"), "{downloads}");
    }

    #[test]
    fn folder_to_open_uses_parent_when_given_downloaded_file_path() {
        let root = std::env::temp_dir().join(format!(
            "ssu-lecture-downloader-test-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create temp dir");
        let file = root.join("lecture.mp4");
        fs::write(&file, b"test").expect("write temp file");

        assert_eq!(folder_to_open(&file), root);

        fs::remove_file(&file).ok();
        fs::remove_dir_all(root).ok();
    }
}
