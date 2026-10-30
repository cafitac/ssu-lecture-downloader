//! Sequential-watch runtime: the small always-visible playback window, the menu bar item, the
//! Dock badge, notifications and the threads that move the queue along.
//!
//! The queue state lives here (in Rust), fed only by the signals the page script reports
//! through the `watch_signal` command (`progress|heartbeat|ended|stalled|debug`), so the menu
//! bar keeps updating even when the main window's web view is throttled or hidden. Signals
//! travel over Tauri IPC, never as navigations: assigning `location` from the page aborts the
//! document (and iframe) load that is still in flight. The React app reads the same state
//! through `watch_queue_status` and the `watch-queue-changed` event.
//!
//! Rules this module keeps:
//! - The playback window stays genuinely visible (small, always on top, bottom-right corner).
//!   Nothing here spoofs visibility, speed or position; a covered or minimized window simply
//!   shows as "멈춤" until the user brings it back.
//! - One window serves the whole queue: the next lecture is loaded into it through the same
//!   cookie bridge as the manual viewer.
//! - The queue mutex is never held while calling window/tray/network APIs (those may wait for
//!   the main thread, which may itself be waiting for the mutex): state changes are computed
//!   under the lock into a [`View`], then applied after it is released.

use crate::backend::{BackendManager, BACKEND_TOKEN_HEADER};
use crate::platform::{set_badge, set_tray_title, PlatformNotifier};
use crate::reminders::Notifier;
use crate::viewer::{
    bridge_session_and_navigate, session_bridge_target, watch_debug_event, with_watch_assist,
    OfficialViewerSessionBridgeRequest, VIEWER_USER_AGENT,
};
use crate::watch_external::{pending_notifications, ExternalWatchView};
use crate::watch_queue::{
    bottom_right_position, dock_badge, finished_notification, skipped_notification, tray_menu_text,
    tray_title, window_title, Phase, Progress, QueueCore, QueueItem, Rect, Snapshot, StallDecision,
    Step, TrayMenuText, FINISHED_VISIBLE_SECS, NO_ACTIVITY_TIMEOUT_SECS, USER_SKIP_REASON,
    WATCH_WINDOW_HEIGHT, WATCH_WINDOW_MARGIN, WATCH_WINDOW_MIN_HEIGHT, WATCH_WINDOW_MIN_WIDTH,
    WATCH_WINDOW_WIDTH,
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};
use tauri::menu::{IsMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, Wry};

const TRAY_ID: &str = "watch-queue";
pub(crate) const WATCH_LABEL_PREFIX: &str = "watch-player";
const QUEUE_EVENT: &str = "watch-queue-changed";
const NOTICE_EVENT: &str = "watch-queue-notice";
const EXTERNAL_COMMAND_EVENT: &str = "watch-external-command";
const ENDED_EVENT: &str = "official-watch-ended";
const DEBUG_EVENT: &str = "official-watch-debug";
const MENU_SHOW: &str = "watch-show-window";
const MENU_NEXT: &str = "watch-next";
const MENU_STOP: &str = "watch-stop";
const MENU_OPEN_APP: &str = "watch-open-app";
const MAX_QUEUE_ITEMS: usize = 200;
const TICK_INTERVAL: Duration = Duration::from_secs(2);
const PLAN_TIMEOUT: Duration = Duration::from_secs(120);
const PAGE_SWAP_GRACE: Duration = Duration::from_millis(1500);
const MAIN_THREAD_TIMEOUT: Duration = Duration::from_secs(20);
/// After a click on the finished menu bar text it disappears a little later (not while the
/// menu it just opened is still on screen).
const FINISHED_ACK_DELAY: Duration = Duration::from_secs(8);

// ------------------------------------------------------------------------- state

struct RuntimeState {
    core: QueueCore,
    window_label: Option<String>,
    /// The page the current lecture was opened at (reloaded once when it stalls).
    target: Option<tauri::Url>,
    last_activity: Instant,
    /// Progress/ended/stalled signals count only once the current lecture's page is loaded:
    /// until then the previous page may still be on screen and reporting.
    accepting: bool,
    finished_at: Option<Instant>,
    finished_acknowledged: bool,
    /// Distinguishes the playback windows of successive runs.
    run_seq: u64,
    /// A queue played outside this app (the Chrome window); mirrored into the menu bar, badge
    /// and notifications only while the in-app queue is idle.
    external: Option<ExternalRun>,
}

/// What the shell knows about the externally played queue (see `watch_external.rs`).
struct ExternalRun {
    run_id: String,
    snapshot: Snapshot,
    /// Highest notice id already announced with a macOS notification.
    last_notice_id: u64,
    finished_at: Option<Instant>,
    finished_acknowledged: bool,
}

impl RuntimeState {
    fn new() -> Self {
        Self {
            core: QueueCore::new(Vec::new()),
            window_label: None,
            target: None,
            last_activity: Instant::now(),
            accepting: true,
            finished_at: None,
            finished_acknowledged: false,
            run_seq: 0,
            external: None,
        }
    }

    /// The externally played queue shows only while the in-app queue has nothing to show.
    fn external_shown(&self) -> Option<&ExternalRun> {
        if self.core.is_active() || self.core.phase() != Phase::Idle {
            return None;
        }
        self.external.as_ref()
    }
}

#[derive(Clone)]
struct TrayMenuHandles {
    header: MenuItem<Wry>,
    total: MenuItem<Wry>,
    rows: Vec<MenuItem<Wry>>,
}

#[derive(Default)]
struct TrayState {
    handles: Option<TrayMenuHandles>,
    last_view: Option<TrayView>,
    last_badge: Option<Option<i64>>,
}

pub(crate) struct WatchRuntime {
    state: Mutex<RuntimeState>,
    tray: Mutex<TrayState>,
    notifier: Arc<dyn Notifier>,
}

impl WatchRuntime {
    pub(crate) fn new() -> Self {
        Self::with_notifier(Arc::new(PlatformNotifier))
    }

    fn with_notifier(notifier: Arc<dyn Notifier>) -> Self {
        Self {
            state: Mutex::new(RuntimeState::new()),
            tray: Mutex::new(TrayState::default()),
            notifier,
        }
    }
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

// -------------------------------------------------------------------------- views

/// What the outside world shows for one state: the snapshot for React plus the tray texts and
/// the Dock badge. Computed under the lock, applied after it.
#[derive(Clone)]
struct View {
    snapshot: Snapshot,
    tray: TrayView,
    badge: Option<i64>,
}

#[derive(Clone, Debug, PartialEq)]
struct TrayView {
    visible: bool,
    title: String,
    menu: TrayMenuText,
}

impl View {
    fn of(state: &RuntimeState) -> Self {
        match state.external_shown() {
            Some(external) => Self::from_snapshot(
                external.snapshot.clone(),
                external.finished_at,
                external.finished_acknowledged,
            ),
            None => Self::from_snapshot(
                state.core.snapshot(),
                state.finished_at,
                state.finished_acknowledged,
            ),
        }
    }

    fn from_snapshot(
        snapshot: Snapshot,
        finished_at: Option<Instant>,
        finished_acknowledged: bool,
    ) -> Self {
        let finished_visible = snapshot.phase == "finished"
            && !finished_acknowledged
            && finished_at
                .is_some_and(|at| at.elapsed() < Duration::from_secs(FINISHED_VISIBLE_SECS));
        let visible = snapshot.active || finished_visible;
        Self {
            tray: TrayView {
                visible,
                title: if visible {
                    tray_title(&snapshot)
                } else {
                    String::new()
                },
                menu: tray_menu_text(&snapshot),
            },
            badge: dock_badge(&snapshot),
            snapshot,
        }
    }
}

/// Run `change` under the lock and return its result with the resulting view.
fn update<R>(app: &AppHandle, change: impl FnOnce(&mut RuntimeState) -> R) -> (R, View) {
    let runtime = app.state::<WatchRuntime>();
    let mut state = lock(&runtime.state);
    let result = change(&mut state);
    let view = View::of(&state);
    (result, view)
}

fn read<R>(app: &AppHandle, look: impl FnOnce(&RuntimeState) -> R) -> R {
    let runtime = app.state::<WatchRuntime>();
    let state = lock(&runtime.state);
    look(&state)
}

/// Tell the world about a state change: React event, menu bar, Dock badge.
fn publish(app: &AppHandle, view: &View) {
    let _ = app.emit(QUEUE_EVENT, &view.snapshot);
    apply_tray(app, &view.tray);
    apply_badge(app, view.badge);
}

/// Menu bar and badge only (the periodic refresh does not need to wake React).
fn publish_outputs(app: &AppHandle, view: &View) {
    apply_tray(app, &view.tray);
    apply_badge(app, view.badge);
}

fn apply_badge(app: &AppHandle, badge: Option<i64>) {
    {
        let runtime = app.state::<WatchRuntime>();
        let mut tray = lock(&runtime.tray);
        if tray.last_badge == Some(badge) {
            return;
        }
        tray.last_badge = Some(badge);
    }
    if let Some(window) = app.get_webview_window("main") {
        set_badge(&window, badge);
    }
}

#[derive(Serialize, Clone)]
struct Notice {
    kind: &'static str,
    text: String,
    auth_expired: bool,
}

fn notice(app: &AppHandle, kind: &'static str, text: impl Into<String>) {
    let _ = app.emit(
        NOTICE_EVENT,
        Notice {
            kind,
            text: text.into(),
            auth_expired: false,
        },
    );
}

fn notify_later(app: &AppHandle, title: String, body: String) {
    let notifier = Arc::clone(&app.state::<WatchRuntime>().notifier);
    std::thread::spawn(move || {
        let _ = notifier.notify(&title, &body);
    });
}

// --------------------------------------------------------------------------- tray

/// Create the (hidden) menu bar item once, on the main thread, at start-up.
pub(crate) fn install_tray(app: &AppHandle) {
    let menu = match Menu::new(app) {
        Ok(menu) => menu,
        Err(error) => {
            eprintln!("watch tray: menu: {error}");
            return;
        }
    };
    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .menu(&menu)
        .show_menu_on_left_click(true)
        .tooltip("자동 재생")
        .on_menu_event(|app, event| match event.id().as_ref() {
            MENU_SHOW => {
                if external_active(app) {
                    external_command(app, "show");
                } else {
                    let _ = show_playback_window(app);
                }
            }
            MENU_NEXT => {
                if external_active(app) {
                    external_command(app, "next");
                } else {
                    next_item(app);
                }
            }
            MENU_STOP => {
                if external_active(app) {
                    external_command(app, "stop");
                } else {
                    stop_queue(app);
                }
            }
            MENU_OPEN_APP => show_main_window(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if matches!(event, TrayIconEvent::Click { .. }) {
                acknowledge_finished(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    match builder.build(app) {
        Ok(tray) => {
            let _ = tray.set_visible(false);
        }
        Err(error) => eprintln!("watch tray: {error}"),
    }
}

fn menu_item(app: &AppHandle, id: &str, text: &str, enabled: bool) -> Option<MenuItem<Wry>> {
    MenuItem::with_id(app, id, text, enabled, None::<&str>).ok()
}

/// Build the menu for a queue with `rows` rows. The info lines are disabled items whose text
/// is updated in place afterwards (rebuilding while the menu is open would close it).
fn build_menu(app: &AppHandle, text: &TrayMenuText) -> Option<(Menu<Wry>, TrayMenuHandles)> {
    let header = menu_item(app, "watch-header", &text.header, false)?;
    let total = menu_item(app, "watch-total", &text.total, false)?;
    let rows: Vec<MenuItem<Wry>> = text
        .rows
        .iter()
        .enumerate()
        .map(|(index, row)| menu_item(app, &format!("watch-row-{index}"), row, false))
        .collect::<Option<_>>()?;
    let show = menu_item(app, MENU_SHOW, "재생 창 보이기", true)?;
    let next = menu_item(app, MENU_NEXT, "다음 강의", true)?;
    let stop = menu_item(app, MENU_STOP, "자동 재생 중지", true)?;
    let open = menu_item(app, MENU_OPEN_APP, "앱 열기", true)?;
    let first_separator = PredefinedMenuItem::separator(app).ok()?;
    let second_separator = PredefinedMenuItem::separator(app).ok()?;
    let third_separator = PredefinedMenuItem::separator(app).ok()?;
    let mut items: Vec<&dyn IsMenuItem<Wry>> = vec![&header, &total, &first_separator];
    for row in &rows {
        items.push(row);
    }
    items.push(&second_separator);
    items.push(&show);
    items.push(&next);
    items.push(&stop);
    items.push(&third_separator);
    items.push(&open);
    let menu = Menu::with_items(app, &items).ok()?;
    Some((
        menu,
        TrayMenuHandles {
            header,
            total,
            rows,
        },
    ))
}

fn apply_tray(app: &AppHandle, view: &TrayView) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    let handles = {
        let runtime = app.state::<WatchRuntime>();
        let mut state = lock(&runtime.tray);
        if state.last_view.as_ref() == Some(view) {
            return;
        }
        state.last_view = Some(view.clone());
        state.handles.clone()
    };
    if !view.visible {
        set_tray_title(&tray, Some(""));
        let _ = tray.set_visible(false);
        return;
    }
    let handles = match handles {
        Some(handles) if handles.rows.len() == view.menu.rows.len() => handles,
        _ => {
            let Some((menu, fresh)) = build_menu(app, &view.menu) else {
                return;
            };
            let _ = tray.set_menu(Some(menu));
            let runtime = app.state::<WatchRuntime>();
            lock(&runtime.tray).handles = Some(fresh.clone());
            fresh
        }
    };
    let _ = handles.header.set_text(&view.menu.header);
    let _ = handles.total.set_text(&view.menu.total);
    for (item, text) in handles.rows.iter().zip(&view.menu.rows) {
        let _ = item.set_text(text);
    }
    set_tray_title(&tray, Some(view.title.as_str()));
    let _ = tray.set_visible(true);
}

/// A click on the "finished" menu bar text dismisses it after a moment.
fn acknowledge_finished(app: &AppHandle) {
    if read(app, |state| match state.external_shown() {
        Some(external) => external.snapshot.phase != "finished",
        None => state.core.phase() != Phase::Finished,
    }) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(FINISHED_ACK_DELAY);
        let (_, view) = update(&app, |state| {
            if state.external_shown().is_some() {
                if let Some(external) = state.external.as_mut() {
                    if external.snapshot.phase == "finished" {
                        external.finished_acknowledged = true;
                    }
                }
            } else if state.core.phase() == Phase::Finished {
                state.finished_acknowledged = true;
            }
        });
        publish_outputs(&app, &view);
    });
}

// ------------------------------------------------------------------------ commands

#[derive(Debug, Deserialize)]
pub(crate) struct WatchQueueItemInput {
    module_item_id: String,
    /// The lecture's own course (a queue may mix courses); falls back to the request's course.
    #[serde(default)]
    course_id: Option<String>,
    #[serde(default)]
    course_name: Option<String>,
    title: String,
    /// Length in seconds from the lecture list, when known.
    #[serde(default)]
    duration: Option<f64>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct WatchQueueStartRequest {
    /// Default course of items that do not name their own (per-course queues).
    #[serde(default)]
    course_id: String,
    #[serde(default)]
    course_name: String,
    items: Vec<WatchQueueItemInput>,
}

/// Clean the request into queue items: each carries its own course (its own fields, else the
/// request's), duplicates and blank ids are dropped, and every item must have a course.
fn queue_items(request: &WatchQueueStartRequest) -> Result<Vec<QueueItem>, String> {
    let mut seen = std::collections::HashSet::new();
    let mut items: Vec<QueueItem> = Vec::new();
    for item in request
        .items
        .iter()
        .filter(|item| !item.module_item_id.trim().is_empty())
    {
        let course_id = item
            .course_id
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| request.course_id.trim());
        if course_id.is_empty() {
            return Err("missing_course_id".to_string());
        }
        let course_name = item
            .course_name
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| request.course_name.trim());
        if !seen.insert((
            course_id.to_string(),
            item.module_item_id.trim().to_string(),
        )) {
            continue;
        }
        if items.len() >= MAX_QUEUE_ITEMS {
            break;
        }
        let title = item.title.trim();
        items.push(
            QueueItem::new(
                item.module_item_id.trim(),
                if title.is_empty() { "강의" } else { title },
                item.duration,
            )
            .with_course(course_id, course_name),
        );
    }
    if items.is_empty() {
        return Err("empty_watch_queue".to_string());
    }
    Ok(items)
}

#[tauri::command]
pub(crate) fn watch_queue_start(
    app: AppHandle,
    request: WatchQueueStartRequest,
) -> Result<Snapshot, String> {
    let items = queue_items(&request)?;
    if crate::update::is_update_in_progress(&app) {
        return Err(crate::update::UPDATE_IN_PROGRESS.to_string());
    }
    // A queue that is still running is replaced; its window goes away first.
    let (previous, _) = update(&app, |state| {
        let label = state.window_label.take();
        state.core.stop();
        label
    });
    close_window(&app, previous);
    let (step, view) = update(&app, |state| {
        state.run_seq += 1;
        state.external = None;
        state.core = QueueCore::new(items).with_epoch(state.core.epoch() + 1);
        state.target = None;
        state.accepting = true;
        state.finished_at = None;
        state.finished_acknowledged = false;
        state.last_activity = Instant::now();
        state.core.start()
    });
    // The update's guard runs after its latch is set: a queue that became active before the
    // latch is seen by the guard, one that became active after it is stopped here.
    if crate::update::is_update_in_progress(&app) {
        stop_queue(&app);
        return Err(crate::update::UPDATE_IN_PROGRESS.to_string());
    }
    publish(&app, &view);
    run_step(&app, step);
    Ok(view.snapshot)
}

#[tauri::command]
pub(crate) fn watch_queue_next(app: AppHandle) -> Snapshot {
    next_item(&app);
    read(&app, |state| state.core.snapshot())
}

#[tauri::command]
pub(crate) fn watch_queue_stop(app: AppHandle) -> Snapshot {
    stop_queue(&app);
    read(&app, |state| state.core.snapshot())
}

#[tauri::command]
pub(crate) fn watch_queue_status(app: AppHandle) -> Snapshot {
    read(&app, |state| state.core.snapshot())
}

#[tauri::command]
pub(crate) fn watch_queue_show_window(app: AppHandle) -> Result<(), String> {
    show_playback_window(&app)
}

/// The Chrome-mode queue as the React app just read it from the backend: shown in the menu bar
/// and Dock badge like an in-app queue, with macOS notifications for its new notices. Ignored
/// while the in-app queue is running (only one mode runs at a time).
#[tauri::command]
pub(crate) fn watch_external_status(app: AppHandle, view: ExternalWatchView) {
    let (notifications, shell_view) =
        update(&app, |state| apply_external(state, &view, Instant::now()));
    publish_outputs(&app, &shell_view);
    for (title, body) in notifications {
        notify_later(&app, title, body);
    }
}

/// Fold one external status into the runtime state; returns the notifications to send.
fn apply_external(
    state: &mut RuntimeState,
    view: &ExternalWatchView,
    now: Instant,
) -> Vec<(String, String)> {
    if state.core.is_active() {
        return Vec::new();
    }
    let snapshot = view.to_snapshot();
    let relevant = snapshot.active || snapshot.phase == "finished";
    if !relevant {
        // Stopped, failed or idle: nothing to show any more.
        state.external = None;
        return Vec::new();
    }
    if state.core.phase() == Phase::Finished {
        // A finished in-app run no longer hides the new one.
        let epoch = state.core.epoch() + 1;
        state.core = QueueCore::new(Vec::new()).with_epoch(epoch);
    }
    let fresh_run = state
        .external
        .as_ref()
        .map_or(true, |external| external.run_id != view.run_id);
    if fresh_run {
        state.external = Some(ExternalRun {
            run_id: view.run_id.clone(),
            snapshot: snapshot.clone(),
            last_notice_id: 0,
            finished_at: None,
            finished_acknowledged: false,
        });
    }
    let Some(external) = state.external.as_mut() else {
        return Vec::new();
    };
    let became_finished = snapshot.phase == "finished" && external.snapshot.phase != "finished";
    external.snapshot = snapshot;
    if became_finished || (external.snapshot.phase == "finished" && external.finished_at.is_none())
    {
        external.finished_at = Some(now);
        external.finished_acknowledged = false;
    }
    let (notifications, newest) =
        pending_notifications(&external.snapshot, &view.notices, external.last_notice_id);
    external.last_notice_id = newest;
    notifications
}

/// Ask the React app to run a menu bar action on the externally played queue.
fn external_command(app: &AppHandle, command: &'static str) {
    let _ = app.emit(EXTERNAL_COMMAND_EVENT, ExternalCommand { command });
}

#[derive(Serialize, Clone)]
struct ExternalCommand {
    command: &'static str,
}

fn external_active(app: &AppHandle) -> bool {
    read(app, |state| {
        state
            .external_shown()
            .is_some_and(|run| run.snapshot.active)
    })
}

fn next_item(app: &AppHandle) {
    let (step, view) = update(app, |state| state.core.skip_current(USER_SKIP_REASON));
    if step == Step::None {
        return;
    }
    publish(app, &view);
    run_step(app, step);
}

fn stop_queue(app: &AppHandle) {
    let (label, view) = update(app, |state| {
        let label = state.window_label.take();
        state.core.stop();
        label
    });
    publish(app, &view);
    close_window(app, label);
}

/// Whether a queue is running (the main window then hides instead of closing).
pub(crate) fn is_queue_active<R: tauri::Runtime>(app: &AppHandle<R>) -> bool {
    app.try_state::<WatchRuntime>().is_some_and(|runtime| {
        let state = lock(&runtime.state);
        state.core.is_active()
            || state
                .external_shown()
                .is_some_and(|run| run.snapshot.active)
    })
}

/// App is quitting: close the playback window and clear the menu bar and Dock.
pub(crate) fn shutdown(app: &AppHandle) {
    if app.try_state::<WatchRuntime>().is_none() {
        return;
    }
    update(app, |state| state.external = None);
    stop_queue(app);
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn show_playback_window(app: &AppHandle) -> Result<(), String> {
    let label = read(app, |state| state.window_label.clone());
    let window = label
        .and_then(|label| app.get_webview_window(&label))
        .ok_or_else(|| "재생 창이 열려 있지 않아요.".to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

fn close_window(app: &AppHandle, label: Option<String>) {
    if let Some(window) = label.and_then(|label| app.get_webview_window(&label)) {
        let _ = window.close();
    }
}

// ----------------------------------------------------------------- queue driving

fn run_step(app: &AppHandle, step: Step) {
    match step {
        Step::Open(index) => {
            let (epoch, _) = update(app, |state| {
                state.accepting = false;
                state.last_activity = Instant::now();
                state.core.epoch()
            });
            let app = app.clone();
            std::thread::spawn(move || open_item(&app, epoch, index));
        }
        Step::Finished => finish_queue(app),
        Step::None => {}
    }
}

fn finish_queue(app: &AppHandle) {
    let ((label, snapshot), view) = update(app, |state| {
        state.finished_at = Some(Instant::now());
        state.finished_acknowledged = false;
        (state.window_label.take(), state.core.snapshot())
    });
    publish(app, &view);
    close_window(app, label);
    let (title, body) = finished_notification(&snapshot);
    notify_later(app, title, body);
    notice(
        app,
        "success",
        format!(
            "자동 재생을 마쳤어요. {}개 시청{}",
            snapshot.done_count,
            if snapshot.skipped_count > 0 {
                format!(" · {}개 건너뜀", snapshot.skipped_count)
            } else {
                String::new()
            }
        ),
    );
}

#[derive(Clone, Debug, PartialEq)]
struct OpenJob {
    course_id: String,
    course_name: String,
    module_item_id: String,
    title: String,
    total: usize,
}

impl OpenJob {
    /// The lecture is opened in the course of its own queue item (queues may mix courses).
    fn from_item(item: &QueueItem, total: usize) -> Self {
        Self {
            course_id: item.course_id.clone(),
            course_name: item.course_name.clone(),
            module_item_id: item.module_item_id.clone(),
            title: item.title.clone(),
            total,
        }
    }
}

enum OpenError {
    /// The queue moved on while this was working; nothing to report.
    Stale,
    /// The LMS login is gone: the whole queue stops.
    Auth,
    Failed(String),
}

fn open_item(app: &AppHandle, epoch: u64, index: usize) {
    let job = read(app, |state| {
        if state.core.epoch() != epoch {
            return None;
        }
        let item = state.core.items().get(index)?;
        Some(OpenJob::from_item(item, state.core.items().len()))
    });
    let Some(job) = job else {
        wlog(
            app,
            format!("open_item epoch={epoch} index={index}: stale before start"),
        );
        return;
    };
    wlog(
        app,
        format!(
            "open_item epoch={epoch} index={index} item={}",
            job.module_item_id
        ),
    );
    let outcome = plan_and_show(app, epoch, index, &job);
    wlog(
        app,
        format!(
            "open_item index={index} outcome={}",
            match &outcome {
                Ok(()) => "ok".to_string(),
                Err(OpenError::Stale) => "stale".to_string(),
                Err(OpenError::Auth) => "auth".to_string(),
                Err(OpenError::Failed(reason)) => format!("failed: {reason}"),
            }
        ),
    );
    match outcome {
        Ok(()) | Err(OpenError::Stale) => {}
        Err(OpenError::Auth) => {
            let (label, view) = update(app, |state| {
                if state.core.epoch() != epoch {
                    return None;
                }
                let label = state.window_label.take();
                state.core.stop();
                Some(label)
            });
            if let Some(label) = label {
                publish(app, &view);
                close_window(app, label);
                let _ = app.emit(
                    NOTICE_EVENT,
                    Notice {
                        kind: "error",
                        text: "로그인이 만료되어 자동 재생을 멈췄어요. 다시 로그인해 주세요."
                            .to_string(),
                        auth_expired: true,
                    },
                );
            }
        }
        Err(OpenError::Failed(reason)) => {
            let (step, view) = update(app, |state| {
                if state.core.epoch() != epoch {
                    return Step::None;
                }
                state.core.skip_current(&reason)
            });
            if step != Step::None {
                publish(app, &view);
                notice(
                    app,
                    "error",
                    format!("‘{}’을(를) 열지 못해 건너뛰었어요. {reason}", job.title),
                );
                run_step(app, step);
            }
        }
    }
}

/// Step log for the playback window (`<app log dir>/watch.log`), for diagnosing runtime issues.
/// It records ids, stages and signal kinds only, never cookies or full URLs with tokens.
fn wlog(app: &AppHandle, message: impl AsRef<str>) {
    let line = format!(
        "{:?} {}\n",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis())
            .unwrap_or_default(),
        message.as_ref()
    );
    eprint!("watch: {line}");
    if let Ok(dir) = app.path().app_log_dir() {
        let _ = crate::log_rotation::append_line(
            &dir.join("watch.log"),
            crate::log_rotation::WATCH_LOG_CAP,
            &line,
        );
    }
}

fn stale(app: &AppHandle, epoch: u64) -> bool {
    read(app, |state| state.core.epoch() != epoch)
}

fn plan_and_show(
    app: &AppHandle,
    epoch: u64,
    index: usize,
    job: &OpenJob,
) -> Result<(), OpenError> {
    let connection = app
        .try_state::<Arc<BackendManager>>()
        .map(|manager| manager.connection())
        .ok_or_else(|| OpenError::Failed("앱 내부 서버를 사용할 수 없어요.".to_string()))?;
    let plan = fetch_session_bridge_plan(
        &connection.base_url,
        connection.token.as_deref(),
        &job.course_id,
        &job.module_item_id,
        &job.course_name,
    )
    .map_err(|error| match error {
        PlanError::Auth => OpenError::Auth,
        PlanError::Failed(reason) => OpenError::Failed(reason),
    })?;
    wlog(
        app,
        format!(
            "plan ok cookies={} launch={} viewer={}",
            plan.cookies.len(),
            plan.launch_url.is_some(),
            plan.viewer_url.is_some()
        ),
    );
    let target = session_bridge_target(&plan)
        .map_err(|_| OpenError::Failed("시청 주소를 찾지 못했어요.".to_string()))?;
    wlog(
        app,
        format!(
            "target host={} path={}",
            target.host_str().unwrap_or_default(),
            target.path()
        ),
    );
    if stale(app, epoch) {
        return Err(OpenError::Stale);
    }
    show_in_window(app, epoch, index, job, &plan, target.clone())?;
    update(app, |state| {
        if state.core.epoch() == epoch {
            state.target = Some(target);
            state.last_activity = Instant::now();
        }
    });
    accept_signals_soon(app, epoch);
    Ok(())
}

/// Let the previous page finish unloading before its successor's reports are believed.
fn accept_signals_soon(app: &AppHandle, epoch: u64) {
    std::thread::sleep(PAGE_SWAP_GRACE);
    update(app, |state| {
        if state.core.epoch() == epoch {
            state.accepting = true;
            state.last_activity = Instant::now();
        }
    });
}

/// Load the lecture into the queue's window, creating the window on first use.
fn show_in_window(
    app: &AppHandle,
    epoch: u64,
    index: usize,
    job: &OpenJob,
    plan: &OfficialViewerSessionBridgeRequest,
    target: tauri::Url,
) -> Result<(), OpenError> {
    // WebKit's cookie store and window creation must run on the main thread, like the
    // manual viewer's command does; from this worker thread the cookie call never returns.
    let main_app = app.clone();
    let job = job.clone();
    let plan = plan.clone();
    on_main_thread(app, move || {
        show_in_window_on_main(&main_app, epoch, index, &job, &plan, target)
    })
    .unwrap_or_else(|| Err(OpenError::Failed("재생 창이 응답하지 않아요.".to_string())))
}

/// Run `work` on the main thread and wait for its result (`None` if it never reports back).
fn on_main_thread<T: Send + 'static>(
    app: &AppHandle,
    work: impl FnOnce() -> T + Send + 'static,
) -> Option<T> {
    let (sender, receiver) = std::sync::mpsc::channel();
    app.run_on_main_thread(move || {
        let _ = sender.send(work());
    })
    .ok()?;
    receiver.recv_timeout(MAIN_THREAD_TIMEOUT).ok()
}

fn show_in_window_on_main(
    app: &AppHandle,
    epoch: u64,
    index: usize,
    job: &OpenJob,
    plan: &OfficialViewerSessionBridgeRequest,
    target: tauri::Url,
) -> Result<(), OpenError> {
    let title = window_title(index, job.total, &job.title);
    let existing = read(app, |state| state.window_label.clone())
        .and_then(|label| app.get_webview_window(&label));
    wlog(
        app,
        format!("on main thread; existing window={}", existing.is_some()),
    );
    let window = match existing {
        Some(window) => {
            let _ = window.set_title(&title);
            window
        }
        None => {
            let label = {
                let (label, _) = update(app, |state| {
                    if state.core.epoch() != epoch {
                        return None;
                    }
                    let label = format!("{WATCH_LABEL_PREFIX}-{}", state.run_seq);
                    state.window_label = Some(label.clone());
                    Some(label)
                });
                label.ok_or(OpenError::Stale)?
            };
            match create_watch_window(app, &label, &title) {
                Ok(window) => window,
                Err(error) => {
                    update(app, |state| {
                        if state.window_label.as_deref() == Some(label.as_str()) {
                            state.window_label = None;
                        }
                    });
                    return Err(OpenError::Failed(format!(
                        "재생 창을 만들지 못했어요. {error}"
                    )));
                }
            }
        }
    };
    // No page silencing here: an eval queued now can land on the *next* document and mute
    // it for good. Late signals from the old page are dropped by the epoch/accepting checks.
    wlog(app, "bridging cookies and navigating");
    let result = bridge_session_and_navigate(&window, &plan.cookies, target);
    wlog(
        app,
        format!(
            "bridge result={:?}",
            result.as_ref().map(|_| ()).map_err(|e| e.clone())
        ),
    );
    result.map_err(|_| OpenError::Failed("재생 페이지를 불러오지 못했어요.".to_string()))
}

/// The monitor the user is working on: the main window's, else the primary one.
fn current_visible_frame(app: &AppHandle) -> Option<Rect> {
    let monitor = app
        .get_webview_window("main")
        .and_then(|window| window.current_monitor().ok().flatten())
        .or_else(|| app.primary_monitor().ok().flatten())?;
    let area = monitor.work_area();
    Some(Rect::from_physical(
        (area.position.x, area.position.y),
        (area.size.width, area.size.height),
        monitor.scale_factor(),
    ))
}

fn create_watch_window(
    app: &AppHandle,
    label: &str,
    title: &str,
) -> tauri::Result<tauri::WebviewWindow> {
    let mut builder = tauri::WebviewWindowBuilder::new(
        app,
        label,
        tauri::WebviewUrl::External(crate::viewer::official_viewer_bootstrap_url()),
    )
    .title(title)
    .inner_size(WATCH_WINDOW_WIDTH, WATCH_WINDOW_HEIGHT)
    .min_inner_size(WATCH_WINDOW_MIN_WIDTH, WATCH_WINDOW_MIN_HEIGHT)
    .always_on_top(true)
    // Opening and advancing must not pull the keyboard away from whatever the user is doing.
    .focused(false)
    .resizable(true)
    .visible(true)
    .decorations(true)
    .user_agent(VIEWER_USER_AGENT);
    match current_visible_frame(app) {
        Some(frame) => {
            let (x, y) = bottom_right_position(
                frame,
                WATCH_WINDOW_WIDTH,
                WATCH_WINDOW_HEIGHT,
                WATCH_WINDOW_MARGIN,
            );
            builder = builder.position(x, y);
        }
        None => builder = builder.center(),
    }
    let load_app = app.clone();
    let window = with_watch_assist(builder)
        .on_page_load(move |_, payload| {
            let url = payload.url();
            wlog(
                &load_app,
                format!(
                    "page {:?} host={} path={}",
                    payload.event(),
                    url.host_str().unwrap_or_default(),
                    url.path()
                ),
            );
        })
        .build()?;
    wlog(app, format!("window created label={label}"));
    // Debug builds: open the inspector so the page console can be checked during runtime QA.
    #[cfg(debug_assertions)]
    window.open_devtools();
    let gone_app = app.clone();
    let gone_label = label.to_string();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed) {
            window_gone(&gone_app, &gone_label);
        }
    });
    Ok(window)
}

/// The user closed the playback window: the queue cannot go on without it.
fn window_gone(app: &AppHandle, label: &str) {
    let (stopped, view) = update(app, |state| {
        if state.window_label.as_deref() != Some(label) {
            return false;
        }
        state.window_label = None;
        state.core.stop();
        true
    });
    if stopped {
        publish(app, &view);
        notice(app, "info", "재생 창이 닫혀 자동 재생을 멈췄어요.");
    }
}

// ---------------------------------------------------------------------- signals

/// Most fields and the longest value a signal may carry (the page script sends a handful of
/// short fields; anything beyond that is cut).
const MAX_SIGNAL_FIELDS: usize = 24;
const MAX_SIGNAL_VALUE_CHARS: usize = 180;

/// The page script's report (`progress`, `heartbeat`, `ended`, `stalled`, `debug`) arriving
/// over Tauri IPC from the LMS page in the playback window. Only that window's remote pages may
/// call it (capabilities/watch-remote.json); the calling webview's label is checked again here,
/// so a signal from any other window, or from a window of an earlier run, is dropped.
#[tauri::command]
pub(crate) fn watch_signal(
    app: AppHandle,
    webview: tauri::Webview,
    kind: String,
    payload: HashMap<String, String>,
) {
    handle_signal(&app, webview.label(), &kind, &clean_signal(payload));
}

/// Bounded copy of a signal payload (never trust the page about sizes).
fn clean_signal(payload: HashMap<String, String>) -> HashMap<String, String> {
    payload
        .into_iter()
        .take(MAX_SIGNAL_FIELDS)
        .map(|(key, value)| {
            let key: String = key.chars().take(MAX_SIGNAL_VALUE_CHARS).collect();
            let value: String = value.chars().take(MAX_SIGNAL_VALUE_CHARS).collect();
            (key, value)
        })
        .collect()
}

/// Apply one signal of the playback window `label` (runs on the main thread, so it only does
/// short work; anything slow is handed to a thread).
fn handle_signal(app: &AppHandle, label: &str, kind: &str, values: &HashMap<String, String>) {
    if read(app, |state| state.window_label.as_deref() != Some(label)) {
        return;
    }
    let field = |key: &str| values.get(key).map(String::as_str);
    wlog(
        app,
        format!(
            "signal {kind} {} frame={}",
            field("stage").unwrap_or_default(),
            field("frame").unwrap_or_default()
        ),
    );
    if matches!(kind, "progress" | "ended" | "stalled") && !read(app, |state| state.accepting) {
        return;
    }
    match kind {
        "debug" => {
            let _ = app.emit(DEBUG_EVENT, watch_debug_event(values));
        }
        "progress" => {
            if let Some(progress) = progress_from_values(values) {
                let (_, view) = update(app, |state| {
                    state.last_activity = Instant::now();
                    state.core.report_progress(progress)
                });
                publish(app, &view);
            }
        }
        "heartbeat" => {
            let rendering = field("rendering") != Some("0");
            let (_, view) = update(app, |state| state.core.report_rendering(rendering));
            publish_outputs(app, &view);
        }
        "ended" => handle_ended(app),
        "stalled" => handle_stalled(app),
        _ => {}
    }
}

fn handle_ended(app: &AppHandle) {
    let ((step, ended_id), view) = update(app, |state| {
        // The long video only counts once the player has reported it playing.
        if state.core.phase() != Phase::Playing {
            return (Step::None, None);
        }
        let id = state
            .core
            .current()
            .map(|index| state.core.items()[index].module_item_id.clone());
        (state.core.on_ended(), id)
    });
    if step == Step::None {
        return;
    }
    publish(app, &view);
    let _ = app.emit(
        ENDED_EVENT,
        EndedEvent {
            stage: "ended",
            module_item_id: ended_id,
        },
    );
    run_step(app, step);
}

#[derive(Serialize, Clone)]
struct EndedEvent {
    stage: &'static str,
    module_item_id: Option<String>,
}

fn handle_stalled(app: &AppHandle) {
    let ((decision, title), view) = update(app, |state| {
        let title = state
            .core
            .current()
            .map(|index| state.core.items()[index].title.clone());
        let decision = state.core.on_stalled();
        if decision == StallDecision::Retry {
            state.last_activity = Instant::now();
        }
        (decision, title)
    });
    match decision {
        StallDecision::Ignore => {}
        StallDecision::Retry => {
            publish(app, &view);
            notice(app, "info", "재생이 멈춰 한 번 다시 불러오고 있어요.");
            let app = app.clone();
            std::thread::spawn(move || reload_current(&app));
        }
        StallDecision::Next(step) => {
            publish(app, &view);
            let title = title.unwrap_or_default();
            let (notice_title, body) =
                skipped_notification(&title, crate::watch_queue::STALL_SKIP_REASON);
            notify_later(app, notice_title, body);
            notice(app, "error", format!("‘{title}’ 재생이 멈춰 건너뛰었어요."));
            run_step(app, step);
        }
    }
}

/// Reload the lecture page of the current item (the single automatic retry of a stall).
fn reload_current(app: &AppHandle) {
    let (target, label) = read(app, |state| {
        (state.target.clone(), state.window_label.clone())
    });
    if let (Some(target), Some(window)) = (target, label.and_then(|l| app.get_webview_window(&l))) {
        let _ = app.run_on_main_thread(move || {
            let _ = window.navigate(target);
        });
    }
}

/// The progress report of the page script (`time`, `duration`, `main`, `paused`, `rendering`).
pub(crate) fn progress_from_values(values: &HashMap<String, String>) -> Option<Progress> {
    let text = |key: &str| values.get(key).map(String::as_str);
    let number = |key: &str| {
        text(key)
            .and_then(|value| value.parse::<f64>().ok())
            .filter(|value| value.is_finite() && *value >= 0.0)
    };
    let position = number("time")?;
    let duration = number("duration")?;
    Some(Progress {
        position,
        duration,
        main: text("main") == Some("1"),
        paused: text("paused") == Some("1"),
        rendering: text("rendering") != Some("0"),
    })
}

// ------------------------------------------------------------------------ ticker

/// Once per `TICK_INTERVAL`: keeps the menu bar current, hides the finished text after its
/// time, and treats a page that has been silent for too long as stalled.
pub(crate) fn spawn_ticker(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(TICK_INTERVAL);
        tick(&app);
    });
}

fn tick(app: &AppHandle) {
    let (stalled, view) = update(app, |state| {
        if !state.core.is_active() || !state.accepting {
            return false;
        }
        if !state.core.rendering() {
            // A covered or minimized window cannot play; waiting is not a stall.
            state.last_activity = Instant::now();
            return false;
        }
        if state.last_activity.elapsed() > Duration::from_secs(NO_ACTIVITY_TIMEOUT_SECS) {
            state.last_activity = Instant::now();
            return true;
        }
        false
    });
    publish_outputs(app, &view);
    if stalled {
        handle_stalled(app);
    }
}

// ------------------------------------------------------------- backend plan call

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum PlanError {
    /// The backend says the LMS login is gone (HTTP 401).
    Auth,
    Failed(String),
}

fn plan_error_reason(error_code: Option<&str>) -> String {
    match error_code {
        Some("network_error") | Some("network_timeout") => {
            "LMS에 연결하지 못했어요. 인터넷 연결을 확인해 주세요.".to_string()
        }
        _ => "시청 주소를 찾지 못했어요.".to_string(),
    }
}

/// Ask the local backend for the session-bridge plan of one lecture (viewer address, launch
/// address and the LMS cookies to inject), the same call the React app makes for a manual watch.
pub(crate) fn fetch_session_bridge_plan(
    base_url: &str,
    token: Option<&str>,
    course_id: &str,
    module_item_id: &str,
    course_name: &str,
) -> Result<OfficialViewerSessionBridgeRequest, PlanError> {
    let client = reqwest::blocking::Client::builder()
        .timeout(PLAN_TIMEOUT)
        .build()
        .map_err(|_| PlanError::Failed("앱 내부 통신을 준비하지 못했어요.".to_string()))?;
    let body = serde_json::json!({
        "course_id": course_id,
        "module_item_id": module_item_id,
        "course_name": course_name,
    });
    let mut request = client
        .post(format!("{base_url}/api/watch/session-bridge-plan"))
        .header(reqwest::header::CONTENT_TYPE, "application/json")
        .body(body.to_string());
    if let Some(token) = token {
        request = request.header(BACKEND_TOKEN_HEADER, token);
    }
    let response = request.send().map_err(|error| {
        if error.is_timeout() {
            PlanError::Failed("시청 주소를 가져오는 데 너무 오래 걸렸어요.".to_string())
        } else {
            PlanError::Failed("앱 내부 서버에 연결하지 못했어요.".to_string())
        }
    })?;
    let status = response.status();
    let text = response.text().unwrap_or_default();
    if !status.is_success() {
        let code = serde_json::from_str::<serde_json::Value>(&text)
            .ok()
            .and_then(|value| value["error_code"].as_str().map(str::to_string));
        if status == reqwest::StatusCode::UNAUTHORIZED
            && code.as_deref() != Some("api_token_invalid")
        {
            return Err(PlanError::Auth);
        }
        return Err(PlanError::Failed(plan_error_reason(code.as_deref())));
    }
    serde_json::from_str::<OfficialViewerSessionBridgeRequest>(&text)
        .map_err(|_| PlanError::Failed("시청 주소를 읽지 못했어요.".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;

    fn values(fields: &[(&str, &str)]) -> HashMap<String, String> {
        fields
            .iter()
            .map(|(key, value)| (key.to_string(), value.to_string()))
            .collect()
    }

    #[test]
    fn progress_reports_are_parsed_from_the_signal_payload() {
        let progress = progress_from_values(&values(&[
            ("time", "69.4"),
            ("duration", "450"),
            ("main", "1"),
            ("paused", "0"),
            ("rendering", "1"),
            ("frame", "child"),
        ]))
        .expect("progress");
        assert_eq!(
            progress,
            Progress {
                position: 69.4,
                duration: 450.0,
                main: true,
                paused: false,
                rendering: true,
            }
        );
        let covered = progress_from_values(&values(&[
            ("time", "3"),
            ("duration", "6"),
            ("main", "0"),
            ("paused", "1"),
            ("rendering", "0"),
        ]))
        .expect("progress");
        assert!(!covered.main && covered.paused && !covered.rendering);
    }

    #[test]
    fn broken_progress_reports_are_ignored() {
        assert_eq!(progress_from_values(&values(&[])), None);
        for (time, duration) in [("abc", "10"), ("-4", "10"), ("NaN", "10"), ("3", "inf")] {
            assert_eq!(
                progress_from_values(&values(&[("time", time), ("duration", duration)])),
                None,
                "{time} {duration}"
            );
        }
    }

    #[test]
    fn signal_payloads_are_bounded_before_use() {
        let mut payload = HashMap::new();
        for index in 0..100 {
            payload.insert(format!("key{index}"), "x".repeat(10_000));
        }
        let cleaned = clean_signal(payload);
        assert_eq!(cleaned.len(), MAX_SIGNAL_FIELDS);
        assert!(cleaned
            .values()
            .all(|value| value.chars().count() == MAX_SIGNAL_VALUE_CHARS));
    }

    fn input(id: &str, course: Option<(&str, &str)>) -> WatchQueueItemInput {
        WatchQueueItemInput {
            module_item_id: id.into(),
            course_id: course.map(|(id, _)| id.to_string()),
            course_name: course.map(|(_, name)| name.to_string()),
            title: format!("강의 {id}"),
            duration: Some(600.0),
        }
    }

    #[test]
    fn queue_request_is_cleaned_deduplicated_and_must_not_be_empty() {
        let request = WatchQueueStartRequest {
            course_id: " 44070 ".to_string(),
            course_name: "UX".to_string(),
            items: vec![
                WatchQueueItemInput {
                    title: " 1주차 ".into(),
                    ..input("1", None)
                },
                WatchQueueItemInput {
                    title: "dup".into(),
                    duration: None,
                    ..input("1", None)
                },
                WatchQueueItemInput {
                    title: "no id".into(),
                    duration: None,
                    ..input(" ", None)
                },
                WatchQueueItemInput {
                    title: "".into(),
                    duration: Some(-1.0),
                    ..input("2", None)
                },
            ],
        };
        let items = queue_items(&request).expect("items");
        assert_eq!(items.len(), 2);
        assert_eq!(items[0].title, "1주차");
        assert_eq!(items[0].duration_secs, Some(600.0));
        assert_eq!(items[1].title, "강의");
        assert_eq!(items[1].duration_secs, None);
        // Items without their own course take the request's.
        assert_eq!(items[0].course_id, "44070");
        assert_eq!(items[0].course_name, "UX");

        let empty = WatchQueueStartRequest {
            course_id: "1".into(),
            course_name: String::new(),
            items: Vec::new(),
        };
        assert_eq!(queue_items(&empty).unwrap_err(), "empty_watch_queue");
        let no_course = WatchQueueStartRequest {
            course_id: " ".into(),
            ..request
        };
        assert_eq!(queue_items(&no_course).unwrap_err(), "missing_course_id");
    }

    #[test]
    fn mixed_course_queue_keeps_each_items_own_course_and_opens_it_there() {
        let request = WatchQueueStartRequest {
            course_id: String::new(),
            course_name: String::new(),
            items: vec![
                input("11", Some((" 100 ", "비전채플"))),
                input("21", Some(("200", "자료구조"))),
                // The same module item id in another course is a different lecture.
                input("11", Some(("300", "알고리즘"))),
                // The same lecture twice is one.
                input("21", Some(("200", "자료구조"))),
                input("12", Some(("100", "비전채플"))),
            ],
        };
        let items = queue_items(&request).expect("items");
        let order: Vec<_> = items
            .iter()
            .map(|item| (item.course_id.as_str(), item.module_item_id.as_str()))
            .collect();
        assert_eq!(
            order,
            vec![("100", "11"), ("200", "21"), ("300", "11"), ("100", "12")]
        );

        let mut core = QueueCore::new(items);
        let mut jobs = Vec::new();
        let mut step = core.start();
        while let Step::Open(index) = step {
            jobs.push(OpenJob::from_item(&core.items()[index], core.items().len()));
            step = core.on_ended();
        }
        assert_eq!(step, Step::Finished);
        assert_eq!(
            jobs.iter()
                .map(|job| (
                    job.course_id.as_str(),
                    job.course_name.as_str(),
                    job.module_item_id.as_str()
                ))
                .collect::<Vec<_>>(),
            vec![
                ("100", "비전채플", "11"),
                ("200", "자료구조", "21"),
                ("300", "알고리즘", "11"),
                ("100", "비전채플", "12"),
            ]
        );
        assert!(jobs.iter().all(|job| job.total == 4));
    }

    #[test]
    fn an_item_without_any_course_is_rejected() {
        let request = WatchQueueStartRequest {
            course_id: String::new(),
            course_name: String::new(),
            items: vec![input("1", Some(("5", "a"))), input("2", None)],
        };
        assert_eq!(queue_items(&request).unwrap_err(), "missing_course_id");
    }

    /// One-shot HTTP server: returns the raw request it received.
    fn serve_once(status_line: &str, body: &str) -> (String, std::thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let address = listener.local_addr().expect("addr");
        let response = format!(
            "HTTP/1.1 {status_line}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let handle = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let mut received = Vec::new();
            let mut buffer = [0u8; 4096];
            loop {
                let read = stream.read(&mut buffer).expect("read");
                received.extend_from_slice(&buffer[..read]);
                let text = String::from_utf8_lossy(&received).to_string();
                if let Some((head, rest)) = text.split_once("\r\n\r\n") {
                    let length = head
                        .lines()
                        .find_map(|line| {
                            line.to_ascii_lowercase()
                                .strip_prefix("content-length:")
                                .map(|value| value.trim().parse::<usize>().unwrap_or(0))
                        })
                        .unwrap_or(0);
                    if rest.len() >= length {
                        break;
                    }
                }
                if read == 0 {
                    break;
                }
            }
            stream.write_all(response.as_bytes()).expect("write");
            String::from_utf8_lossy(&received).to_string()
        });
        (format!("http://{address}"), handle)
    }

    #[test]
    fn plan_call_sends_token_and_ids_and_reads_the_bridge_payload() {
        let payload = r#"{"ok":true,"error_code":null,"title":"3주차","viewer_url":"https://commons.ssu.ac.kr/viewer","launch_url":"https://canvas.ssu.ac.kr/courses/7/modules/items/9","cookies":[{"name":"xn_api_token","value":"secret","domain":"canvas.ssu.ac.kr","path":"/","secure":true,"http_only":true,"same_site":null}],"diagnostics":{"cookie_count":1}}"#;
        let (base, handle) = serve_once("200 OK", payload);
        let plan =
            fetch_session_bridge_plan(&base, Some("tok-123"), "7", "9", "UX방법론").expect("plan");
        let received = handle.join().expect("server");
        assert!(
            received.starts_with("POST /api/watch/session-bridge-plan HTTP/1.1"),
            "{received}"
        );
        assert!(received
            .to_ascii_lowercase()
            .contains("x-ssu-token: tok-123"));
        assert!(received.contains(r#""course_id":"7""#), "{received}");
        assert!(received.contains(r#""module_item_id":"9""#), "{received}");
        assert_eq!(plan.title.as_deref(), Some("3주차"));
        assert_eq!(plan.cookies.len(), 1);
        let target = session_bridge_target(&plan).expect("target");
        assert_eq!(
            target.as_str(),
            "https://canvas.ssu.ac.kr/courses/7/modules/items/9"
        );
    }

    #[test]
    fn plan_call_maps_login_loss_and_backend_errors() {
        let (base, handle) = serve_once(
            "401 Unauthorized",
            r#"{"ok":false,"error_code":"session_expired"}"#,
        );
        assert_eq!(
            fetch_session_bridge_plan(&base, None, "7", "9", "c"),
            Err(PlanError::Auth)
        );
        handle.join().expect("server");

        let (base, handle) = serve_once(
            "401 Unauthorized",
            r#"{"ok":false,"error_code":"api_token_invalid"}"#,
        );
        assert!(matches!(
            fetch_session_bridge_plan(&base, None, "7", "9", "c"),
            Err(PlanError::Failed(_))
        ));
        handle.join().expect("server");

        let (base, handle) = serve_once(
            "502 Bad Gateway",
            r#"{"ok":false,"error_code":"network_error"}"#,
        );
        match fetch_session_bridge_plan(&base, None, "7", "9", "c") {
            Err(PlanError::Failed(reason)) => {
                assert!(reason.contains("인터넷"), "{reason}");
                assert!(!reason.contains("network_error"), "{reason}");
            }
            other => panic!("{other:?}"),
        }
        handle.join().expect("server");

        let (base, handle) = serve_once("200 OK", "not json");
        assert!(matches!(
            fetch_session_bridge_plan(&base, None, "7", "9", "c"),
            Err(PlanError::Failed(_))
        ));
        handle.join().expect("server");
    }

    fn finished_state(finished_ago: Duration) -> RuntimeState {
        let mut state = RuntimeState::new();
        state.core = QueueCore::new(vec![QueueItem::new("a", "강의 A", Some(600.0))]);
        state.core.start();
        state.core.on_ended();
        state.finished_at = Instant::now().checked_sub(finished_ago);
        state
    }

    #[test]
    fn menu_bar_shows_the_finished_text_for_ten_minutes_unless_dismissed() {
        let fresh = View::of(&finished_state(Duration::from_secs(5)));
        assert!(fresh.tray.visible);
        assert_eq!(fresh.tray.title, "✓ 1/1 완료");
        assert_eq!(fresh.badge, None);

        let stale = View::of(&finished_state(Duration::from_secs(
            FINISHED_VISIBLE_SECS + 5,
        )));
        assert!(!stale.tray.visible);
        assert_eq!(stale.tray.title, "");

        let mut dismissed = finished_state(Duration::from_secs(5));
        dismissed.finished_acknowledged = true;
        assert!(!View::of(&dismissed).tray.visible);
    }

    #[test]
    fn menu_bar_follows_a_running_queue_and_disappears_when_stopped() {
        let mut state = RuntimeState::new();
        assert!(!View::of(&state).tray.visible, "nothing ever ran");
        state.core = QueueCore::new(
            (0..3)
                .map(|n| QueueItem::new(&n.to_string(), &format!("강의 {n}"), Some(600.0)))
                .collect(),
        );
        state.core.start();
        state.core.report_progress(Progress {
            position: 69.0,
            duration: 450.0,
            main: true,
            paused: false,
            rendering: true,
        });
        let running = View::of(&state);
        assert!(running.tray.visible);
        assert_eq!(running.tray.title, "▶ 1/3 · 6:21 남음");
        assert_eq!(running.tray.menu.header, "강의 0 · 01:09 / 07:30");
        assert_eq!(running.tray.menu.total, "전체 남은 시간 약 27분");
        assert_eq!(running.badge, Some(2));
        assert_eq!(running.snapshot.index, 0);

        state.core.stop();
        let stopped = View::of(&state);
        assert!(!stopped.tray.visible);
        assert_eq!(stopped.badge, None);
    }

    fn external_view(
        run_id: &str,
        phase: &str,
        active: bool,
        notices: serde_json::Value,
    ) -> ExternalWatchView {
        serde_json::from_value(serde_json::json!({
            "run_id": run_id,
            "active": active,
            "phase": phase,
            "index": 0,
            "total": 2,
            "done_count": 0,
            "skipped_count": 0,
            "current_title": "1주차 강의",
            "position_secs": 61.0,
            "duration_secs": 3026.0,
            "remaining_secs": 2965.0,
            "total_remaining_secs": 5990.0,
            "items": [
                {"title": "1주차 강의", "state": "playing"},
                {"title": "2주차 강의", "state": "pending"}
            ],
            "notices": notices,
        }))
        .expect("view")
    }

    #[test]
    fn an_external_queue_drives_the_menu_bar_and_badge_like_the_in_app_one() {
        let mut state = RuntimeState::new();
        let sent = apply_external(
            &mut state,
            &external_view("r1", "playing", true, serde_json::json!([])),
            Instant::now(),
        );
        assert!(sent.is_empty());
        let view = View::of(&state);
        assert!(view.tray.visible);
        assert_eq!(view.tray.title, "▶ 1/2 · 49:25 남음");
        assert_eq!(view.tray.menu.header, "1주차 강의 · 01:01 / 50:26");
        assert_eq!(view.badge, Some(1));
        assert!(state
            .external_shown()
            .is_some_and(|run| run.snapshot.active));

        // It ends: the finished text shows, no badge, and one notification is sent once.
        let notices = serde_json::json!([{"id": 1, "kind": "finished", "text": "done"}]);
        let finished = external_view("r1", "finished", false, notices);
        let sent = apply_external(&mut state, &finished, Instant::now());
        assert_eq!(sent.len(), 1);
        assert_eq!(sent[0].0, "자동 재생 완료");
        let view = View::of(&state);
        assert!(view.tray.visible && view.tray.title.starts_with("✓"));
        assert_eq!(view.badge, None);
        assert!(apply_external(&mut state, &finished, Instant::now()).is_empty());

        // A click on the finished text acknowledges it.
        state
            .external
            .as_mut()
            .expect("external")
            .finished_acknowledged = true;
        assert!(!View::of(&state).tray.visible);
    }

    #[test]
    fn a_stopped_or_failed_external_queue_leaves_the_menu_bar() {
        let mut state = RuntimeState::new();
        apply_external(
            &mut state,
            &external_view("r1", "playing", true, serde_json::json!([])),
            Instant::now(),
        );
        assert!(View::of(&state).tray.visible);
        apply_external(
            &mut state,
            &external_view("r1", "idle", false, serde_json::json!([])),
            Instant::now(),
        );
        assert!(state.external.is_none());
        assert!(!View::of(&state).tray.visible);
        assert_eq!(View::of(&state).badge, None);
    }

    #[test]
    fn a_new_external_run_announces_its_notices_from_the_start() {
        let mut state = RuntimeState::new();
        let skipped = serde_json::json!([{"id": 1, "kind": "skipped", "text": "t", "item_title": "2주차", "reason": "재생이 멈춰 건너뛰었어요"}]);
        assert_eq!(
            apply_external(
                &mut state,
                &external_view("a", "playing", true, skipped.clone()),
                Instant::now()
            )
            .len(),
            1
        );
        assert!(apply_external(
            &mut state,
            &external_view("a", "playing", true, skipped.clone()),
            Instant::now()
        )
        .is_empty());
        // Another run restarts the ids at 1.
        assert_eq!(
            apply_external(
                &mut state,
                &external_view("b", "playing", true, skipped),
                Instant::now()
            )
            .len(),
            1
        );
    }

    #[test]
    fn the_in_app_queue_wins_over_an_external_one() {
        let mut state = RuntimeState::new();
        state.core = QueueCore::new(vec![
            QueueItem::new("1", "앱 강의", Some(600.0)).with_course("9", "c")
        ]);
        state.core.start();
        let sent = apply_external(
            &mut state,
            &external_view("r1", "playing", true, serde_json::json!([])),
            Instant::now(),
        );
        assert!(sent.is_empty() && state.external.is_none());
        assert_eq!(
            View::of(&state).snapshot.current_title.as_deref(),
            Some("앱 강의")
        );

        // Started later in app mode, a stale external run disappears from the menu bar.
        state.core.stop();
        let mut other = RuntimeState::new();
        apply_external(
            &mut other,
            &external_view("r1", "playing", true, serde_json::json!([])),
            Instant::now(),
        );
        other.core = QueueCore::new(vec![
            QueueItem::new("1", "앱 강의", Some(600.0)).with_course("9", "c")
        ]);
        other.core.start();
        assert_eq!(
            View::of(&other).snapshot.current_title.as_deref(),
            Some("앱 강의")
        );
    }

    #[test]
    fn a_finished_in_app_run_gives_way_to_a_new_external_run() {
        let mut state = RuntimeState::new();
        state.core = QueueCore::new(vec![
            QueueItem::new("1", "앱 강의", Some(600.0)).with_course("9", "c")
        ]);
        state.core.start();
        state.core.on_ended();
        assert_eq!(state.core.phase(), Phase::Finished);
        apply_external(
            &mut state,
            &external_view("r1", "playing", true, serde_json::json!([])),
            Instant::now(),
        );
        assert_eq!(
            View::of(&state).snapshot.current_title.as_deref(),
            Some("1주차 강의")
        );
    }

    #[test]
    fn runtime_wiring_keeps_the_rules_of_the_playback_window() {
        let source = include_str!("watch_runtime.rs");
        let production = source
            .split("#[cfg(test)]")
            .next()
            .expect("production part");
        // Small, always on top, user-movable, and never grabbing the keyboard on its own.
        assert!(production.contains(".always_on_top(true)"));
        assert!(production.contains(".focused(false)"));
        assert!(production.contains(".resizable(true)"));
        assert!(production.contains("WATCH_WINDOW_WIDTH"));
        assert!(production.contains("with_watch_assist("));
        // One window serves the queue: later lectures are navigated into it.
        assert!(production.contains("bridge_session_and_navigate(&window"));
        // Signals come in over IPC (a command), never as navigations of the playback window.
        assert!(production.contains("pub(crate) fn watch_signal("));
        assert!(production.contains("webview.label()"));
        for forbidden in ["on_navigation", "ssu-watch"] {
            assert!(!production.contains(forbidden), "{forbidden}");
        }
        // The external (Chrome) mode feeds the same tray code and is only reachable from the main window.
        assert!(production.contains("pub(crate) fn watch_external_status("));
        assert!(production.contains("external_command(app, \"stop\")"));
        // No attempt to fake what the page reports.
        for forbidden in ["visibilityState", "playbackRate", "set_cookie"] {
            assert!(!production.contains(forbidden), "{forbidden}");
        }
    }
}
