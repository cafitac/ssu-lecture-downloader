//! In-app update support on the Rust side: the install guard and the sidecar stop that must
//! happen before the updater plugin swaps the app. Nothing here starts an update; the
//! frontend decides when to check, download and install (design/05 install flow, 06 states).
//!
//! Two commands, both for the main window only:
//!
//! - `update_install_guard`: may an update be installed now? Refused while a sequential watch
//!   (the app's own queue or an external run) or a native download is running. Stops nothing.
//! - `prepare_for_update`: re-checks the guard, then stops the backend sidecar so the new
//!   version can replace the app. If the install then fails, the frontend calls the existing
//!   `backend_restart` command (the stop keeps the backend restartable).
//!
//! Platform-neutral: only `BackendManager` (which calls `platform::terminate_process_group`)
//! touches the OS, so the Windows `cargo check --lib` job compiles this unchanged.

use std::sync::Arc;

use serde::Serialize;
use tauri::{AppHandle, Manager};

use crate::backend::BackendManager;
use crate::native_download::NativeDownloadJobs;
use crate::watch_runtime;

/// Why an update must wait. Serialized as the snake_case name; the frontend maps it to copy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum RefuseReason {
    /// The sequential watch is running (the app's own queue or an external run).
    WatchActive,
    /// A native download job is still running.
    DownloadActive,
}

/// Result of `update_install_guard`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub(crate) enum InstallGuard {
    Allowed,
    Refused { reason: RefuseReason },
}

/// Error of `prepare_for_update`. Nothing was stopped for any of these.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "code", rename_all = "snake_case")]
pub(crate) enum PrepareError {
    /// The guard refused (it changed state since the frontend asked).
    Refused { reason: RefuseReason },
    /// The backend is still starting, so stopping it now would race its startup.
    BackendStarting,
    /// The backend manager is not available.
    BackendUnavailable,
}

/// What the guard and the preparation need to know about or do to the running app.
pub(crate) trait UpdateHost {
    fn watch_active(&self) -> bool;
    fn download_running(&self) -> bool;
    fn stop_backend(&self) -> Result<(), PrepareError>;
}

/// The guard table. Watch wins over download when both hold (the more disruptive one is
/// reported).
pub(crate) fn evaluate_guard(watch_active: bool, download_running: bool) -> InstallGuard {
    if watch_active {
        InstallGuard::Refused {
            reason: RefuseReason::WatchActive,
        }
    } else if download_running {
        InstallGuard::Refused {
            reason: RefuseReason::DownloadActive,
        }
    } else {
        InstallGuard::Allowed
    }
}

pub(crate) fn guard_for(host: &impl UpdateHost) -> InstallGuard {
    let guard = evaluate_guard(host.watch_active(), host.download_running());
    match guard {
        InstallGuard::Allowed => eprintln!("updater: install guard allowed"),
        InstallGuard::Refused { reason } => {
            eprintln!("updater: install guard refused ({reason:?})")
        }
    }
    guard
}

/// Guard first, then stop the sidecar. The sidecar is stopped only after the guard allowed.
pub(crate) fn prepare_with(host: &impl UpdateHost) -> Result<(), PrepareError> {
    if let InstallGuard::Refused { reason } = guard_for(host) {
        return Err(PrepareError::Refused { reason });
    }
    match host.stop_backend() {
        Ok(()) => {
            eprintln!("updater: backend sidecar stopped for the update");
            Ok(())
        }
        Err(error) => {
            eprintln!("updater: could not stop the backend sidecar ({error:?})");
            Err(error)
        }
    }
}

struct AppHost<'a> {
    app: &'a AppHandle,
}

impl UpdateHost for AppHost<'_> {
    fn watch_active(&self) -> bool {
        // Covers the app's own queue and an external run.
        watch_runtime::is_queue_active(self.app)
    }

    fn download_running(&self) -> bool {
        self.app
            .try_state::<NativeDownloadJobs>()
            .is_some_and(|jobs| jobs.has_running_job())
    }

    fn stop_backend(&self) -> Result<(), PrepareError> {
        let manager = self
            .app
            .try_state::<Arc<BackendManager>>()
            .ok_or(PrepareError::BackendUnavailable)?;
        manager
            .stop_for_update()
            .map_err(|_| PrepareError::BackendStarting)
    }
}

#[tauri::command]
pub(crate) fn update_install_guard(app: AppHandle) -> InstallGuard {
    guard_for(&AppHost { app: &app })
}

/// `async` so the sidecar's SIGTERM grace wait does not run on the main thread.
#[tauri::command]
pub(crate) async fn prepare_for_update(app: AppHandle) -> Result<(), PrepareError> {
    prepare_with(&AppHost { app: &app })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    /// A host that records the order of the calls made to it.
    struct FakeHost {
        watch: bool,
        download: bool,
        stop_result: Result<(), PrepareError>,
        calls: RefCell<Vec<&'static str>>,
    }

    impl FakeHost {
        fn new(watch: bool, download: bool) -> Self {
            Self {
                watch,
                download,
                stop_result: Ok(()),
                calls: RefCell::new(Vec::new()),
            }
        }
    }

    impl UpdateHost for FakeHost {
        fn watch_active(&self) -> bool {
            self.calls.borrow_mut().push("watch_active");
            self.watch
        }
        fn download_running(&self) -> bool {
            self.calls.borrow_mut().push("download_running");
            self.download
        }
        fn stop_backend(&self) -> Result<(), PrepareError> {
            self.calls.borrow_mut().push("stop_backend");
            self.stop_result.clone()
        }
    }

    #[test]
    fn the_guard_table() {
        use InstallGuard::{Allowed, Refused};
        use RefuseReason::{DownloadActive, WatchActive};
        for (watch, download, expected) in [
            (false, false, Allowed),
            (
                true,
                false,
                Refused {
                    reason: WatchActive,
                },
            ),
            (
                false,
                true,
                Refused {
                    reason: DownloadActive,
                },
            ),
            (
                true,
                true,
                Refused {
                    reason: WatchActive,
                },
            ),
        ] {
            assert_eq!(
                evaluate_guard(watch, download),
                expected,
                "{watch} {download}"
            );
        }
    }

    #[test]
    fn guard_and_error_shapes_are_stable_for_the_frontend() {
        assert_eq!(
            serde_json::to_value(InstallGuard::Allowed).unwrap(),
            serde_json::json!({ "status": "allowed" })
        );
        assert_eq!(
            serde_json::to_value(InstallGuard::Refused {
                reason: RefuseReason::WatchActive
            })
            .unwrap(),
            serde_json::json!({ "status": "refused", "reason": "watch_active" })
        );
        assert_eq!(
            serde_json::to_value(InstallGuard::Refused {
                reason: RefuseReason::DownloadActive
            })
            .unwrap(),
            serde_json::json!({ "status": "refused", "reason": "download_active" })
        );
        assert_eq!(
            serde_json::to_value(PrepareError::Refused {
                reason: RefuseReason::DownloadActive
            })
            .unwrap(),
            serde_json::json!({ "code": "refused", "reason": "download_active" })
        );
        assert_eq!(
            serde_json::to_value(PrepareError::BackendStarting).unwrap(),
            serde_json::json!({ "code": "backend_starting" })
        );
    }

    #[test]
    fn a_refused_guard_stops_nothing() {
        for (watch, download, reason) in [
            (true, false, RefuseReason::WatchActive),
            (false, true, RefuseReason::DownloadActive),
        ] {
            let host = FakeHost::new(watch, download);
            assert_eq!(prepare_with(&host), Err(PrepareError::Refused { reason }));
            assert!(
                !host.calls.borrow().contains(&"stop_backend"),
                "the sidecar must not be stopped while busy"
            );
        }
    }

    #[test]
    fn the_sidecar_is_stopped_only_after_the_guard_allowed() {
        let host = FakeHost::new(false, false);
        assert_eq!(prepare_with(&host), Ok(()));
        assert_eq!(
            *host.calls.borrow(),
            ["watch_active", "download_running", "stop_backend"],
            "guard checks, then the stop"
        );
    }

    #[test]
    fn a_stop_failure_is_reported_to_the_caller() {
        let mut host = FakeHost::new(false, false);
        host.stop_result = Err(PrepareError::BackendStarting);
        assert_eq!(prepare_with(&host), Err(PrepareError::BackendStarting));
    }
}
