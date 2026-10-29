//! In-app update support on the Rust side: the install guard and the sidecar stop that must
//! happen before the updater plugin swaps the app. Nothing here starts an update; the
//! frontend decides when to check, download and install (design/05 install flow, 06 states).
//!
//! Two commands, both for the main window only:
//!
//! - `update_install_guard`: may an update be installed now? Refused while a sequential watch
//!   (the app's own queue or an external run) or a native download is running. Stops nothing.
//! - `prepare_for_update`: latches "update in progress", re-checks the guard, then stops the
//!   backend sidecar so the new version can replace the app. While the latch is set the backend
//!   cannot be (re)started, and native download jobs and watch queues refuse to start
//!   (`UPDATE_IN_PROGRESS`), so nothing can slip in between the guard and the install.
//! - `resume_after_failed_update`: the install failed (or was abandoned): clear the latch and
//!   restart the backend. The frontend calls this, not `backend_restart` (which refuses while
//!   the latch is set), in the Failed state of design/06.
//!
//! Platform-neutral: only `BackendManager` (which calls `platform::terminate_process_group`)
//! touches the OS, so the Windows `cargo check --lib` job compiles this unchanged.

use std::sync::Arc;

use serde::Serialize;
use tauri::{AppHandle, Manager, Runtime};

use crate::backend::BackendManager;
use crate::native_download::NativeDownloadJobs;
use crate::watch_runtime;

/// Error code of every start (backend restart, native download, watch queue) refused because an
/// update is being installed. The frontend maps it to Korean copy (`src/lib/errors.ts`).
pub(crate) const UPDATE_IN_PROGRESS: &str = "update_in_progress";

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
    /// Latch "update in progress" (atomically refusing while a backend startup runs).
    fn begin_update(&self) -> Result<(), PrepareError>;
    /// Release the latch (the guard refused).
    fn cancel_update(&self);
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

/// Latch first, so nothing new can start; then the guard; then the stop. The sidecar is stopped
/// only after the guard allowed, and the latch is released again when the guard refuses.
pub(crate) fn prepare_with(host: &impl UpdateHost) -> Result<(), PrepareError> {
    host.begin_update()?;
    if let InstallGuard::Refused { reason } = guard_for(host) {
        host.cancel_update();
        return Err(PrepareError::Refused { reason });
    }
    match host.stop_backend() {
        Ok(()) => {
            eprintln!("updater: backend sidecar stopped for the update");
            Ok(())
        }
        Err(error) => {
            // Nothing was stopped; do not leave the app latched.
            host.cancel_update();
            eprintln!("updater: could not stop the backend sidecar ({error:?})");
            Err(error)
        }
    }
}

/// Whether an update is being installed (false when there is no backend manager, as in tests).
pub(crate) fn is_update_in_progress<R: Runtime>(app: &AppHandle<R>) -> bool {
    app.try_state::<Arc<BackendManager>>()
        .is_some_and(|manager| manager.update_in_progress())
}

pub(crate) struct AppHost<'a, R: Runtime> {
    pub(crate) app: &'a AppHandle<R>,
}

impl<R: Runtime> AppHost<'_, R> {
    fn manager(&self) -> Result<tauri::State<'_, Arc<BackendManager>>, PrepareError> {
        self.app
            .try_state::<Arc<BackendManager>>()
            .ok_or(PrepareError::BackendUnavailable)
    }
}

impl<R: Runtime> UpdateHost for AppHost<'_, R> {
    fn watch_active(&self) -> bool {
        // Covers the app's own queue and an external run.
        watch_runtime::is_queue_active(self.app)
    }

    fn download_running(&self) -> bool {
        self.app
            .try_state::<NativeDownloadJobs>()
            .is_some_and(|jobs| jobs.has_running_job())
    }

    fn begin_update(&self) -> Result<(), PrepareError> {
        self.manager()?
            .begin_update()
            .map_err(|_| PrepareError::BackendStarting)
    }

    fn cancel_update(&self) {
        if let Ok(manager) = self.manager() {
            manager.cancel_update();
        }
    }

    fn stop_backend(&self) -> Result<(), PrepareError> {
        self.manager()?
            .stop_for_update()
            .map_err(|_| PrepareError::BackendStarting)
    }
}

#[tauri::command]
pub(crate) fn update_install_guard(app: AppHandle) -> InstallGuard {
    guard_for(&AppHost { app: &app })
}

/// The blocking stop (SIGTERM grace wait) runs on the blocking pool, not on a runtime thread.
#[tauri::command]
pub(crate) async fn prepare_for_update(app: AppHandle) -> Result<(), PrepareError> {
    tauri::async_runtime::spawn_blocking(move || prepare_with(&AppHost { app: &app }))
        .await
        .unwrap_or(Err(PrepareError::BackendUnavailable))
}

/// The install failed (or was abandoned): clear the update latch and restart the backend, so the
/// old app keeps working. Returns immediately; the respawn runs on the supervisor thread.
#[tauri::command]
pub(crate) fn resume_after_failed_update(
    state: tauri::State<'_, Arc<BackendManager>>,
) -> Result<(), String> {
    eprintln!("updater: resuming the backend after a failed install");
    // `false` only when a startup is already running, which is the state we want anyway.
    Arc::clone(&state).resume_after_failed_update();
    Ok(())
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
        begin_result: Result<(), PrepareError>,
        calls: RefCell<Vec<&'static str>>,
    }

    impl FakeHost {
        fn new(watch: bool, download: bool) -> Self {
            Self {
                watch,
                download,
                stop_result: Ok(()),
                begin_result: Ok(()),
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
        fn begin_update(&self) -> Result<(), PrepareError> {
            self.calls.borrow_mut().push("begin_update");
            self.begin_result.clone()
        }
        fn cancel_update(&self) {
            self.calls.borrow_mut().push("cancel_update");
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
            assert_eq!(
                host.calls.borrow().last(),
                Some(&"cancel_update"),
                "a refusal releases the latch"
            );
        }
    }

    #[test]
    fn the_sidecar_is_stopped_only_after_the_guard_allowed() {
        let host = FakeHost::new(false, false);
        assert_eq!(prepare_with(&host), Ok(()));
        assert_eq!(
            *host.calls.borrow(),
            [
                "begin_update",
                "watch_active",
                "download_running",
                "stop_backend"
            ],
            "latch, guard checks, then the stop"
        );
    }

    #[test]
    fn a_stop_failure_is_reported_and_releases_the_latch() {
        let mut host = FakeHost::new(false, false);
        host.stop_result = Err(PrepareError::BackendStarting);
        assert_eq!(prepare_with(&host), Err(PrepareError::BackendStarting));
        assert_eq!(host.calls.borrow().last(), Some(&"cancel_update"));
    }

    #[test]
    fn a_failed_latch_checks_nothing_and_stops_nothing() {
        let mut host = FakeHost::new(false, false);
        host.begin_result = Err(PrepareError::BackendStarting);
        assert_eq!(prepare_with(&host), Err(PrepareError::BackendStarting));
        assert_eq!(*host.calls.borrow(), ["begin_update"]);
    }

    // The real wiring (`AppHost`) against a mock app that manages the real state.

    use crate::native_download::NativeDownloadJobs;
    use tauri::test::{mock_app, MockRuntime};

    fn mock_app_with_backend() -> (tauri::App<MockRuntime>, Arc<BackendManager>) {
        let app = mock_app();
        let root = std::env::temp_dir().join("ssu-update-wiring");
        let manager = Arc::new(BackendManager::new(
            root.join("project"),
            None,
            root.join("logs"),
            None,
        ));
        app.manage(Arc::clone(&manager));
        app.manage(NativeDownloadJobs::new());
        (app, manager)
    }

    #[test]
    fn a_running_native_download_makes_the_real_guard_refuse() {
        let (app, _manager) = mock_app_with_backend();
        let host = AppHost { app: app.handle() };
        assert_eq!(guard_for(&host), InstallGuard::Allowed);

        let jobs = app.state::<NativeDownloadJobs>();
        jobs.create_queued_for_test();
        assert_eq!(
            guard_for(&host),
            InstallGuard::Refused {
                reason: RefuseReason::DownloadActive
            }
        );
        assert_eq!(
            prepare_with(&host),
            Err(PrepareError::Refused {
                reason: RefuseReason::DownloadActive
            })
        );
        assert!(
            !is_update_in_progress(app.handle()),
            "a refused prepare leaves no latch behind"
        );
    }

    #[test]
    fn prepare_latches_the_real_backend_and_resume_clears_it() {
        let (app, manager) = mock_app_with_backend();
        let host = AppHost { app: app.handle() };
        assert!(!is_update_in_progress(app.handle()));
        assert_eq!(prepare_with(&host), Ok(()));
        assert!(is_update_in_progress(app.handle()));
        assert!(
            !manager.restart(),
            "backend_restart is refused while updating"
        );

        manager.cancel_update();
        assert!(!is_update_in_progress(app.handle()));
    }

    /// The start commands take an `AppHandle`, so they cannot run in the mock app; this pins
    /// that each one consults the update latch (watch: before and after becoming active).
    #[test]
    fn every_start_command_consults_the_update_latch() {
        let watch = include_str!("watch_runtime.rs");
        let start = watch
            .split("pub(crate) fn watch_queue_start(")
            .nth(1)
            .expect("start");
        let start = start
            .split("pub(crate) fn watch_queue_next")
            .next()
            .expect("body");
        assert_eq!(
            start.matches("is_update_in_progress(&app)").count(),
            2,
            "before and after"
        );
        let native = include_str!("native_download.rs");
        for command in [
            "native_start_download_job",
            "native_start_batch_download_job",
        ] {
            let body = native
                .split(&format!("pub(crate) fn {command}("))
                .nth(1)
                .expect(command);
            let body = body.split("#[tauri::command]").next().expect("body");
            assert!(
                body.contains("create_and_snapshot_unless_updating"),
                "{command}"
            );
        }
        let backend = include_str!("backend.rs");
        assert!(
            backend.contains("if state.update_in_progress() {"),
            "backend_restart"
        );
    }
}
