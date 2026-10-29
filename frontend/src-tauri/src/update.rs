//! In-app update support on the Rust side: the install guard and the sidecar stop that must
//! happen before the updater plugin swaps the app. Nothing here starts an update; the
//! frontend decides when to check, download and install (design/05 install flow, 06 states).
//!
//! Two commands here, both for the main window only:
//!
//! - `update_install_guard`: may an update be installed now? Refused while a sequential watch
//!   (the app's own queue or an external run) or a native download is running. Stops nothing.
//! - `prepare_for_update`: latches "update in progress", re-checks the guard, then stops the
//!   backend sidecar so the new version can replace the app. While the latch is set the backend
//!   cannot be (re)started, and native download jobs and watch queues refuse to start
//!   (`UPDATE_IN_PROGRESS`), so nothing can slip in between the guard and the install.
//!
//! Recovery is the existing `backend_restart` (design 05/06/07): it clears the latch and
//! restarts the backend, so the Failed state of an install brings the old app back. The
//! frontend must not offer a restart while an install is in progress.
//!
//! Platform-neutral: only `BackendManager` (which calls `platform::terminate_process_group`)
//! touches the OS, so the Windows `cargo check --lib` job compiles this unchanged.

use std::sync::{Arc, Mutex};

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
    /// Latch "update in progress" (atomically refusing while a backend startup runs). `Ok(true)`
    /// when this call acquired the latch, `Ok(false)` when it was already set.
    fn begin_update(&self) -> Result<bool, PrepareError>;
    /// Release the latch (only the call that acquired it, when the guard refuses).
    fn cancel_update(&self);
    /// Held for the whole of `prepare_with`, so prepares run one at a time.
    fn prepare_lock(&self) -> &Mutex<()>;
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
///
/// Invariant: `Ok` means the guard passed under the latch and the sidecar is stopped.
///
/// Prepares are serialized by `prepare_lock`, so a second prepare always sees the first one's
/// final outcome: latch still held means the first one really prepared (the second returns Ok
/// at once, without re-running the guard); latch released means the first one refused or failed,
/// and the second acquires the latch itself and runs the guard and the stop. Only the call that
/// acquired the latch releases it.
pub(crate) fn prepare_with(host: &impl UpdateHost) -> Result<(), PrepareError> {
    let _serialized = host
        .prepare_lock()
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if !host.begin_update()? {
        eprintln!("updater: update already prepared; keeping the latch");
        return Ok(());
    }
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

/// Used when there is no backend manager (nothing to prepare then anyway).
static NO_MANAGER_LOCK: Mutex<()> = Mutex::new(());

pub(crate) struct AppHost<'a, R: Runtime> {
    app: &'a AppHandle<R>,
    manager: Option<Arc<BackendManager>>,
}

impl<'a, R: Runtime> AppHost<'a, R> {
    pub(crate) fn new(app: &'a AppHandle<R>) -> Self {
        let manager = app
            .try_state::<Arc<BackendManager>>()
            .map(|state| Arc::clone(&state));
        Self { app, manager }
    }

    fn manager(&self) -> Result<&Arc<BackendManager>, PrepareError> {
        self.manager
            .as_ref()
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

    fn begin_update(&self) -> Result<bool, PrepareError> {
        self.manager()?
            .begin_update()
            .map_err(|_| PrepareError::BackendStarting)
    }

    fn cancel_update(&self) {
        if let Ok(manager) = self.manager() {
            manager.cancel_update();
        }
    }

    fn prepare_lock(&self) -> &Mutex<()> {
        self.manager
            .as_ref()
            .map_or(&NO_MANAGER_LOCK, |manager| manager.prepare_lock())
    }

    fn stop_backend(&self) -> Result<(), PrepareError> {
        self.manager()?
            .stop_for_update()
            .map_err(|_| PrepareError::BackendStarting)
    }
}

#[tauri::command]
pub(crate) fn update_install_guard(app: AppHandle) -> InstallGuard {
    guard_for(&AppHost::new(&app))
}

/// The blocking stop (SIGTERM grace wait) runs on the blocking pool, not on a runtime thread.
#[tauri::command]
pub(crate) async fn prepare_for_update(app: AppHandle) -> Result<(), PrepareError> {
    tauri::async_runtime::spawn_blocking(move || prepare_with(&AppHost::new(&app)))
        .await
        .unwrap_or(Err(PrepareError::BackendUnavailable))
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
        begin_result: Result<bool, PrepareError>,
        calls: RefCell<Vec<&'static str>>,
        lock: Mutex<()>,
    }

    impl FakeHost {
        fn new(watch: bool, download: bool) -> Self {
            Self {
                watch,
                download,
                stop_result: Ok(()),
                begin_result: Ok(true),
                calls: RefCell::new(Vec::new()),
                lock: Mutex::new(()),
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
        fn begin_update(&self) -> Result<bool, PrepareError> {
            self.calls.borrow_mut().push("begin_update");
            self.begin_result.clone()
        }
        fn cancel_update(&self) {
            self.calls.borrow_mut().push("cancel_update");
        }
        fn prepare_lock(&self) -> &Mutex<()> {
            &self.lock
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
    fn a_second_prepare_keeps_the_first_latch() {
        // The latch is already held (the first prepare stopped the sidecar). Even though the
        // guard would refuse now, the second prepare neither re-checks nor cancels.
        let mut host = FakeHost::new(true, false);
        host.begin_result = Ok(false);
        assert_eq!(prepare_with(&host), Ok(()));
        assert_eq!(*host.calls.borrow(), ["begin_update"]);

        // Same with the real manager: the first prepare latches and stops, the second keeps it.
        let (app, manager) = mock_app_with_backend();
        let real = AppHost::new(app.handle());
        assert_eq!(prepare_with(&real), Ok(()));
        assert_eq!(prepare_with(&real), Ok(()));
        assert!(manager.update_in_progress(), "the first latch survives");
    }

    /// A host shared by two threads, with a real latch like the manager's. Its first guard check
    /// parks inside the guard until the test releases it, so a second prepare overlaps it.
    struct OverlapHost {
        latch: std::sync::atomic::AtomicBool,
        lock: Mutex<()>,
        /// The owner is inside the guard (sent once).
        entered_guard: std::sync::mpsc::Sender<()>,
        /// The test lets the owner's guard finish (taken once, by the first guard check).
        release_guard: Mutex<Option<std::sync::mpsc::Receiver<()>>>,
        /// Any later `begin_update` call (the second prepare reached the latch).
        later_begin: std::sync::mpsc::Sender<()>,
        begins: std::sync::atomic::AtomicUsize,
        stops: std::sync::atomic::AtomicUsize,
    }

    impl UpdateHost for OverlapHost {
        fn watch_active(&self) -> bool {
            let release = self.release_guard.lock().unwrap().take();
            if let Some(release) = release {
                self.entered_guard.send(()).unwrap();
                release.recv().unwrap();
            }
            true // a watch is running: every guard check refuses
        }
        fn download_running(&self) -> bool {
            false
        }
        fn begin_update(&self) -> Result<bool, PrepareError> {
            use std::sync::atomic::Ordering::SeqCst;
            if self.begins.fetch_add(1, SeqCst) > 0 {
                let _ = self.later_begin.send(());
            }
            Ok(!self.latch.swap(true, SeqCst))
        }
        fn cancel_update(&self) {
            self.latch.store(false, std::sync::atomic::Ordering::SeqCst);
        }
        fn prepare_lock(&self) -> &Mutex<()> {
            &self.lock
        }
        fn stop_backend(&self) -> Result<(), PrepareError> {
            self.stops.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Ok(())
        }
    }

    #[test]
    fn a_prepare_overlapping_a_refusing_prepare_does_not_return_ok() {
        use std::sync::atomic::Ordering::SeqCst;
        use std::sync::mpsc::channel;
        use std::time::Duration;
        let (entered_tx, entered_rx) = channel();
        let (release_tx, release_rx) = channel();
        let (later_tx, later_rx) = channel();
        let host = Arc::new(OverlapHost {
            latch: std::sync::atomic::AtomicBool::new(false),
            lock: Mutex::new(()),
            entered_guard: entered_tx,
            release_guard: Mutex::new(Some(release_rx)),
            later_begin: later_tx,
            begins: std::sync::atomic::AtomicUsize::new(0),
            stops: std::sync::atomic::AtomicUsize::new(0),
        });

        let first = {
            let host = Arc::clone(&host);
            std::thread::spawn(move || prepare_with(&*host))
        };
        // The owner holds the latch and is parked inside its guard check.
        entered_rx.recv().unwrap();
        assert!(host.latch.load(SeqCst));

        let second = {
            let host = Arc::clone(&host);
            std::thread::spawn(move || prepare_with(&*host))
        };
        // Serialized, the second prepare cannot reach the latch while the first is parked. (If
        // it did, it would see the latch held and report Ok before the first one refuses.)
        let overlapped = later_rx.recv_timeout(Duration::from_millis(300)).is_ok();
        release_tx.send(()).unwrap();

        let refused = Err(PrepareError::Refused {
            reason: RefuseReason::WatchActive,
        });
        assert_eq!(first.join().unwrap(), refused);
        assert_eq!(
            second.join().unwrap(),
            refused,
            "the second prepare must see the first one's outcome, not an early Ok"
        );
        assert!(!overlapped, "the second prepare overlapped the first");
        assert_eq!(host.stops.load(SeqCst), 0, "nothing was ever stopped");
        assert!(
            !host.latch.load(SeqCst),
            "both refused: no latch left behind"
        );
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
        let host = AppHost::new(app.handle());
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
    fn prepare_latches_the_real_backend_and_backend_restart_recovers() {
        let (app, manager) = mock_app_with_backend();
        let host = AppHost::new(app.handle());
        assert!(!is_update_in_progress(app.handle()));
        assert_eq!(prepare_with(&host), Ok(()));
        assert!(is_update_in_progress(app.handle()));
        assert!(
            !manager.restart(),
            "plain starts are refused while updating"
        );

        // `backend_restart` (restart_clearing_update) is the recovery of a failed install.
        let _ = manager
            .restart_clearing_update()
            .expect("no prepare is running");
        assert!(!is_update_in_progress(app.handle()));
        manager.shutdown();
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
            backend.contains("Arc::clone(&state).restart_clearing_update()?;"),
            "backend_restart recovers a failed install"
        );
    }
}
