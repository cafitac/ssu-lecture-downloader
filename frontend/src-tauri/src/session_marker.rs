//! Tells the next launch whether the previous run ended abnormally.
//!
//! At startup the shell writes a marker file into the app data dir; a clean exit (quit, or the
//! updater's relaunch, which also goes through `RunEvent::Exit`) removes it. A marker that is
//! already there at startup means the previous run was killed, crashed or lost power. The result
//! is read once by the frontend (`previous_run_crashed`), which asks the user about it.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

const MARKER_FILE: &str = "session.marker";

/// Managed state: the marker's folder and whether the previous run crashed (until it is read).
pub(crate) struct SessionMarker {
    dir: PathBuf,
    previous_crashed: AtomicBool,
}

fn marker_path(dir: &Path) -> PathBuf {
    dir.join(MARKER_FILE)
}

impl SessionMarker {
    /// Looks for the previous run's marker, then writes this run's. A marker that cannot be
    /// written only means a later crash goes unnoticed.
    pub(crate) fn begin(dir: PathBuf) -> Self {
        let path = marker_path(&dir);
        let previous_crashed = path.exists();
        let _ = fs::create_dir_all(&dir);
        let _ = fs::write(&path, std::process::id().to_string());
        Self {
            dir,
            previous_crashed: AtomicBool::new(previous_crashed),
        }
    }

    /// Reads and clears the flag: `true` only for the first call after a crashed run.
    pub(crate) fn take_previous_crashed(&self) -> bool {
        self.previous_crashed.swap(false, Ordering::SeqCst)
    }

    /// The run is ending normally.
    pub(crate) fn end(&self) {
        let _ = fs::remove_file(marker_path(&self.dir));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(tag: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "ssu-session-marker-{tag}-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ))
    }

    #[test]
    fn a_clean_exit_leaves_no_prompt_for_the_next_launch() {
        let dir = temp_dir("clean");
        let first = SessionMarker::begin(dir.clone());
        assert!(
            !first.take_previous_crashed(),
            "nothing before the first run"
        );
        assert!(
            marker_path(&dir).exists(),
            "the running app leaves a marker"
        );
        first.end();
        assert!(!marker_path(&dir).exists());
        let second = SessionMarker::begin(dir.clone());
        assert!(!second.take_previous_crashed());
        second.end();
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_crash_is_reported_once_by_the_next_launch() {
        let dir = temp_dir("crash");
        // The first run never calls `end`: killed, crashed, force-quit.
        let _killed = SessionMarker::begin(dir.clone());
        let next = SessionMarker::begin(dir.clone());
        assert!(next.take_previous_crashed(), "the crash is reported");
        assert!(!next.take_previous_crashed(), "and only once");
        next.end();
        // The run after a clean one is quiet again.
        assert!(!SessionMarker::begin(dir.clone()).take_previous_crashed());
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn an_unwritable_folder_never_panics_or_reports_a_crash() {
        let dir = temp_dir("nowrite");
        fs::write(&dir, "a file where the folder should be").unwrap();
        let marker = SessionMarker::begin(dir.join("sub"));
        assert!(!marker.take_previous_crashed());
        marker.end();
        fs::remove_file(dir).ok();
    }
}
