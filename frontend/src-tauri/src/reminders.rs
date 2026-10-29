//! Deadline reminders.
//!
//! The UI writes a compact deadline cache (`deadlines.json`) whenever it has fresh data. Because
//! the LMS credentials only live in memory while the app runs, everything here works from that
//! cache alone: the in-app checker thread and the `--check-reminders` LaunchAgent run both read
//! the cache, decide which reminders are due and remember what was sent in `notified.json`.
//!
//! The rule (`plan_reminders`) is pure and takes `now` as an argument so it can be tested.

use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

pub const CACHE_FILE: &str = "deadlines.json";
pub const NOTIFIED_FILE: &str = "notified.json";
pub const SETTINGS_FILE: &str = "settings.json";
const LOCK_FILE: &str = "reminders.lock";
const SETTINGS_KEY: &str = "reminders_enabled";
/// Must equal `identifier` in `tauri.conf.json` (checked by a test); the LaunchAgent run has no
/// Tauri runtime to ask for the app data directory.
pub const APP_IDENTIFIER: &str = "com.local.ssu-lecture-downloader";
pub const CHECK_ARG: &str = "--check-reminders";

const HOUR: i64 = 3600;
const PRUNE_AFTER_SECS: i64 = 30 * 24 * HOUR;
const CHECK_INTERVAL: Duration = Duration::from_secs(10 * 60);
const MAX_ITEMS: usize = 2000;
const MAX_TEXT_CHARS: usize = 120;

// ------------------------------------------------------------------------- data

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CachedItem {
    /// Stable id: `course_id:kind:module_item_id` or `course_id:kind:<title hash>`.
    pub id: String,
    /// Short course name (no term or section decoration).
    pub course: String,
    /// `video`, `assignment` or `quiz`.
    pub kind: String,
    pub title: String,
    /// Next open deadline (unix seconds): the due time, or the end of a late window.
    pub deadline_at: i64,
    /// `deadline_at` closes a late window (the regular due time has passed).
    #[serde(default)]
    pub late: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeadlineCache {
    pub saved_at: i64,
    #[serde(default)]
    pub items: Vec<CachedItem>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    /// ~24 hours before the deadline.
    Day,
    /// ~3 hours before the deadline.
    ThreeHours,
}

impl Stage {
    pub fn as_str(self) -> &'static str {
        match self {
            Stage::Day => "24h",
            Stage::ThreeHours => "3h",
        }
    }

    fn lead_secs(self) -> i64 {
        match self {
            Stage::Day => 24 * HOUR,
            Stage::ThreeHours => 3 * HOUR,
        }
    }

    /// `deadline - lead <= now < deadline`.
    fn applies(self, deadline_at: i64, now: i64) -> bool {
        deadline_at - self.lead_secs() <= now && now < deadline_at
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct NotifiedEntry {
    pub id: String,
    pub stage: String,
    /// The deadline the reminder was for: a moved deadline is a new key and notifies again.
    pub deadline_at: i64,
    pub sent_at: i64,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct NotifiedLog {
    #[serde(default)]
    pub entries: Vec<NotifiedEntry>,
}

impl NotifiedLog {
    fn has(&self, id: &str, stage: Stage, deadline_at: i64) -> bool {
        self.entries
            .iter()
            .any(|e| e.id == id && e.stage == stage.as_str() && e.deadline_at == deadline_at)
    }

    fn record(&mut self, id: &str, stage: Stage, deadline_at: i64, now: i64) {
        if !self.has(id, stage, deadline_at) {
            self.entries.push(NotifiedEntry {
                id: id.to_string(),
                stage: stage.as_str().to_string(),
                deadline_at,
                sent_at: now,
            });
        }
    }

    /// Drops entries sent more than 30 days ago; returns whether anything was removed.
    pub fn prune(&mut self, now: i64) -> bool {
        let before = self.entries.len();
        self.entries.retain(|e| e.sent_at > now - PRUNE_AFTER_SECS);
        self.entries.len() != before
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reminder {
    pub id: String,
    pub deadline_at: i64,
    /// The stage that is announced.
    pub stage: Stage,
    /// Every stage this notification covers (the announced one and any less urgent one).
    pub covers: Vec<Stage>,
    pub title: String,
    pub body: String,
}

// ------------------------------------------------------------------------- rule

fn kind_label(kind: &str) -> &'static str {
    match kind {
        "video" => "강의",
        "assignment" => "과제",
        "quiz" => "퀴즈",
        _ => "항목",
    }
}

fn truncate_chars(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let mut cut: String = text.chars().take(max.saturating_sub(1)).collect();
    cut.push('…');
    cut
}

/// Days since 1970-01-01 -> (year, month, day), proleptic Gregorian.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if month <= 2 { year + 1 } else { year }, month, day)
}

/// `10/22(목) 23:59` for a unix time shown at `utc_offset_secs` east of UTC.
pub fn format_when(at: i64, utc_offset_secs: i64) -> String {
    const WEEKDAYS: [&str; 7] = ["일", "월", "화", "수", "목", "금", "토"];
    let local = at + utc_offset_secs;
    let days = local.div_euclid(86_400);
    let secs = local.rem_euclid(86_400);
    let (_, month, day) = civil_from_days(days);
    let weekday = WEEKDAYS[(days + 4).rem_euclid(7) as usize]; // 1970-01-01 was a Thursday
    format!(
        "{month}/{day}({weekday}) {:02}:{:02}",
        secs / 3600,
        secs % 3600 / 60
    )
}

fn message(item: &CachedItem, utc_offset_secs: i64) -> (String, String) {
    let when = format_when(item.deadline_at, utc_offset_secs);
    let title = format!("기한 임박: {}", truncate_chars(&item.course, 40));
    let tail = if item.late {
        format!("{when}까지 지각 인정")
    } else {
        format!("{when}까지")
    };
    let body = format!(
        "{} · {} · {}",
        kind_label(&item.kind),
        truncate_chars(&item.title, MAX_TEXT_CHARS),
        tail
    );
    (title, body)
}

/// The reminders that are due at `now` and not yet in `log`.
///
/// Per item at most one notification is produced: the most urgent applicable stage. A stage that
/// is no longer the right one to announce (e.g. the 24h reminder once the 3h window has opened)
/// is covered by it, so that it is not sent afterwards. `utc_offset` maps a unix time to the
/// display time zone's offset in seconds.
pub fn plan_reminders(
    items: &[CachedItem],
    log: &NotifiedLog,
    now: i64,
    utc_offset: &dyn Fn(i64) -> i64,
) -> Vec<Reminder> {
    let mut reminders = Vec::new();
    for item in items {
        let applicable: Vec<Stage> = [Stage::ThreeHours, Stage::Day]
            .into_iter()
            .filter(|stage| stage.applies(item.deadline_at, now))
            .collect();
        let Some(&stage) = applicable.first() else {
            continue;
        };
        if log.has(&item.id, stage, item.deadline_at) {
            // Announced already (a sent 3h reminder also covers the 24h one).
            continue;
        }
        let (title, body) = message(item, utc_offset(item.deadline_at));
        reminders.push(Reminder {
            id: item.id.clone(),
            deadline_at: item.deadline_at,
            stage,
            covers: applicable,
            title,
            body,
        });
    }
    reminders.sort_by_key(|r| r.deadline_at);
    reminders
}

/// Cleans a cache written by the UI: bounded size and text, no empty ids, no duplicate ids.
pub fn sanitize_items(items: Vec<CachedItem>) -> Vec<CachedItem> {
    let mut seen = std::collections::HashSet::new();
    items
        .into_iter()
        .filter(|item| !item.id.trim().is_empty() && item.deadline_at > 0)
        .filter(|item| seen.insert(item.id.clone()))
        .take(MAX_ITEMS)
        .map(|mut item| {
            item.id = truncate_chars(&item.id, 200);
            item.course = truncate_chars(item.course.trim(), 60);
            item.title = truncate_chars(item.title.trim(), MAX_TEXT_CHARS);
            item
        })
        .collect()
}

// ---------------------------------------------------------------------- notifier

/// Posts a macOS notification. Abstracted so tests never show a real one.
pub trait Notifier: Send + Sync {
    fn notify(&self, title: &str, body: &str) -> Result<(), String>;
}

// ------------------------------------------------------------------------ storage

pub fn write_atomic(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut tmp_name = path.file_name().unwrap_or_default().to_os_string();
    tmp_name.push(format!(".tmp-{}", std::process::id()));
    let tmp = path.with_file_name(tmp_name);
    let result = (|| {
        let mut options = fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&tmp)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&tmp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Option<T> {
    serde_json::from_slice(&fs::read(path).ok()?).ok()
}

pub fn read_cache(dir: &Path) -> Option<DeadlineCache> {
    read_json(&dir.join(CACHE_FILE))
}

pub fn write_cache(dir: &Path, items: Vec<CachedItem>, now: i64) -> Result<DeadlineCache, String> {
    let cache = DeadlineCache {
        saved_at: now,
        items: sanitize_items(items),
    };
    let bytes = serde_json::to_vec(&cache).map_err(|e| e.to_string())?;
    write_atomic(&dir.join(CACHE_FILE), &bytes)
        .map_err(|e| format!("기한 정보를 저장할 수 없습니다: {e}"))?;
    Ok(cache)
}

fn read_log(dir: &Path) -> NotifiedLog {
    read_json(&dir.join(NOTIFIED_FILE)).unwrap_or_default()
}

fn write_log(dir: &Path, log: &NotifiedLog) -> Result<(), String> {
    let bytes = serde_json::to_vec(log).map_err(|e| e.to_string())?;
    write_atomic(&dir.join(NOTIFIED_FILE), &bytes).map_err(|e| e.to_string())
}

/// The preference in `settings.json`, or `None` when the user never chose.
pub fn read_enabled_setting(dir: &Path) -> Option<bool> {
    let value: serde_json::Value = read_json(&dir.join(SETTINGS_FILE))?;
    value.get(SETTINGS_KEY)?.as_bool()
}

/// Persists the preference and keeps any other key already in `settings.json`.
pub fn write_enabled_setting(dir: &Path, enabled: bool) -> Result<(), String> {
    let path = dir.join(SETTINGS_FILE);
    let mut map = match read_json::<serde_json::Value>(&path) {
        Some(serde_json::Value::Object(map)) => map,
        _ => serde_json::Map::new(),
    };
    map.insert(SETTINGS_KEY.to_string(), serde_json::Value::Bool(enabled));
    let bytes =
        serde_json::to_vec_pretty(&serde_json::Value::Object(map)).map_err(|e| e.to_string())?;
    write_atomic(&path, &bytes).map_err(|e| format!("설정을 저장할 수 없습니다: {e}"))
}

/// Holds an exclusive advisory lock so the app and the LaunchAgent run never check at once
/// (which would send a reminder twice). Released when dropped.
struct DirLock {
    _file: fs::File,
}

impl DirLock {
    fn acquire(dir: &Path) -> Option<DirLock> {
        fs::create_dir_all(dir).ok()?;
        let file = fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .write(true)
            .open(dir.join(LOCK_FILE))
            .ok()?;
        crate::platform::lock_exclusive(&file);
        Some(DirLock { _file: file })
    }
}

/// Local UTC offset in seconds at unix time `at`.
#[cfg(unix)]
pub fn local_utc_offset(at: i64) -> i64 {
    unsafe {
        let time: libc::time_t = at as libc::time_t;
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&time, &mut tm).is_null() {
            return 0;
        }
        tm.tm_gmtoff as i64
    }
}

#[cfg(not(unix))]
pub fn local_utc_offset(_at: i64) -> i64 {
    0
}

pub fn unix_now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Reads the cache, sends every due reminder and records it. Returns how many were sent.
/// A reminder counts as sent only if the notifier succeeded; otherwise the next check retries.
pub fn check_and_notify(
    dir: &Path,
    now: i64,
    notifier: &dyn Notifier,
    utc_offset: &dyn Fn(i64) -> i64,
) -> Result<usize, String> {
    let _lock = DirLock::acquire(dir);
    let mut log = read_log(dir);
    let mut dirty = log.prune(now);
    let mut sent = 0;
    let mut last_error = None;
    if let Some(cache) = read_cache(dir) {
        for reminder in plan_reminders(&cache.items, &log, now, utc_offset) {
            match notifier.notify(&reminder.title, &reminder.body) {
                Ok(()) => {
                    for stage in &reminder.covers {
                        log.record(&reminder.id, *stage, reminder.deadline_at, now);
                    }
                    dirty = true;
                    sent += 1;
                }
                Err(error) => last_error = Some(error),
            }
        }
    }
    if dirty {
        write_log(dir, &log)?;
    }
    match last_error {
        Some(error) if sent == 0 => Err(error),
        _ => Ok(sent),
    }
}

// ----------------------------------------------------------------- argv / CLI run

/// Whether the process was started by the LaunchAgent to run one reminder check.
pub fn is_check_invocation<I, S>(args: I) -> bool
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    args.into_iter()
        .skip(1)
        .any(|arg| arg.as_ref() == CHECK_ARG)
}

/// The `--check-reminders` run: no window, no Tauri, no backend. Returns the exit code.
pub fn run_check_cli(home: Option<PathBuf>) -> i32 {
    let Some(home) = home else {
        eprintln!("reminders: cannot resolve the home directory");
        return 1;
    };
    let dir = crate::platform::app_data_dir_for_home(&home);
    // The agent only exists while reminders are on; a missing preference means on.
    if read_enabled_setting(&dir) == Some(false) {
        return 0;
    }
    match check_and_notify(
        &dir,
        unix_now(),
        &crate::platform::PlatformNotifier,
        &local_utc_offset,
    ) {
        Ok(_) => 0,
        Err(error) => {
            eprintln!("reminders: {error}");
            1
        }
    }
}

// ----------------------------------------------------------- in-app checker thread

/// Background checker of the running app: every 10 minutes and on each cache write.
pub struct ReminderService {
    dir: PathBuf,
    default_enabled: bool,
    trigger: Sender<()>,
}

impl ReminderService {
    pub fn start(
        dir: PathBuf,
        default_enabled: bool,
        notifier: Arc<dyn Notifier>,
    ) -> Arc<ReminderService> {
        let (trigger, receiver) = mpsc::channel::<()>();
        let service = Arc::new(ReminderService {
            dir: dir.clone(),
            default_enabled,
            trigger,
        });
        thread::Builder::new()
            .name("deadline-reminders".to_string())
            .spawn(move || loop {
                if read_enabled_setting(&dir).unwrap_or(default_enabled) {
                    if let Err(error) =
                        check_and_notify(&dir, unix_now(), notifier.as_ref(), &local_utc_offset)
                    {
                        eprintln!("reminders: {error}");
                    }
                }
                match receiver.recv_timeout(CHECK_INTERVAL) {
                    Ok(()) | Err(RecvTimeoutError::Timeout) => {}
                    Err(RecvTimeoutError::Disconnected) => break,
                }
            })
            .ok();
        service
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    pub fn enabled(&self) -> bool {
        read_enabled_setting(&self.dir).unwrap_or(self.default_enabled)
    }

    /// Asks the checker thread to run now (after a cache write or turning reminders on).
    pub fn check_soon(&self) {
        let _ = self.trigger.send(());
    }
}

// -------------------------------------------------------------------------- tests

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    // 2026-10-16 12:00:00 UTC.
    const NOW: i64 = 1_792_152_000;
    const KST: i64 = 9 * HOUR;

    fn item(id: &str, deadline_at: i64) -> CachedItem {
        CachedItem {
            id: id.to_string(),
            course: "운영체제".to_string(),
            kind: "assignment".to_string(),
            title: "과제 1".to_string(),
            deadline_at,
            late: false,
        }
    }

    fn kst(_: i64) -> i64 {
        KST
    }

    fn plan(items: &[CachedItem], log: &NotifiedLog, now: i64) -> Vec<Reminder> {
        plan_reminders(items, log, now, &kst)
    }

    struct Recorder {
        sent: Mutex<Vec<(String, String)>>,
        fail: bool,
    }

    impl Recorder {
        fn new(fail: bool) -> Recorder {
            Recorder {
                sent: Mutex::new(Vec::new()),
                fail,
            }
        }
    }

    impl Notifier for Recorder {
        fn notify(&self, title: &str, body: &str) -> Result<(), String> {
            if self.fail {
                return Err("nope".to_string());
            }
            self.sent
                .lock()
                .unwrap()
                .push((title.to_string(), body.to_string()));
            Ok(())
        }
    }

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "ssu-reminders-{name}-{}-{}",
            std::process::id(),
            unix_now()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn nothing_is_due_outside_the_windows() {
        let items = [item("a", NOW + 25 * HOUR)];
        assert!(plan(&items, &NotifiedLog::default(), NOW).is_empty());
        // The deadline itself and anything after it never notifies.
        let items = [item("a", NOW)];
        assert!(plan(&items, &NotifiedLog::default(), NOW).is_empty());
        let items = [item("a", NOW - 60)];
        assert!(plan(&items, &NotifiedLog::default(), NOW).is_empty());
    }

    #[test]
    fn day_reminder_opens_exactly_24h_before() {
        let items = [item("a", NOW + 24 * HOUR)];
        let due = plan(&items, &NotifiedLog::default(), NOW);
        assert_eq!(due.len(), 1);
        assert_eq!(due[0].stage, Stage::Day);
        assert!(plan(&items, &NotifiedLog::default(), NOW - 1).is_empty());
    }

    #[test]
    fn three_hour_reminder_opens_exactly_3h_before_and_follows_the_day_one() {
        let deadline = NOW + 3 * HOUR;
        let items = [item("a", deadline)];
        let mut log = NotifiedLog::default();
        log.record("a", Stage::Day, deadline, NOW - 20 * HOUR);
        let due = plan(&items, &log, NOW);
        assert_eq!(due.len(), 1);
        assert_eq!(due[0].stage, Stage::ThreeHours);
        // One second earlier only the (already sent) 24h window is open.
        assert!(plan(&items, &log, NOW - 1).is_empty());
    }

    #[test]
    fn a_reminder_is_sent_once() {
        let deadline = NOW + 10 * HOUR;
        let items = [item("a", deadline)];
        let mut log = NotifiedLog::default();
        let due = plan(&items, &log, NOW);
        assert_eq!(due.len(), 1);
        for stage in &due[0].covers {
            log.record("a", *stage, deadline, NOW);
        }
        assert!(plan(&items, &log, NOW + HOUR).is_empty());
    }

    #[test]
    fn first_seen_inside_the_3h_window_sends_only_the_3h_reminder() {
        let deadline = NOW + HOUR;
        let items = [item("a", deadline)];
        let mut log = NotifiedLog::default();
        let due = plan(&items, &log, NOW);
        assert_eq!(due.len(), 1);
        assert_eq!(due[0].stage, Stage::ThreeHours);
        assert_eq!(due[0].covers, vec![Stage::ThreeHours, Stage::Day]);
        for stage in &due[0].covers {
            log.record("a", *stage, deadline, NOW);
        }
        // The 24h window is still open, but it was covered.
        assert!(plan(&items, &log, NOW + 60).is_empty());
    }

    #[test]
    fn a_moved_deadline_notifies_again() {
        let mut log = NotifiedLog::default();
        log.record("a", Stage::Day, NOW + 10 * HOUR, NOW - HOUR);
        let items = [item("a", NOW + 12 * HOUR)];
        assert_eq!(plan(&items, &log, NOW).len(), 1);
        let same = [item("a", NOW + 10 * HOUR)];
        assert!(plan(&same, &log, NOW).is_empty());
    }

    #[test]
    fn reminders_are_ordered_by_deadline() {
        let items = [item("late", NOW + 20 * HOUR), item("soon", NOW + HOUR)];
        let ids: Vec<_> = plan(&items, &NotifiedLog::default(), NOW)
            .into_iter()
            .map(|r| r.id)
            .collect();
        assert_eq!(ids, ["soon", "late"]);
    }

    #[test]
    fn korean_text_for_each_kind_and_for_late_windows() {
        // 2026-10-16 12:00 UTC + 11h = 23:00 UTC = 2026-10-17 08:00 KST (a Saturday).
        let deadline = NOW + 11 * HOUR;
        let mut video = item("v", deadline);
        video.kind = "video".to_string();
        video.title = "3주차 1교시".to_string();
        let mut quiz = item("q", deadline);
        quiz.kind = "quiz".to_string();
        let mut late = item("l", deadline);
        late.late = true;
        let due = plan(&[video, quiz, late], &NotifiedLog::default(), NOW);
        let bodies: Vec<_> = due.iter().map(|r| r.body.as_str()).collect();
        assert!(bodies.contains(&"강의 · 3주차 1교시 · 10/17(토) 08:00까지"));
        assert!(bodies.contains(&"퀴즈 · 과제 1 · 10/17(토) 08:00까지"));
        assert!(bodies.contains(&"과제 · 과제 1 · 10/17(토) 08:00까지 지각 인정"));
        assert!(due.iter().all(|r| r.title == "기한 임박: 운영체제"));
    }

    #[test]
    fn format_when_handles_offsets_weekdays_and_day_rollover() {
        // 1970-01-01 00:00 UTC was a Thursday.
        assert_eq!(format_when(0, 0), "1/1(목) 00:00");
        assert_eq!(format_when(0, KST), "1/1(목) 09:00");
        assert_eq!(format_when(0, -HOUR), "12/31(수) 23:00");
        // Leap day 2024-02-29 is a Thursday.
        assert_eq!(format_when(1_709_208_000, 0), "2/29(목) 12:00");
    }

    #[test]
    fn prune_drops_entries_older_than_30_days() {
        let mut log = NotifiedLog::default();
        log.record("old", Stage::Day, NOW, NOW - 31 * 24 * HOUR);
        log.record("edge", Stage::Day, NOW, NOW - 30 * 24 * HOUR);
        log.record("new", Stage::Day, NOW, NOW - 29 * 24 * HOUR);
        assert!(log.prune(NOW));
        let ids: Vec<_> = log.entries.iter().map(|e| e.id.as_str()).collect();
        assert_eq!(ids, ["new"]);
        assert!(!log.prune(NOW));
    }

    #[test]
    fn sanitize_drops_bad_rows_and_bounds_text() {
        let mut long = item("long", NOW + HOUR);
        long.title = "가".repeat(500);
        let cleaned = sanitize_items(vec![
            item("", NOW + HOUR),
            item("zero", 0),
            item("dup", NOW + HOUR),
            item("dup", NOW + 2 * HOUR),
            long,
        ]);
        let ids: Vec<_> = cleaned.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids, ["dup", "long"]);
        assert_eq!(cleaned[0].deadline_at, NOW + HOUR);
        assert_eq!(cleaned[1].title.chars().count(), MAX_TEXT_CHARS);
    }

    #[test]
    fn argv_detection_ignores_the_program_name() {
        assert!(is_check_invocation(["app", "--check-reminders"]));
        assert!(is_check_invocation(["app", "-x", "--check-reminders"]));
        assert!(!is_check_invocation(["app"]));
        assert!(!is_check_invocation(["--check-reminders"]));
        assert!(!is_check_invocation(["app", "--check-reminders-now"]));
        assert!(!is_check_invocation(Vec::<String>::new()));
    }

    #[test]
    fn data_dir_matches_the_tauri_identifier() {
        let conf: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(conf["identifier"], APP_IDENTIFIER);
    }

    #[test]
    fn check_and_notify_sends_records_and_does_not_repeat() {
        let dir = temp_dir("check");
        write_cache(&dir, vec![item("a", NOW + 2 * HOUR)], NOW).unwrap();
        let notifier = Recorder::new(false);
        assert_eq!(check_and_notify(&dir, NOW, &notifier, &kst), Ok(1));
        assert_eq!(check_and_notify(&dir, NOW + 600, &notifier, &kst), Ok(0));
        let sent = notifier.sent.lock().unwrap();
        assert_eq!(sent.len(), 1);
        assert_eq!(sent[0].0, "기한 임박: 운영체제");
        let log = read_log(&dir);
        assert_eq!(log.entries.len(), 2); // 3h announced, 24h covered
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn failed_delivery_is_retried_and_stale_log_entries_are_pruned() {
        let dir = temp_dir("retry");
        write_cache(&dir, vec![item("a", NOW + 2 * HOUR)], NOW).unwrap();
        let mut log = NotifiedLog::default();
        log.record(
            "gone",
            Stage::Day,
            NOW - 40 * 24 * HOUR,
            NOW - 40 * 24 * HOUR,
        );
        write_log(&dir, &log).unwrap();

        let failing = Recorder::new(true);
        assert!(check_and_notify(&dir, NOW, &failing, &kst).is_err());
        let after_failure = read_log(&dir);
        assert!(after_failure.entries.is_empty(), "pruned, nothing recorded");

        let working = Recorder::new(false);
        assert_eq!(check_and_notify(&dir, NOW + 60, &working, &kst), Ok(1));
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn missing_cache_is_not_an_error() {
        let dir = temp_dir("nocache");
        assert_eq!(
            check_and_notify(&dir, NOW, &Recorder::new(false), &kst),
            Ok(0)
        );
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn cache_is_written_atomically_with_saved_at_and_nothing_else() {
        let dir = temp_dir("cache");
        let cache = write_cache(&dir, vec![item("a", NOW + HOUR)], NOW).unwrap();
        assert_eq!(cache.saved_at, NOW);
        assert_eq!(read_cache(&dir), Some(cache));
        let names: Vec<_> = fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        assert_eq!(names, ["deadlines.json"], "no temp file left behind");
        let raw = fs::read_to_string(dir.join(CACHE_FILE)).unwrap();
        for field in ["url", "cookie", "token", "password"] {
            assert!(!raw.contains(field), "{field}");
        }
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn enabled_setting_round_trips_and_keeps_other_keys() {
        let dir = temp_dir("settings");
        assert_eq!(read_enabled_setting(&dir), None);
        fs::write(dir.join(SETTINGS_FILE), r#"{"other": 1}"#).unwrap();
        write_enabled_setting(&dir, false).unwrap();
        assert_eq!(read_enabled_setting(&dir), Some(false));
        write_enabled_setting(&dir, true).unwrap();
        assert_eq!(read_enabled_setting(&dir), Some(true));
        let value: serde_json::Value = read_json(&dir.join(SETTINGS_FILE)).unwrap();
        assert_eq!(value["other"], 1);
        fs::remove_dir_all(dir).ok();
    }
}
