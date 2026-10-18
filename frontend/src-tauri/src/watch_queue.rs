//! Sequential-watch queue: the pure part.
//!
//! Everything here is free of Tauri types so it can be unit tested: the queue state machine
//! (which lecture is playing, what happens when it ends, stalls or is skipped), the numbers the
//! UI and the menu bar show (clock, time left, whole-queue estimate) and the geometry/title of
//! the small always-visible playback window. `watch_runtime.rs` owns the windows, the tray and
//! the threads and only feeds events into [`QueueCore`].
//!
//! Playback itself is never touched from here: the state only mirrors what the page script
//! (`assets/watch_assist.js`) reports about the real, visible, muted official player.

use serde::Serialize;

pub const WATCH_WINDOW_WIDTH: f64 = 480.0;
pub const WATCH_WINDOW_HEIGHT: f64 = 300.0;
pub const WATCH_WINDOW_MIN_WIDTH: f64 = 320.0;
pub const WATCH_WINDOW_MIN_HEIGHT: f64 = 200.0;
pub const WATCH_WINDOW_MARGIN: f64 = 16.0;
/// Without any sign of life from the page for this long (while the window is drawn), the item
/// counts as stalled.
pub const NO_ACTIVITY_TIMEOUT_SECS: u64 = 120;
/// How long the "finished" text stays in the menu bar.
pub const FINISHED_VISIBLE_SECS: u64 = 10 * 60;
/// The tray menu lists at most this many queue rows (a window around the current one).
pub const MAX_TRAY_ROWS: usize = 15;
const TITLE_MAX_CHARS_WINDOW: usize = 60;
const TITLE_MAX_CHARS_MENU: usize = 28;

// ------------------------------------------------------------------------ geometry

/// A rectangle in logical points (what the window APIs call logical pixels).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl Rect {
    /// The logical rectangle of a physical one on a screen with `scale` physical px per point.
    pub fn from_physical(position: (i32, i32), size: (u32, u32), scale: f64) -> Self {
        let scale = if scale.is_finite() && scale > 0.0 {
            scale
        } else {
            1.0
        };
        Self {
            x: f64::from(position.0) / scale,
            y: f64::from(position.1) / scale,
            width: f64::from(size.0) / scale,
            height: f64::from(size.1) / scale,
        }
    }
}

/// Top-left corner of a `width` x `height` window in the bottom-right corner of the screen's
/// visible frame (the area without the menu bar and the Dock), `margin` points from both edges.
/// A frame too small for the window pins it to the frame's top-left corner instead.
pub fn bottom_right_position(visible: Rect, width: f64, height: f64, margin: f64) -> (f64, f64) {
    let x = visible.x + visible.width - width - margin;
    let y = visible.y + visible.height - height - margin;
    (x.max(visible.x), y.max(visible.y))
}

/// `자동 재생 · 3/7 · <lecture title>` (`index` is zero based).
pub fn window_title(index: usize, total: usize, lecture_title: &str) -> String {
    format!(
        "자동 재생 · {}/{} · {}",
        index + 1,
        total,
        truncate_chars(lecture_title.trim(), TITLE_MAX_CHARS_WINDOW)
    )
}

pub fn truncate_chars(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let kept: String = text.chars().take(max.saturating_sub(1)).collect();
    format!("{}…", kept.trim_end())
}

// ---------------------------------------------------------------------------- state

#[derive(Clone, Debug, PartialEq)]
pub enum ItemState {
    Pending,
    Playing,
    Done,
    Skipped(String),
}

impl ItemState {
    fn name(&self) -> &'static str {
        match self {
            ItemState::Pending => "pending",
            ItemState::Playing => "playing",
            ItemState::Done => "done",
            ItemState::Skipped(_) => "skipped",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct QueueItem {
    pub module_item_id: String,
    /// The course this lecture belongs to: a queue may mix courses (all-course auto playback).
    pub course_id: String,
    pub course_name: String,
    pub title: String,
    /// Length from the lecture list, in seconds, when known.
    pub duration_secs: Option<f64>,
    pub state: ItemState,
}

impl QueueItem {
    pub fn new(module_item_id: &str, title: &str, duration_secs: Option<f64>) -> Self {
        Self {
            module_item_id: module_item_id.to_string(),
            course_id: String::new(),
            course_name: String::new(),
            title: title.to_string(),
            duration_secs: duration_secs.filter(|value| value.is_finite() && *value > 0.0),
            state: ItemState::Pending,
        }
    }

    /// The course this lecture is opened in.
    pub fn with_course(mut self, course_id: &str, course_name: &str) -> Self {
        self.course_id = course_id.to_string();
        self.course_name = course_name.to_string();
        self
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    /// No queue is running (never started, or stopped).
    Idle,
    /// The current lecture is being opened; the player has not reported yet.
    Opening,
    Playing,
    /// The page reported that playback does not advance; a retry is under way.
    Stalled,
    Finished,
}

impl Phase {
    fn name(self) -> &'static str {
        match self {
            Phase::Idle => "idle",
            Phase::Opening => "opening",
            Phase::Playing => "playing",
            Phase::Stalled => "stalled",
            Phase::Finished => "finished",
        }
    }
}

/// One report of the page script about the main (or intro) video.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Progress {
    pub position: f64,
    pub duration: f64,
    /// The long main video, not the short intro clip.
    pub main: bool,
    pub paused: bool,
    /// The page is being drawn (the window is not minimized or covered).
    pub rendering: bool,
}

/// What the owner has to do after an event.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Step {
    /// Open this item in the playback window.
    Open(usize),
    /// Every item has been handled.
    Finished,
    /// Nothing (the event did not apply).
    None,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StallDecision {
    /// First stall of this item: reload it once.
    Retry,
    /// Second stall: the item was skipped, carry on with the step.
    Next(Step),
    Ignore,
}

pub const STALL_SKIP_REASON: &str = "재생이 멈춰 건너뛰었어요";
pub const USER_SKIP_REASON: &str = "직접 넘겼어요";

pub struct QueueCore {
    items: Vec<QueueItem>,
    current: Option<usize>,
    phase: Phase,
    progress: Option<Progress>,
    rendering: bool,
    retried: bool,
    /// Bumped whenever a different item is opened or the queue ends, so slow work started for
    /// an earlier step can recognise itself as outdated.
    epoch: u64,
}

impl QueueCore {
    pub fn new(items: Vec<QueueItem>) -> Self {
        Self {
            items,
            current: None,
            phase: Phase::Idle,
            progress: None,
            rendering: true,
            retried: false,
            epoch: 0,
        }
    }

    /// Start counting epochs above `epoch`, so work begun for an earlier queue can never be
    /// mistaken for this one's (pass the previous queue's epoch + 1).
    pub fn with_epoch(mut self, epoch: u64) -> Self {
        self.epoch = epoch;
        self
    }

    pub fn phase(&self) -> Phase {
        self.phase
    }

    pub fn epoch(&self) -> u64 {
        self.epoch
    }

    pub fn current(&self) -> Option<usize> {
        self.current
    }

    pub fn items(&self) -> &[QueueItem] {
        &self.items
    }

    pub fn is_active(&self) -> bool {
        matches!(self.phase, Phase::Opening | Phase::Playing | Phase::Stalled)
    }

    /// Whether the page is currently drawn (false while minimized or covered).
    pub fn rendering(&self) -> bool {
        self.rendering
    }

    pub fn start(&mut self) -> Step {
        self.current = None;
        self.open_next()
    }

    fn open_next(&mut self) -> Step {
        let from = self.current.map_or(0, |index| index + 1);
        self.epoch += 1;
        self.progress = None;
        self.rendering = true;
        self.retried = false;
        let next = self
            .items
            .iter()
            .enumerate()
            .skip(from)
            .find(|(_, item)| item.state == ItemState::Pending)
            .map(|(index, _)| index);
        match next {
            Some(index) => {
                self.items[index].state = ItemState::Playing;
                self.current = Some(index);
                self.phase = Phase::Opening;
                Step::Open(index)
            }
            None => {
                self.current = None;
                self.phase = Phase::Finished;
                Step::Finished
            }
        }
    }

    fn finish_current(&mut self, state: ItemState) -> Step {
        if !self.is_active() {
            return Step::None;
        }
        if let Some(index) = self.current {
            self.items[index].state = state;
        }
        self.open_next()
    }

    /// Latest report of the page. Returns whether it applied to the running item.
    pub fn report_progress(&mut self, progress: Progress) -> bool {
        if !self.is_active() {
            return false;
        }
        self.rendering = progress.rendering;
        self.phase = Phase::Playing;
        // Only the long video drives the clock; the intro clip just proves the page is alive.
        if progress.main {
            self.progress = Some(progress);
        }
        true
    }

    /// A heartbeat that says nothing about the video, only whether the page is being drawn.
    pub fn report_rendering(&mut self, rendering: bool) {
        if self.is_active() {
            self.rendering = rendering;
        }
    }

    /// The main video really ended: the item is done, move on.
    pub fn on_ended(&mut self) -> Step {
        self.finish_current(ItemState::Done)
    }

    /// Playback did not advance. The first time the item is reloaded, the second time it is
    /// skipped with a reason.
    pub fn on_stalled(&mut self) -> StallDecision {
        if !self.is_active() {
            return StallDecision::Ignore;
        }
        if !self.retried {
            self.retried = true;
            self.phase = Phase::Stalled;
            self.progress = None;
            return StallDecision::Retry;
        }
        StallDecision::Next(self.finish_current(ItemState::Skipped(STALL_SKIP_REASON.to_string())))
    }

    /// Give up on the current item (user pressed next, or it could not be opened).
    pub fn skip_current(&mut self, reason: &str) -> Step {
        self.finish_current(ItemState::Skipped(reason.to_string()))
    }

    /// The user stopped the queue (or it cannot continue).
    pub fn stop(&mut self) {
        if let Some(index) = self.current {
            if self.items[index].state == ItemState::Playing {
                self.items[index].state = ItemState::Pending;
            }
        }
        self.current = None;
        self.progress = None;
        self.phase = Phase::Idle;
        self.epoch += 1;
    }

    // ------------------------------------------------------------------ numbers

    fn count(&self, wanted: &str) -> usize {
        self.items
            .iter()
            .filter(|item| item.state.name() == wanted)
            .count()
    }

    /// Seconds left of the current lecture: from the player when it reported, else the list's length.
    pub fn current_remaining_secs(&self) -> Option<f64> {
        let index = self.current?;
        match self.progress {
            Some(progress) if progress.duration > 0.0 => {
                Some((progress.duration - progress.position).max(0.0))
            }
            _ => self.items[index].duration_secs,
        }
    }

    /// Seconds left for the whole queue: the current lecture plus every waiting one (lectures
    /// without a known length count as zero).
    pub fn total_remaining_secs(&self) -> f64 {
        let mut total = 0.0;
        for (index, item) in self.items.iter().enumerate() {
            match item.state {
                ItemState::Pending => total += item.duration_secs.unwrap_or(0.0),
                ItemState::Playing if self.current == Some(index) => {
                    total += self.current_remaining_secs().unwrap_or(0.0)
                }
                _ => {}
            }
        }
        total
    }

    pub fn snapshot(&self) -> Snapshot {
        let total = self.items.len();
        let main_progress = self.progress.filter(|progress| progress.main);
        Snapshot {
            active: self.is_active(),
            phase: self.phase.name(),
            index: match self.phase {
                Phase::Finished => total,
                _ => self.current.unwrap_or(0),
            },
            total,
            done_count: self.count("done"),
            skipped_count: self.count("skipped"),
            current_module_item_id: self
                .current
                .map(|index| self.items[index].module_item_id.clone()),
            current_title: self.current.map(|index| self.items[index].title.clone()),
            position_secs: main_progress.map(|progress| progress.position),
            duration_secs: main_progress.map(|progress| progress.duration).or_else(|| {
                self.current
                    .and_then(|index| self.items[index].duration_secs)
            }),
            remaining_secs: self.current_remaining_secs(),
            total_remaining_secs: self.total_remaining_secs(),
            paused: main_progress.is_some_and(|progress| progress.paused),
            rendering: self.rendering,
            items: self
                .items
                .iter()
                .map(|item| ItemSnapshot {
                    module_item_id: item.module_item_id.clone(),
                    title: item.title.clone(),
                    state: item.state.name(),
                    reason: match &item.state {
                        ItemState::Skipped(reason) => Some(reason.clone()),
                        _ => None,
                    },
                    duration_secs: item.duration_secs,
                })
                .collect(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ItemSnapshot {
    pub module_item_id: String,
    pub title: String,
    /// `pending`, `playing`, `done` or `skipped`.
    pub state: &'static str,
    pub reason: Option<String>,
    pub duration_secs: Option<f64>,
}

/// What the React app and the menu bar read.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Snapshot {
    pub active: bool,
    /// `idle`, `opening`, `playing`, `stalled` or `finished`.
    pub phase: &'static str,
    /// Zero based index of the current lecture (`total` once finished).
    pub index: usize,
    pub total: usize,
    pub done_count: usize,
    pub skipped_count: usize,
    pub current_module_item_id: Option<String>,
    pub current_title: Option<String>,
    pub position_secs: Option<f64>,
    pub duration_secs: Option<f64>,
    pub remaining_secs: Option<f64>,
    pub total_remaining_secs: f64,
    pub paused: bool,
    /// False while the playback window is minimized or covered (playback stops then).
    pub rendering: bool,
    pub items: Vec<ItemSnapshot>,
}

#[cfg(test)]
impl Snapshot {
    pub fn idle() -> Self {
        QueueCore::new(Vec::new()).snapshot()
    }
}

// ------------------------------------------------------------------- text formats

/// `01:09` / `1:02:05`: the player clock.
pub fn format_clock(secs: f64) -> String {
    let total = secs.max(0.0).floor() as u64;
    let (hours, minutes, seconds) = (total / 3600, (total % 3600) / 60, total % 60);
    if hours > 0 {
        format!("{hours}:{minutes:02}:{seconds:02}")
    } else {
        format!("{minutes:02}:{seconds:02}")
    }
}

/// `5:21` / `1:02:05`: time left, as short as possible.
pub fn format_left(secs: f64) -> String {
    let total = secs.max(0.0).ceil() as u64;
    let (hours, minutes, seconds) = (total / 3600, (total % 3600) / 60, total % 60);
    if hours > 0 {
        format!("{hours}:{minutes:02}:{seconds:02}")
    } else {
        format!("{minutes}:{seconds:02}")
    }
}

/// `약 1시간 12분`, `약 35분`, `1분 미만`: the whole-queue estimate, rounded up to a minute.
pub fn format_total_remaining(secs: f64) -> String {
    if secs <= 0.0 {
        return "곧 끝나요".to_string();
    }
    if secs < 60.0 {
        return "1분 미만".to_string();
    }
    let minutes_total = (secs / 60.0).ceil() as u64;
    let (hours, minutes) = (minutes_total / 60, minutes_total % 60);
    match (hours, minutes) {
        (0, minutes) => format!("약 {minutes}분"),
        (hours, 0) => format!("약 {hours}시간"),
        (hours, minutes) => format!("약 {hours}시간 {minutes}분"),
    }
}

/// The text next to the menu bar icon.
pub fn tray_title(snapshot: &Snapshot) -> String {
    let position = format!("{}/{}", snapshot.index + 1, snapshot.total);
    match snapshot.phase {
        "finished" => format!("✓ {}/{} 완료", snapshot.done_count, snapshot.total),
        "stalled" => format!("⏸ {position} · 멈춤"),
        "opening" => format!("▶ {position} · 여는 중"),
        "playing" if snapshot.paused || !snapshot.rendering => format!("⏸ {position} · 멈춤"),
        "playing" => match snapshot.remaining_secs {
            Some(left) => format!("▶ {position} · {} 남음", format_left(left)),
            None => format!("▶ {position}"),
        },
        _ => String::new(),
    }
}

/// `✓ 완료 · title`, `▶ 재생 중 · title`, `· 대기 · title`, `! 건너뜀(reason) · title`.
pub fn queue_line(item: &ItemSnapshot) -> String {
    let title = truncate_chars(item.title.trim(), TITLE_MAX_CHARS_MENU);
    match item.state {
        "done" => format!("✓ 완료 · {title}"),
        "playing" => format!("▶ 재생 중 · {title}"),
        "skipped" => format!(
            "! 건너뜀({}) · {title}",
            item.reason.as_deref().unwrap_or("")
        ),
        _ => format!("· 대기 · {title}"),
    }
}

/// The index range of the queue rows the tray menu lists: at most `max` rows around the
/// current lecture.
pub fn menu_window(current: usize, total: usize, max: usize) -> std::ops::Range<usize> {
    if total <= max {
        return 0..total;
    }
    let start = current.saturating_sub(max / 4).min(total - max);
    start..start + max
}

#[derive(Clone, Debug, PartialEq)]
pub struct TrayMenuText {
    pub header: String,
    pub total: String,
    pub rows: Vec<String>,
}

/// Menu texts: header (title and clock), whole-queue estimate and the queue rows.
pub fn tray_menu_text(snapshot: &Snapshot) -> TrayMenuText {
    let header = match (&snapshot.current_title, snapshot.phase) {
        (_, "finished") => format!(
            "모두 끝났어요 · {}/{}개 시청{}",
            snapshot.done_count,
            snapshot.total,
            if snapshot.skipped_count > 0 {
                format!(" · {}개 건너뜀", snapshot.skipped_count)
            } else {
                String::new()
            }
        ),
        (Some(title), _) => {
            let clock = match (snapshot.position_secs, snapshot.duration_secs) {
                (Some(position), Some(duration)) => {
                    format!("{} / {}", format_clock(position), format_clock(duration))
                }
                (None, Some(duration)) => format!("00:00 / {}", format_clock(duration)),
                _ => String::new(),
            };
            let title = truncate_chars(title.trim(), TITLE_MAX_CHARS_MENU);
            if clock.is_empty() {
                title
            } else {
                format!("{title} · {clock}")
            }
        }
        (None, _) => "자동 재생".to_string(),
    };
    let total = if snapshot.phase == "finished" {
        "전체 남은 시간 없음".to_string()
    } else {
        format!(
            "전체 남은 시간 {}",
            format_total_remaining(snapshot.total_remaining_secs)
        )
    };
    let window = menu_window(snapshot.index, snapshot.total, MAX_TRAY_ROWS);
    TrayMenuText {
        header,
        total,
        rows: snapshot.items[window].iter().map(queue_line).collect(),
    }
}

/// Dock badge: how many lectures still wait after the current one (none: no badge).
pub fn dock_badge(snapshot: &Snapshot) -> Option<i64> {
    if !snapshot.active {
        return None;
    }
    let waiting = snapshot
        .items
        .iter()
        .filter(|item| item.state == "pending")
        .count() as i64;
    (waiting > 0).then_some(waiting)
}

/// macOS notification when the whole queue finished.
pub fn finished_notification(snapshot: &Snapshot) -> (String, String) {
    let mut body = format!("{}개 강의", snapshot.done_count);
    if snapshot.skipped_count > 0 {
        body.push_str(&format!(" · {}개 건너뜀", snapshot.skipped_count));
    }
    ("자동 재생 완료".to_string(), body)
}

/// macOS notification when a lecture was skipped on its own.
pub fn skipped_notification(title: &str, reason: &str) -> (String, String) {
    (
        "자동 재생: 강의를 건너뛰었어요".to_string(),
        format!("{} · {reason}", truncate_chars(title.trim(), 40)),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn queue(count: usize) -> QueueCore {
        QueueCore::new(
            (0..count)
                .map(|n| QueueItem::new(&format!("m{n}"), &format!("강의 {n}"), Some(600.0)))
                .collect(),
        )
    }

    fn progress(position: f64, duration: f64) -> Progress {
        Progress {
            position,
            duration,
            main: true,
            paused: false,
            rendering: true,
        }
    }

    fn states(core: &QueueCore) -> Vec<&'static str> {
        core.snapshot()
            .items
            .iter()
            .map(|item| item.state)
            .collect()
    }

    // ----------------------------------------------------------------- geometry

    #[test]
    fn window_sits_in_the_bottom_right_of_the_visible_frame() {
        // 1440x900 screen, 25pt menu bar on top and a 70pt Dock below.
        let visible = Rect {
            x: 0.0,
            y: 25.0,
            width: 1440.0,
            height: 805.0,
        };
        assert_eq!(
            bottom_right_position(visible, 480.0, 300.0, 16.0),
            (944.0, 514.0)
        );
        // A second screen to the right keeps its own origin.
        let second = Rect {
            x: 1440.0,
            y: 0.0,
            width: 1920.0,
            height: 1055.0,
        };
        assert_eq!(
            bottom_right_position(second, 480.0, 300.0, 16.0),
            (2864.0, 739.0)
        );
    }

    #[test]
    fn window_never_leaves_a_tiny_visible_frame() {
        let tiny = Rect {
            x: 10.0,
            y: 20.0,
            width: 300.0,
            height: 200.0,
        };
        assert_eq!(
            bottom_right_position(tiny, 480.0, 300.0, 16.0),
            (10.0, 20.0)
        );
    }

    #[test]
    fn physical_work_area_becomes_logical_points() {
        let rect = Rect::from_physical((0, 50), (2880, 1610), 2.0);
        assert_eq!(
            rect,
            Rect {
                x: 0.0,
                y: 25.0,
                width: 1440.0,
                height: 805.0
            }
        );
        assert_eq!(Rect::from_physical((0, 0), (100, 100), 0.0).width, 100.0);
    }

    #[test]
    fn window_title_shows_position_and_a_bounded_lecture_title() {
        assert_eq!(
            window_title(2, 7, " 3주차 강의 "),
            "자동 재생 · 3/7 · 3주차 강의"
        );
        let long = "가".repeat(100);
        let title = window_title(0, 1, &long);
        assert!(title.starts_with("자동 재생 · 1/1 · 가"));
        assert!(title.ends_with('…'));
        assert!(title.chars().count() < 80, "{title}");
    }

    // -------------------------------------------------------------- queue advance

    #[test]
    fn queue_advances_in_order_and_finishes() {
        let mut core = queue(3);
        assert_eq!(core.phase(), Phase::Idle);
        assert_eq!(core.start(), Step::Open(0));
        assert_eq!(core.phase(), Phase::Opening);
        assert_eq!(states(&core), ["playing", "pending", "pending"]);

        assert!(core.report_progress(progress(5.0, 600.0)));
        assert_eq!(core.phase(), Phase::Playing);
        assert_eq!(core.on_ended(), Step::Open(1));
        assert_eq!(core.on_ended(), Step::Open(2));
        assert_eq!(core.on_ended(), Step::Finished);
        assert_eq!(core.phase(), Phase::Finished);
        assert_eq!(states(&core), ["done", "done", "done"]);
        let snapshot = core.snapshot();
        assert!(!snapshot.active);
        assert_eq!(snapshot.index, 3);
        assert_eq!(snapshot.done_count, 3);
    }

    #[test]
    fn every_open_changes_the_epoch_so_stale_work_is_recognised() {
        let mut core = queue(2);
        let before = core.epoch();
        core.start();
        let first = core.epoch();
        core.on_ended();
        let second = core.epoch();
        core.stop();
        assert!(before < first && first < second && second < core.epoch());
    }

    #[test]
    fn a_new_queue_never_reuses_the_epochs_of_the_one_it_replaces() {
        let mut old = queue(2);
        old.start();
        old.on_ended();
        old.stop();
        let mut fresh = queue(2).with_epoch(old.epoch() + 1);
        assert!(fresh.epoch() > old.epoch());
        fresh.start();
        assert!(
            fresh.epoch() > old.epoch(),
            "{} vs {}",
            fresh.epoch(),
            old.epoch()
        );
    }

    #[test]
    fn events_before_start_or_after_stop_do_nothing() {
        let mut core = queue(2);
        assert_eq!(core.on_ended(), Step::None);
        assert_eq!(core.on_stalled(), StallDecision::Ignore);
        assert_eq!(core.skip_current("x"), Step::None);
        assert!(!core.report_progress(progress(1.0, 10.0)));
        core.start();
        core.stop();
        assert_eq!(core.phase(), Phase::Idle);
        assert_eq!(core.on_ended(), Step::None);
        assert_eq!(states(&core), ["pending", "pending"]);
    }

    #[test]
    fn a_stall_reloads_once_then_skips_with_a_reason() {
        let mut core = queue(2);
        core.start();
        core.report_progress(progress(30.0, 600.0));
        assert_eq!(core.on_stalled(), StallDecision::Retry);
        assert_eq!(core.phase(), Phase::Stalled, "reloading");
        assert_eq!(core.snapshot().position_secs, None, "old clock is dropped");
        assert_eq!(core.on_stalled(), StallDecision::Next(Step::Open(1)));
        let snapshot = core.snapshot();
        assert_eq!(snapshot.items[0].state, "skipped");
        assert_eq!(snapshot.items[0].reason.as_deref(), Some(STALL_SKIP_REASON));
        assert_eq!(snapshot.skipped_count, 1);
        // The next item gets its own single retry.
        assert_eq!(core.on_stalled(), StallDecision::Retry);
    }

    #[test]
    fn recovered_playback_after_a_retry_still_counts_the_retry() {
        let mut core = queue(1);
        core.start();
        assert_eq!(core.on_stalled(), StallDecision::Retry);
        core.report_progress(progress(2.0, 600.0));
        assert_eq!(core.phase(), Phase::Playing);
        assert_eq!(
            core.on_stalled(),
            StallDecision::Next(Step::Finished),
            "the single retry was used up"
        );
    }

    #[test]
    fn user_skip_and_open_failures_move_on_with_their_reason() {
        let mut core = queue(3);
        core.start();
        assert_eq!(core.skip_current(USER_SKIP_REASON), Step::Open(1));
        assert_eq!(
            core.skip_current("시청 주소를 찾지 못했어요"),
            Step::Open(2)
        );
        assert_eq!(core.skip_current("x"), Step::Finished);
        let snapshot = core.snapshot();
        assert_eq!(snapshot.skipped_count, 3);
        assert_eq!(snapshot.done_count, 0);
        assert_eq!(
            snapshot.items[1].reason.as_deref(),
            Some("시청 주소를 찾지 못했어요")
        );
    }

    #[test]
    fn empty_queue_finishes_immediately() {
        let mut core = queue(0);
        assert_eq!(core.start(), Step::Finished);
        assert_eq!(core.phase(), Phase::Finished);
    }

    #[test]
    fn intro_progress_proves_life_but_does_not_drive_the_clock() {
        let mut core = queue(1);
        core.start();
        let intro = Progress {
            main: false,
            ..progress(3.0, 6.0)
        };
        assert!(core.report_progress(intro));
        assert_eq!(core.phase(), Phase::Playing);
        let snapshot = core.snapshot();
        assert_eq!(snapshot.position_secs, None);
        assert_eq!(
            snapshot.remaining_secs,
            Some(600.0),
            "list length until the main video reports"
        );
        core.report_progress(progress(100.0, 450.0));
        assert_eq!(core.snapshot().remaining_secs, Some(350.0));
    }

    #[test]
    fn rendering_flag_follows_the_latest_report() {
        let mut core = queue(1);
        core.start();
        assert!(core.rendering());
        core.report_rendering(false);
        assert!(!core.rendering());
        core.report_progress(progress(1.0, 10.0));
        assert!(core.rendering());
        core.stop();
        core.report_rendering(false);
        assert!(
            core.rendering(),
            "idle queues ignore heartbeats and reset on the next open"
        );
    }

    // ----------------------------------------------------------------- numbers

    #[test]
    fn total_remaining_sums_current_and_waiting_lectures() {
        let mut core = QueueCore::new(vec![
            QueueItem::new("a", "a", Some(600.0)),
            QueueItem::new("b", "b", Some(1200.0)),
            QueueItem::new("c", "c", None),
            QueueItem::new("d", "d", Some(300.0)),
        ]);
        core.start();
        // Not reported yet: the list's length counts for the current one.
        assert_eq!(core.total_remaining_secs(), 600.0 + 1200.0 + 300.0);
        core.report_progress(progress(150.0, 480.0));
        assert_eq!(core.current_remaining_secs(), Some(330.0));
        assert_eq!(core.total_remaining_secs(), 330.0 + 1200.0 + 300.0);
        core.on_ended();
        assert_eq!(core.total_remaining_secs(), 1200.0 + 300.0);
        core.skip_current("x");
        core.on_ended();
        core.on_ended();
        assert_eq!(core.total_remaining_secs(), 0.0);
        assert_eq!(core.snapshot().done_count, 3);
    }

    #[test]
    fn remaining_never_goes_negative() {
        let mut core = queue(1);
        core.start();
        core.report_progress(progress(700.0, 600.0));
        assert_eq!(core.current_remaining_secs(), Some(0.0));
    }

    #[test]
    fn clocks_and_estimates_are_formatted_for_korean_ui() {
        assert_eq!(format_clock(69.4), "01:09");
        assert_eq!(format_clock(450.0), "07:30");
        assert_eq!(format_clock(3725.0), "1:02:05");
        assert_eq!(format_clock(-5.0), "00:00");
        assert_eq!(format_left(321.0), "5:21");
        assert_eq!(format_left(320.2), "5:21");
        assert_eq!(format_left(3725.0), "1:02:05");
        assert_eq!(format_total_remaining(4320.0), "약 1시간 12분");
        assert_eq!(format_total_remaining(3600.0), "약 1시간");
        assert_eq!(format_total_remaining(2100.0), "약 35분");
        assert_eq!(format_total_remaining(2101.0), "약 36분");
        assert_eq!(format_total_remaining(20.0), "1분 미만");
        assert_eq!(format_total_remaining(0.0), "곧 끝나요");
    }

    // -------------------------------------------------------------------- tray

    #[test]
    fn tray_title_shows_position_and_time_left() {
        let mut core = queue(7);
        core.start();
        for _ in 0..2 {
            core.on_ended();
        }
        assert_eq!(tray_title(&core.snapshot()), "▶ 3/7 · 여는 중");
        core.report_progress(progress(129.0, 450.0));
        assert_eq!(tray_title(&core.snapshot()), "▶ 3/7 · 5:21 남음");
    }

    #[test]
    fn tray_title_shows_pause_stall_and_finish() {
        let mut core = queue(7);
        core.start();
        core.on_ended();
        core.on_ended();
        core.report_progress(Progress {
            paused: true,
            ..progress(10.0, 450.0)
        });
        assert_eq!(tray_title(&core.snapshot()), "⏸ 3/7 · 멈춤");
        core.report_progress(Progress {
            rendering: false,
            ..progress(10.0, 450.0)
        });
        assert_eq!(
            tray_title(&core.snapshot()),
            "⏸ 3/7 · 멈춤",
            "covered window"
        );
        core.report_progress(progress(11.0, 450.0));
        assert!(tray_title(&core.snapshot()).starts_with("▶ 3/7"));

        core.report_progress(progress(12.0, 450.0));
        core.on_stalled();
        assert_eq!(
            tray_title(&core.snapshot()),
            "⏸ 3/7 · 멈춤",
            "reloading after a stall"
        );

        let mut done = queue(2);
        done.start();
        done.on_ended();
        done.on_ended();
        assert_eq!(tray_title(&done.snapshot()), "✓ 2/2 완료");
        done.stop();
        assert_eq!(tray_title(&done.snapshot()), "");
        assert_eq!(tray_title(&Snapshot::idle()), "");
    }

    #[test]
    fn tray_menu_lists_title_clock_total_and_every_queue_row() {
        let mut core = QueueCore::new(vec![
            QueueItem::new("a", "1주차 강의", Some(600.0)),
            QueueItem::new("b", "2주차 강의", Some(1200.0)),
            QueueItem::new("c", "3주차 강의", Some(1800.0)),
            QueueItem::new("d", "4주차 강의", Some(900.0)),
        ]);
        core.start();
        core.on_ended();
        core.on_stalled();
        core.on_stalled();
        core.report_progress(progress(69.0, 450.0));
        let text = tray_menu_text(&core.snapshot());
        assert_eq!(text.header, "3주차 강의 · 01:09 / 07:30");
        assert_eq!(text.total, "전체 남은 시간 약 22분");
        assert_eq!(
            text.rows,
            [
                "✓ 완료 · 1주차 강의",
                "! 건너뜀(재생이 멈춰 건너뛰었어요) · 2주차 강의",
                "▶ 재생 중 · 3주차 강의",
                "· 대기 · 4주차 강의",
            ]
        );
    }

    #[test]
    fn long_titles_are_truncated_in_menu_rows() {
        let line = queue_line(&ItemSnapshot {
            module_item_id: "a".into(),
            title: "가".repeat(60),
            state: "pending",
            reason: None,
            duration_secs: None,
        });
        assert!(line.starts_with("· 대기 · 가"));
        assert!(line.ends_with('…'));
        assert!(line.chars().count() < 40, "{line}");
    }

    #[test]
    fn finished_menu_summarises_the_run() {
        let mut core = queue(3);
        core.start();
        core.on_ended();
        core.skip_current("x");
        core.on_ended();
        let text = tray_menu_text(&core.snapshot());
        assert_eq!(text.header, "모두 끝났어요 · 2/3개 시청 · 1개 건너뜀");
        assert_eq!(text.total, "전체 남은 시간 없음");
        assert_eq!(text.rows.len(), 3);
    }

    #[test]
    fn long_queues_show_a_window_around_the_current_lecture() {
        assert_eq!(menu_window(0, 5, 15), 0..5);
        assert_eq!(menu_window(0, 40, 15), 0..15);
        assert_eq!(menu_window(20, 40, 15), 17..32);
        assert_eq!(menu_window(39, 40, 15), 25..40);
        let range = menu_window(20, 40, 15);
        assert!(range.contains(&20));
    }

    #[test]
    fn dock_badge_counts_lectures_waiting_after_the_current_one() {
        let mut core = queue(7);
        assert_eq!(dock_badge(&core.snapshot()), None, "not running");
        core.start();
        core.on_ended();
        core.on_ended();
        assert_eq!(dock_badge(&core.snapshot()), Some(4));
        for _ in 0..4 {
            core.on_ended();
        }
        assert_eq!(dock_badge(&core.snapshot()), None, "last lecture playing");
        core.on_ended();
        assert_eq!(dock_badge(&core.snapshot()), None, "finished");
    }

    #[test]
    fn notifications_summarise_the_outcome() {
        let mut core = queue(7);
        core.start();
        for _ in 0..7 {
            core.on_ended();
        }
        let (title, body) = finished_notification(&core.snapshot());
        assert_eq!(title, "자동 재생 완료");
        assert_eq!(body, "7개 강의");

        let mut partly = queue(2);
        partly.start();
        partly.skip_current("x");
        partly.on_ended();
        assert_eq!(
            finished_notification(&partly.snapshot()).1,
            "1개 강의 · 1개 건너뜀"
        );

        let (title, body) = skipped_notification("3주차 강의", STALL_SKIP_REASON);
        assert!(title.contains("건너뛰었어요"));
        assert_eq!(body, "3주차 강의 · 재생이 멈춰 건너뛰었어요");
    }

    #[test]
    fn items_of_different_courses_play_in_queue_order_each_keeping_its_course() {
        let mut core = QueueCore::new(vec![
            QueueItem::new("1", "비전채플 · 7주차", Some(600.0)).with_course("100", "비전채플"),
            QueueItem::new("2", "자료구조 · 3주차", Some(900.0)).with_course("200", "자료구조"),
            QueueItem::new("3", "비전채플 · 8주차", None).with_course("100", "비전채플"),
        ]);
        let mut played = Vec::new();
        let mut step = core.start();
        while let Step::Open(index) = step {
            let item = &core.items()[index];
            played.push((item.course_id.clone(), item.module_item_id.clone()));
            step = if index == 1 {
                core.skip_current("직접 넘겼어요")
            } else {
                core.on_ended()
            };
        }
        assert_eq!(step, Step::Finished);
        assert_eq!(
            played,
            vec![
                ("100".to_string(), "1".to_string()),
                ("200".to_string(), "2".to_string()),
                ("100".to_string(), "3".to_string()),
            ]
        );
        // Totals span the whole queue, whatever the course.
        let snapshot = core.snapshot();
        assert_eq!(
            (snapshot.done_count, snapshot.skipped_count, snapshot.total),
            (2, 1, 3)
        );
        assert_eq!(QueueItem::new("9", "x", None).course_id, "");
    }

    #[test]
    fn snapshot_serializes_with_stable_field_names() {
        let mut core = queue(2);
        core.start();
        core.report_progress(progress(69.0, 450.0));
        let json = serde_json::to_value(core.snapshot()).expect("serialize");
        assert_eq!(json["active"], true);
        assert_eq!(json["phase"], "playing");
        assert_eq!(json["index"], 0);
        assert_eq!(json["total"], 2);
        assert_eq!(json["position_secs"], 69.0);
        assert_eq!(json["duration_secs"], 450.0);
        assert_eq!(json["items"][0]["state"], "playing");
        assert_eq!(json["items"][1]["module_item_id"], "m1");
        assert_eq!(json["current_title"], "강의 0");
    }
}
