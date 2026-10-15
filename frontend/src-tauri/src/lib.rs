mod backend;
mod native_download;
mod viewer;

use std::ffi::CStr;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use tauri::Manager;

use backend::BackendManager;

#[tauri::command]
fn real_download_dir() -> Result<String, String> {
    real_user_home_dir()
        .map(|home| home.join("Downloads").to_string_lossy().to_string())
        .ok_or_else(|| "Could not resolve the macOS user Downloads folder".to_string())
}

#[cfg(target_os = "macos")]
fn real_user_home_dir() -> Option<PathBuf> {
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
fn real_user_home_dir() -> Option<PathBuf> {
    fallback_home_dir()
}

fn fallback_home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
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
    Command::new("open")
        .arg(&folder)
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

#[tauri::command]
fn open_url_in_browser(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("공식 시청 주소가 올바르지 않습니다.".to_string());
    }
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

fn shutdown_backend(app: &tauri::AppHandle) {
    if let Some(manager) = app.try_state::<Arc<BackendManager>>() {
        manager.shutdown();
    }
}

pub fn run() {
    let project_root =
        backend::project_root_from_manifest_dir(Path::new(env!("CARGO_MANIFEST_DIR")));
    let built = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
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
            manager.start_in_background();
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            real_download_dir,
            open_path_in_finder,
            open_url_in_browser,
            viewer::open_assisted_official_viewer,
            viewer::open_assisted_official_viewer_with_session,
            viewer::open_official_viewer_with_session,
            backend::backend_supervision_status,
            backend::backend_api_base_url,
            backend::backend_api_connection,
            backend::backend_restart,
            native_download::native_start_download_job,
            native_download::native_start_batch_download_job,
            native_download::native_download_job_status,
            native_download::native_cancel_download_job
        ])
        .build(tauri::generate_context!());
    match built {
        Ok(app) => app.run(|app_handle, event| {
            // Kill the managed backend's whole process group on every exit path;
            // `shutdown` is idempotent.
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                shutdown_backend(app_handle);
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
