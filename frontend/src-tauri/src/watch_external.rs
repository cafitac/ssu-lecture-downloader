//! Auto playback that runs outside this app (the dedicated Chrome window driven by the
//! backend): the pure part of mirroring it into the menu bar, Dock badge and notifications.
//!
//! The React app polls the backend and hands every status to the shell as an
//! [`ExternalWatchView`]; this module turns it into the same [`Snapshot`] the in-app queue
//! produces, so the tray text, menu rows, badge and notification texts come from the one set of
//! functions in `watch_queue.rs` and look identical in both modes.

use crate::watch_queue::{finished_notification, skipped_notification, ItemSnapshot, Snapshot};
use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct ExternalItem {
    #[serde(default)]
    pub module_item_id: String,
    #[serde(default)]
    pub title: String,
    /// `pending`, `playing`, `done` or `skipped`.
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub duration_secs: Option<f64>,
}

/// A thing the backend wants the user to hear about (ids grow within one run).
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct ExternalNotice {
    pub id: u64,
    /// `finished`, `skipped`, `stalled` or `failed`.
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub item_title: Option<String>,
    #[serde(default)]
    pub reason: Option<String>,
}

/// The queue state as the React app reads it from the backend (same shape as its
/// `WatchQueueState`), plus the run it belongs to and the notices so far.
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct ExternalWatchView {
    #[serde(default)]
    pub run_id: String,
    pub active: bool,
    /// `idle`, `opening`, `playing`, `stalled` or `finished`.
    #[serde(default)]
    pub phase: String,
    #[serde(default)]
    pub index: usize,
    #[serde(default)]
    pub total: usize,
    #[serde(default)]
    pub done_count: usize,
    #[serde(default)]
    pub skipped_count: usize,
    #[serde(default)]
    pub current_module_item_id: Option<String>,
    #[serde(default)]
    pub current_title: Option<String>,
    #[serde(default)]
    pub position_secs: Option<f64>,
    #[serde(default)]
    pub duration_secs: Option<f64>,
    #[serde(default)]
    pub remaining_secs: Option<f64>,
    #[serde(default)]
    pub total_remaining_secs: f64,
    #[serde(default)]
    pub paused: bool,
    #[serde(default)]
    pub items: Vec<ExternalItem>,
    #[serde(default)]
    pub notices: Vec<ExternalNotice>,
}

fn phase_name(phase: &str) -> &'static str {
    match phase {
        "opening" => "opening",
        "playing" => "playing",
        "stalled" => "stalled",
        "finished" => "finished",
        _ => "idle",
    }
}

fn item_state_name(state: &str) -> &'static str {
    match state {
        "playing" => "playing",
        "done" => "done",
        "skipped" => "skipped",
        _ => "pending",
    }
}

fn finite(value: Option<f64>) -> Option<f64> {
    value.filter(|number| number.is_finite() && *number >= 0.0)
}

impl ExternalWatchView {
    /// The snapshot the tray/menu/badge functions read. The Chrome window is never "covered" as
    /// far as this app can tell, so `rendering` stays true.
    pub fn to_snapshot(&self) -> Snapshot {
        let phase = phase_name(&self.phase);
        Snapshot {
            active: self.active && phase != "finished" && phase != "idle",
            phase,
            index: self.index,
            total: self.total,
            done_count: self.done_count,
            skipped_count: self.skipped_count,
            current_module_item_id: self.current_module_item_id.clone(),
            current_title: self.current_title.clone(),
            position_secs: finite(self.position_secs),
            duration_secs: finite(self.duration_secs),
            remaining_secs: finite(self.remaining_secs),
            total_remaining_secs: if self.total_remaining_secs.is_finite() {
                self.total_remaining_secs.max(0.0)
            } else {
                0.0
            },
            paused: self.paused,
            rendering: true,
            items: self
                .items
                .iter()
                .map(|item| ItemSnapshot {
                    module_item_id: item.module_item_id.clone(),
                    title: item.title.clone(),
                    state: item_state_name(&item.state),
                    reason: item.reason.clone(),
                    duration_secs: finite(item.duration_secs),
                })
                .collect(),
        }
    }
}

/// Notifications for the notices newer than `last_id`, with the new high-water mark.
pub(crate) fn pending_notifications(
    snapshot: &Snapshot,
    notices: &[ExternalNotice],
    last_id: u64,
) -> (Vec<(String, String)>, u64) {
    let mut fresh: Vec<&ExternalNotice> = notices
        .iter()
        .filter(|notice| notice.id > last_id)
        .collect();
    fresh.sort_by_key(|notice| notice.id);
    let newest = fresh.last().map_or(last_id, |notice| notice.id);
    let texts = fresh
        .into_iter()
        .filter_map(|notice| match notice.kind.as_str() {
            "finished" => Some(finished_notification(snapshot)),
            "skipped" => {
                let (title, short) = skipped_notification(
                    notice.item_title.as_deref().unwrap_or("강의"),
                    notice.reason.as_deref().unwrap_or(&notice.text),
                );
                // The backend's sentence also says how to get playback going again.
                let body = if notice.text.trim().is_empty() {
                    short
                } else {
                    notice.text.clone()
                };
                Some((title, body))
            }
            "stalled" => Some(("자동 재생이 멈췄어요".to_string(), notice.text.clone())),
            "failed" => Some(("자동 재생을 멈췄어요".to_string(), notice.text.clone())),
            _ => None,
        })
        .collect();
    (texts, newest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::watch_queue::{dock_badge, tray_menu_text, tray_title};

    fn view(json: serde_json::Value) -> ExternalWatchView {
        serde_json::from_value(json).expect("view")
    }

    fn playing() -> ExternalWatchView {
        view(serde_json::json!({
            "run_id": "r1",
            "active": true,
            "phase": "playing",
            "index": 0,
            "total": 2,
            "done_count": 0,
            "skipped_count": 0,
            "current_title": "1주차 강의",
            "position_secs": 61.0,
            "duration_secs": 3026.0,
            "remaining_secs": 2965.0,
            "total_remaining_secs": 5990.0,
            "paused": false,
            "items": [
                {"module_item_id": "1", "title": "1주차 강의", "state": "playing", "duration_secs": 3026.0},
                {"module_item_id": "2", "title": "2주차 강의", "state": "pending", "duration_secs": 3000.0}
            ]
        }))
    }

    #[test]
    fn menu_bar_shows_the_same_text_as_the_in_app_queue() {
        let snapshot = playing().to_snapshot();
        assert!(snapshot.active && snapshot.rendering);
        assert_eq!(tray_title(&snapshot), "▶ 1/2 · 49:25 남음");
        let menu = tray_menu_text(&snapshot);
        assert_eq!(menu.header, "1주차 강의 · 01:01 / 50:26");
        assert_eq!(menu.total, "전체 남은 시간 약 1시간 40분");
        assert_eq!(
            menu.rows,
            vec!["▶ 재생 중 · 1주차 강의", "· 대기 · 2주차 강의"]
        );
        assert_eq!(dock_badge(&snapshot), Some(1));
    }

    #[test]
    fn stalled_paused_and_opening_phases_show_like_the_in_app_ones() {
        let mut stalled = playing();
        stalled.phase = "stalled".into();
        assert_eq!(tray_title(&stalled.to_snapshot()), "⏸ 1/2 · 멈춤");
        let mut paused = playing();
        paused.paused = true;
        assert_eq!(tray_title(&paused.to_snapshot()), "⏸ 1/2 · 멈춤");
        let mut opening = playing();
        opening.phase = "opening".into();
        opening.position_secs = None;
        opening.remaining_secs = None;
        assert_eq!(tray_title(&opening.to_snapshot()), "▶ 1/2 · 여는 중");
    }

    #[test]
    fn finished_run_is_no_longer_active_and_has_no_badge() {
        let mut done = playing();
        done.active = false;
        done.phase = "finished".into();
        done.index = 2;
        done.done_count = 2;
        done.items[0].state = "done".into();
        done.items[1].state = "done".into();
        let snapshot = done.to_snapshot();
        assert!(!snapshot.active);
        assert_eq!(tray_title(&snapshot), "✓ 2/2 완료");
        assert_eq!(dock_badge(&snapshot), None);
    }

    #[test]
    fn unknown_phases_and_bad_numbers_are_made_safe() {
        let mut odd = playing();
        odd.phase = "weird".into();
        odd.position_secs = Some(f64::NAN);
        odd.total_remaining_secs = f64::INFINITY;
        odd.items[0].state = "???".into();
        let snapshot = odd.to_snapshot();
        assert_eq!(snapshot.phase, "idle");
        assert!(!snapshot.active);
        assert_eq!(snapshot.position_secs, None);
        assert_eq!(snapshot.total_remaining_secs, 0.0);
        assert_eq!(snapshot.items[0].state, "pending");
        // Missing optional fields deserialize too.
        let minimal: ExternalWatchView =
            serde_json::from_str(r#"{"active": false}"#).expect("minimal");
        assert_eq!(minimal.to_snapshot().total, 0);
    }

    fn notice(id: u64, kind: &str) -> ExternalNotice {
        ExternalNotice {
            id,
            kind: kind.into(),
            text: format!("text {id}"),
            item_title: Some("2주차 강의".into()),
            reason: Some("재생이 멈춰 건너뛰었어요".into()),
        }
    }

    #[test]
    fn each_notice_notifies_once_with_the_shared_notification_texts() {
        let mut done = playing();
        done.done_count = 1;
        done.skipped_count = 1;
        let snapshot = done.to_snapshot();
        let notices = vec![
            notice(1, "stalled"),
            notice(2, "skipped"),
            notice(3, "finished"),
            notice(4, "info"),
        ];
        let (texts, newest) = pending_notifications(&snapshot, &notices, 0);
        assert_eq!(newest, 4);
        assert_eq!(
            texts,
            vec![
                ("자동 재생이 멈췄어요".to_string(), "text 1".to_string()),
                (
                    "자동 재생: 강의를 건너뛰었어요".to_string(),
                    "text 2".to_string()
                ),
                (
                    "자동 재생 완료".to_string(),
                    "1개 강의 · 1개 건너뜀".to_string()
                ),
            ]
        );
        // Already announced notices are not repeated; the mark never moves backwards.
        let (again, mark) = pending_notifications(&snapshot, &notices, 4);
        assert!(again.is_empty() && mark == 4);
        let (partial, mark) = pending_notifications(&snapshot, &notices, 2);
        assert_eq!(partial.len(), 1);
        assert_eq!(mark, 4);
        let (none, mark) = pending_notifications(&snapshot, &[], 7);
        assert!(none.is_empty() && mark == 7);
    }

    #[test]
    fn a_skip_without_text_falls_back_to_the_shared_skip_notification() {
        let snapshot = playing().to_snapshot();
        let bare = ExternalNotice {
            text: String::new(),
            ..notice(1, "skipped")
        };
        let (texts, _) = pending_notifications(&snapshot, &[bare], 0);
        assert_eq!(texts[0].1, "2주차 강의 · 재생이 멈춰 건너뛰었어요");
    }

    #[test]
    fn failures_notify_with_the_backends_text() {
        let snapshot = playing().to_snapshot();
        let (texts, _) = pending_notifications(&snapshot, &[notice(1, "failed")], 0);
        assert_eq!(
            texts,
            vec![("자동 재생을 멈췄어요".to_string(), "text 1".to_string())]
        );
    }
}
