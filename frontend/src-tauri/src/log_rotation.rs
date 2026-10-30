//! Size-capped log files for the app log dir (`backend.log`, `watch.log`).
//!
//! One small writer used by both logs. When the next line would push the file past its cap, the
//! file is renamed to `<name>.previous.log` (replacing an older one) and writing continues in a
//! fresh file, so a log never grows past its cap and at most one previous file exists. Rotation
//! happens between lines, never inside one, and every line is flushed as it is written so a crash
//! leaves the tail on disk.

use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// `backend.log` (the sidecar's stdout and stderr) rotates at this size.
pub(crate) const BACKEND_LOG_CAP: u64 = 5 * 1024 * 1024;
/// `watch.log` (the playback step log) rotates at this size.
pub(crate) const WATCH_LOG_CAP: u64 = 2 * 1024 * 1024;
/// A line longer than this is written in pieces of this size (the pipe reader's memory bound).
const MAX_CHUNK: usize = 64 * 1024;

/// `backend.log` -> `backend.previous.log`.
pub(crate) fn previous_path(path: &Path) -> PathBuf {
    path.with_extension("previous.log")
}

pub(crate) struct RotatingLog {
    path: PathBuf,
    cap: u64,
    file: Option<File>,
    size: u64,
}

impl RotatingLog {
    /// Continues the file at `path` (creating it), e.g. `watch.log` across runs.
    pub(crate) fn append(path: &Path, cap: u64) -> io::Result<Self> {
        let mut log = Self {
            path: path.to_path_buf(),
            cap,
            file: None,
            size: 0,
        };
        log.reopen()?;
        Ok(log)
    }

    /// Starts a new file at `path`. A log left by the previous run is kept as the previous file,
    /// so a crash before a restart stays diagnosable.
    pub(crate) fn fresh(path: &Path, cap: u64) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        if path.exists() {
            let _ = fs::rename(path, previous_path(path));
        }
        let file = File::create(path)?;
        Ok(Self {
            path: path.to_path_buf(),
            cap,
            file: Some(file),
            size: 0,
        })
    }

    fn reopen(&mut self) -> io::Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        self.size = file.metadata()?.len();
        self.file = Some(file);
        Ok(())
    }

    fn rotate(&mut self) -> io::Result<()> {
        self.file = None;
        if fs::rename(&self.path, previous_path(&self.path)).is_err() {
            // The previous file could not be replaced: still never grow past the cap.
            fs::remove_file(&self.path)?;
        }
        self.reopen()
    }

    /// Writes one whole line (it carries its own newline) and flushes it. Rotates first when the
    /// line would not fit; a line larger than the cap gets a fresh file to itself.
    pub(crate) fn write_line(&mut self, line: &[u8]) -> io::Result<()> {
        if self.file.is_none() {
            self.reopen()?;
        }
        if self.size > 0 && self.size + line.len() as u64 > self.cap {
            self.rotate()?;
        }
        let Some(file) = self.file.as_mut() else {
            return Err(io::Error::other("log file is not open"));
        };
        let written = file.write_all(line).and_then(|()| file.flush());
        match written {
            Ok(()) => {
                self.size += line.len() as u64;
                Ok(())
            }
            Err(error) => {
                // Reopen (and re-measure) on the next line.
                self.file = None;
                Err(error)
            }
        }
    }
}

static APPEND_LOCK: Mutex<()> = Mutex::new(());

/// Appends one line to a capped log that is opened per call (`watch.log`, written from several
/// threads). Failures are returned; callers of a diagnostic log ignore them.
pub(crate) fn append_line(path: &Path, cap: u64, line: &str) -> io::Result<()> {
    let _guard = APPEND_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    RotatingLog::append(path, cap)?.write_line(line.as_bytes())
}

/// A rotating log shared by the threads that drain a child's stdout and stderr.
pub(crate) type SharedLog = Arc<Mutex<RotatingLog>>;

pub(crate) fn share(log: RotatingLog) -> SharedLog {
    Arc::new(Mutex::new(log))
}

/// Copies `reader` into `log` line by line until it ends, and returns how many bytes it drained.
/// A failing disk write never stops the draining: the child must not block on a full pipe.
fn pump_lines<R: Read>(reader: R, log: &Mutex<RotatingLog>) -> u64 {
    let mut reader = BufReader::new(reader);
    let mut buffer = Vec::new();
    let mut drained = 0u64;
    loop {
        buffer.clear();
        match (&mut reader)
            .take(MAX_CHUNK as u64)
            .read_until(b'\n', &mut buffer)
        {
            Ok(0) => break,
            Ok(read) => drained += read as u64,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => break,
        }
        // The last line of a stream may lack its newline; keep the two streams' lines apart.
        if buffer.len() < MAX_CHUNK && !buffer.ends_with(b"\n") {
            buffer.push(b'\n');
        }
        let mut log = log.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let _ = log.write_line(&buffer);
    }
    drained
}

/// The threads copying a child's output into its log.
pub(crate) struct LogPump {
    finished: Receiver<()>,
    threads: usize,
}

impl LogPump {
    /// Starts one thread per stream. A stream that could not be taken is simply absent.
    pub(crate) fn start(log: &SharedLog, streams: Vec<Box<dyn Read + Send>>) -> Self {
        let (done, finished): (Sender<()>, Receiver<()>) = channel();
        let mut threads = 0;
        for stream in streams {
            let log = Arc::clone(log);
            let done = done.clone();
            let spawned = std::thread::Builder::new()
                .name("ssu-backend-log".to_string())
                .spawn(move || {
                    pump_lines(stream, &log);
                    let _ = done.send(());
                });
            if spawned.is_ok() {
                threads += 1;
            }
        }
        Self { finished, threads }
    }

    /// Waits until every stream has ended (the child is gone) or `timeout` passes, so the log
    /// holds everything the child wrote before it is read.
    pub(crate) fn drain(&self, timeout: Duration) {
        let deadline = Instant::now() + timeout;
        for _ in 0..self.threads {
            let left = deadline.saturating_duration_since(Instant::now());
            if self.finished.recv_timeout(left).is_err() {
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "ssu-log-rotation-{tag}-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        fs::create_dir_all(&dir).expect("temp dir");
        dir
    }

    fn line(n: usize) -> String {
        format!("line {n:04} {}\n", "x".repeat(20))
    }

    fn lines_of(path: &Path) -> Vec<String> {
        fs::read_to_string(path)
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    }

    #[test]
    fn rotates_at_the_cap_and_keeps_exactly_one_previous_file() {
        let dir = temp_dir("cap");
        let path = dir.join("backend.log");
        let cap = 1000;
        let mut log = RotatingLog::fresh(&path, cap).unwrap();
        let each = line(0).len() as u64;
        let per_file = cap / each;
        for n in 0..per_file {
            log.write_line(line(n as usize).as_bytes()).unwrap();
        }
        assert!(
            !previous_path(&path).exists(),
            "nothing rotated up to the cap"
        );
        log.write_line(line(per_file as usize).as_bytes()).unwrap();
        assert_eq!(lines_of(&path), [line(per_file as usize).trim_end()]);
        assert_eq!(lines_of(&previous_path(&path)).len() as u64, per_file);

        // Many more rotations: still one previous file, only the newest lines survive.
        for n in per_file + 1..per_file * 10 {
            log.write_line(line(n as usize).as_bytes()).unwrap();
        }
        let mut names: Vec<String> = fs::read_dir(&dir)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        names.sort();
        assert_eq!(names, ["backend.log", "backend.previous.log"]);
        for file in [&path, &previous_path(&path)] {
            assert!(fs::metadata(file).unwrap().len() <= cap);
        }
        let last = lines_of(&path).last().cloned().unwrap();
        assert_eq!(last, line(per_file as usize * 10 - 1).trim_end());
        let oldest_kept = lines_of(&previous_path(&path))[0].clone();
        assert!(
            !oldest_kept.contains("0000"),
            "the first lines were dropped"
        );
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn lines_are_never_split_across_files() {
        let dir = temp_dir("whole");
        let path = dir.join("watch.log");
        let mut log = RotatingLog::fresh(&path, 500).unwrap();
        // Lines of different lengths so the cap falls inside a line at some point.
        for n in 0..200 {
            let text = format!("{n} {}\n", "y".repeat(n % 37));
            log.write_line(text.as_bytes()).unwrap();
        }
        for file in [path.clone(), previous_path(&path)] {
            let text = fs::read_to_string(&file).unwrap();
            assert!(text.ends_with('\n'), "{file:?} ends on a line boundary");
            for entry in text.lines() {
                let (number, padding) = entry.split_once(' ').unwrap_or((entry, ""));
                let n: usize = number.parse().expect("a line starts with its number");
                assert_eq!(padding.len(), n % 37, "line {n} is whole");
            }
        }
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_fresh_log_keeps_the_previous_runs_file() {
        let dir = temp_dir("fresh");
        let path = dir.join("backend.log");
        fs::write(&path, "last run\n").unwrap();
        fs::write(previous_path(&path), "two runs ago\n").unwrap();
        let mut log = RotatingLog::fresh(&path, 1000).unwrap();
        log.write_line(b"this run\n").unwrap();
        assert_eq!(lines_of(&previous_path(&path)), ["last run"]);
        assert_eq!(lines_of(&path), ["this run"]);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn append_line_continues_the_file_across_calls_and_rotates() {
        let dir = temp_dir("append");
        let path = dir.join("nested").join("watch.log");
        for n in 0..40 {
            append_line(&path, 300, &line(n)).unwrap();
        }
        assert!(fs::metadata(&path).unwrap().len() <= 300);
        assert!(fs::metadata(previous_path(&path)).unwrap().len() <= 300);
        assert_eq!(lines_of(&path).last().unwrap(), line(39).trim_end());
        let count = fs::read_dir(path.parent().unwrap()).unwrap().count();
        assert_eq!(count, 2, "the log and one previous file");
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_line_larger_than_the_cap_gets_a_file_of_its_own() {
        let dir = temp_dir("huge");
        let path = dir.join("backend.log");
        let mut log = RotatingLog::fresh(&path, 100).unwrap();
        log.write_line(b"small\n").unwrap();
        let huge = format!("{}\n", "z".repeat(300));
        log.write_line(huge.as_bytes()).unwrap();
        log.write_line(b"after\n").unwrap();
        assert_eq!(lines_of(&previous_path(&path)), ["z".repeat(300)]);
        assert_eq!(lines_of(&path), ["after"]);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn pumping_keeps_line_order_and_ends_a_last_partial_line() {
        let dir = temp_dir("pump");
        let path = dir.join("backend.log");
        let log = share(RotatingLog::fresh(&path, 1000).unwrap());
        let input = "a\nb\nlast without newline";
        let drained = pump_lines(Cursor::new(input.as_bytes().to_vec()), &log);
        assert_eq!(drained, input.len() as u64);
        assert_eq!(lines_of(&path), ["a", "b", "last without newline"]);
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn a_failing_disk_write_never_stops_the_draining() {
        let dir = temp_dir("fail");
        let path = dir.join("backend.log");
        let log = share(RotatingLog::fresh(&path, 1000).unwrap());
        // Make every reopen fail: the log path becomes a directory.
        {
            let mut guard = log.lock().unwrap();
            guard.file = None;
        }
        fs::remove_file(&path).unwrap();
        fs::create_dir(&path).unwrap();
        let input: String = (0..500).map(line).collect();
        let drained = pump_lines(Cursor::new(input.clone().into_bytes()), &log);
        assert_eq!(drained, input.len() as u64, "the whole stream was consumed");
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn two_streams_share_one_log_and_drain_waits_for_both() {
        let dir = temp_dir("pump-two");
        let path = dir.join("backend.log");
        let log = share(RotatingLog::fresh(&path, 100_000).unwrap());
        let stdout: Box<dyn Read + Send> = Box::new(Cursor::new(b"out 1\nout 2\n".to_vec()));
        let stderr: Box<dyn Read + Send> = Box::new(Cursor::new(b"err 1\nerr 2\n".to_vec()));
        let pump = LogPump::start(&log, vec![stdout, stderr]);
        pump.drain(Duration::from_secs(5));
        let all = lines_of(&path);
        assert_eq!(all.len(), 4);
        let position = |text: &str| all.iter().position(|entry| entry == text).unwrap();
        assert!(position("out 1") < position("out 2"));
        assert!(position("err 1") < position("err 2"));
        fs::remove_dir_all(dir).ok();
    }
}
