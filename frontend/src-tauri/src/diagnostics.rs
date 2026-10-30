//! "진단 파일 저장": a zip of the app's own log files plus a small `info.json`, written only where
//! the user chose in the save dialog. Nothing is uploaded or sent anywhere.
//!
//! The shell and the backend already write `backend.log`, `backend.previous.log` and `watch.log`
//! into the app log dir. Before a log goes into the zip it is redacted once more (defence in
//! depth: the writers already keep tokens and cookies out) and cut to its last 5 MB.

use serde::Serialize;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tauri::Manager;

use crate::backend::BackendManager;

/// The log files that go into the zip, by their names in the app log dir.
const LOG_FILES: [&str; 3] = ["backend.log", "backend.previous.log", "watch.log"];
/// Each log is cut to its last this many bytes.
const MAX_LOG_BYTES: u64 = 5 * 1024 * 1024;
const REDACTED: &str = "[redacted]";

#[derive(Debug, Serialize)]
pub(crate) struct SavedDiagnostics {
    pub(crate) path: String,
    /// Names of the entries in the zip (the logs that existed, then `info.json`).
    pub(crate) files: Vec<String>,
}
// ---------------------------------------------------------------------------
// Redaction
// ---------------------------------------------------------------------------

/// Words that mark a value as secret: `(word, whole word only, rest of the line when unquoted)`.
/// A word listed as not whole-word also matches inside names (`xn_api_token`, `JSESSIONID`, ...)
/// and may be followed by more name characters (`session_id=`).
const SECRET_KEYS: [(&str, bool, bool); 14] = [
    ("cookie", false, true),
    ("authorization", false, true),
    ("token", false, false),
    ("password", false, false),
    ("passwd", false, false),
    ("secret", false, false),
    ("session", false, false),
    ("api_key", false, false),
    ("api-key", false, false),
    ("apikey", false, false),
    ("signature", false, false),
    ("key-pair-id", false, false),
    ("sid", true, false),
    ("policy", true, false),
];

/// A dict pair `{'name': <this>, 'value': ...}` is secret when its name contains one of these.
const SECRET_NAME_WORDS: [&str; 4] = ["token", "cookie", "session", "secret"];

fn is_quote(byte: u8) -> bool {
    byte == b'"' || byte == b'\''
}

fn is_value_end(byte: u8) -> bool {
    byte.is_ascii_whitespace() || matches!(byte, b'&' | b'"' | b'\'' | b';' | b',' | b')' | b'}')
}

fn is_name_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-'
}

fn skip_spaces(bytes: &[u8], mut at: usize) -> usize {
    while at < bytes.len() && (bytes[at] == b' ' || bytes[at] == b'\t') {
        at += 1;
    }
    at
}

fn skip_quote(bytes: &[u8], at: usize) -> usize {
    if at < bytes.len() && is_quote(bytes[at]) {
        at + 1
    } else {
        at
    }
}

/// The value that follows a separator: the inside of a quoted string, the rest of the line
/// (`rest_of_line`, for headers such as `Cookie: a=b; c=d`) or a run up to a delimiter.
fn read_value(bytes: &[u8], after_separator: usize, rest_of_line: bool) -> Option<(usize, usize)> {
    let start = skip_spaces(bytes, after_separator);
    if start >= bytes.len() {
        return None;
    }
    let range = if is_quote(bytes[start]) {
        let quote = bytes[start];
        let inner = start + 1;
        let end = bytes[inner..]
            .iter()
            .position(|byte| *byte == quote)
            .map_or(bytes.len(), |offset| inner + offset);
        (inner, end)
    } else if rest_of_line {
        (start, bytes.len())
    } else {
        let mut end = start;
        while end < bytes.len() && !is_value_end(bytes[end]) {
            end += 1;
        }
        (start, end)
    };
    (range.1 > range.0).then_some(range)
}

/// Keeps scheme, host and path of every `http(s)://` URL: no query string, fragment or userinfo.
fn strip_url_queries(line: &str) -> String {
    let lower = line.to_ascii_lowercase();
    let bytes = lower.as_bytes();
    let mut out = String::with_capacity(line.len());
    let mut at = 0;
    while at < line.len() {
        let scheme = if bytes[at..].starts_with(b"https://") {
            8
        } else if bytes[at..].starts_with(b"http://") {
            7
        } else {
            0
        };
        if scheme == 0 {
            let ch = line[at..]
                .chars()
                .next()
                .expect("a char at a char boundary");
            out.push(ch);
            at += ch.len_utf8();
            continue;
        }
        let mut end = at + scheme;
        while end < bytes.len()
            && !(bytes[end].is_ascii_whitespace()
                || matches!(bytes[end], b'"' | b'\'' | b'<' | b'>' | b')' | b']' | b'}'))
        {
            end += 1;
        }
        let url = &line[at + scheme..end];
        let url = url.split(['?', '#']).next().unwrap_or("");
        let (authority, path) = url.split_at(url.find('/').unwrap_or(url.len()));
        let host = authority.rsplit('@').next().unwrap_or(authority);
        out.push_str(&line[at..at + scheme]);
        out.push_str(host);
        out.push_str(path);
        at = end;
    }
    out
}

/// Index of `<quote>key<quote> :` at or after `from`: where the pair's value starts.
fn find_quoted_key(bytes: &[u8], from: usize, key: &str) -> Option<(usize, usize)> {
    let key = key.as_bytes();
    let mut at = from;
    while at + key.len() + 2 <= bytes.len() {
        if is_quote(bytes[at])
            && bytes[at + 1..].starts_with(key)
            && bytes[at + 1 + key.len()] == bytes[at]
        {
            let colon = skip_spaces(bytes, at + 2 + key.len());
            if colon < bytes.len() && bytes[colon] == b':' {
                return Some((at, colon + 1));
            }
        }
        at += 1;
    }
    None
}

/// `{'name': 'xn_api_token', 'value': 'abc'}`: the value of a pair whose name is secret.
fn redact_name_value_pairs(line: &str) -> String {
    let lower = line.to_ascii_lowercase();
    let bytes = lower.as_bytes();
    let mut ranges: Vec<(usize, usize)> = Vec::new();
    let mut from = 0;
    while let Some((name_at, name_after)) = find_quoted_key(bytes, from, "name") {
        from = name_after;
        let Some((name_start, name_end)) =
            read_value(bytes, name_after, false).filter(|(start, _)| is_quote(bytes[*start - 1]))
        else {
            continue;
        };
        let name = &lower[name_start..name_end];
        if !SECRET_NAME_WORDS.iter().any(|word| name.contains(word)) {
            continue;
        }
        let limit = find_quoted_key(bytes, name_end, "name").map_or(bytes.len(), |(next, _)| next);
        if let Some((_, value_after)) = find_quoted_key(&bytes[..limit], name_end, "value") {
            if let Some(range) = read_value(&bytes[..limit], value_after, false) {
                ranges.push(range);
            }
        }
        from = name_end.max(name_at + 1);
    }
    apply_ranges(line, &ranges)
}

fn apply_ranges(line: &str, ranges: &[(usize, usize)]) -> String {
    let mut out = String::with_capacity(line.len());
    let mut copied = 0;
    for (start, end) in ranges {
        if *start < copied || &line[*start..*end] == REDACTED {
            continue;
        }
        out.push_str(&line[copied..*start]);
        out.push_str(REDACTED);
        copied = *end;
    }
    out.push_str(&line[copied..]);
    out
}

/// Replaces secret values with `[redacted]` in one line: `key=value`, `key: value` and quoted
/// dict/JSON keys for the words in `SECRET_KEYS`, `Bearer ...`, and name/value dict pairs.
/// Query strings of URLs are dropped.
fn redact_line(line: &str) -> String {
    let line = redact_name_value_pairs(&strip_url_queries(line));
    let lower = line.to_ascii_lowercase();
    let bytes = lower.as_bytes();
    let mut out = String::with_capacity(line.len());
    let mut at = 0;
    'scan: while at < line.len() {
        let rest = &bytes[at..];
        if rest.starts_with(b"bearer ") {
            if let Some((start, end)) = read_value(bytes, at + 7, false) {
                if &line[start..end] != REDACTED {
                    out.push_str(&line[at..start]);
                    out.push_str(REDACTED);
                    at = end;
                    continue 'scan;
                }
            }
        }
        for (key, whole_word, rest_of_line) in SECRET_KEYS {
            if !rest.starts_with(key.as_bytes()) {
                continue;
            }
            if whole_word && at > 0 && is_name_byte(bytes[at - 1]) {
                continue;
            }
            let mut after_key = at + key.len();
            if !whole_word {
                while after_key < bytes.len() && is_name_byte(bytes[after_key]) {
                    after_key += 1;
                }
            }
            let separator = skip_spaces(bytes, skip_quote(bytes, after_key));
            if separator < bytes.len() && (bytes[separator] == b'=' || bytes[separator] == b':') {
                if let Some((start, end)) = read_value(bytes, separator + 1, rest_of_line) {
                    if line[start..end].trim() != REDACTED {
                        out.push_str(&line[at..start]);
                        out.push_str(REDACTED);
                        at = end;
                        continue 'scan;
                    }
                }
            }
        }
        let ch = line[at..]
            .chars()
            .next()
            .expect("a char at a char boundary");
        out.push(ch);
        at += ch.len_utf8();
    }
    out
}

pub(crate) fn redact(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for line in text.split_inclusive('\n') {
        let (body, newline) = match line.strip_suffix('\n') {
            Some(body) => (body, "\n"),
            None => (line, ""),
        };
        out.push_str(&redact_line(body));
        out.push_str(newline);
    }
    out
}

// ---------------------------------------------------------------------------
// Bundle
// ---------------------------------------------------------------------------

/// The last `MAX_LOG_BYTES` of a file, starting at a line start when the file was cut. `None`
/// when the file does not exist or cannot be read: a missing log is simply left out.
fn read_log_tail(path: &Path) -> Option<String> {
    let mut file = File::open(path).ok()?;
    let length = file.metadata().ok()?.len();
    let cut = length > MAX_LOG_BYTES;
    if cut {
        file.seek(SeekFrom::Start(length - MAX_LOG_BYTES)).ok()?;
    }
    let mut bytes = Vec::new();
    file.take(MAX_LOG_BYTES).read_to_end(&mut bytes).ok()?;
    let mut text = String::from_utf8_lossy(&bytes).into_owned();
    if cut {
        // Drop the partial first line (and any broken character at the cut).
        text = match text.find('\n') {
            Some(newline) => text[newline + 1..].to_string(),
            None => String::new(),
        };
    }
    Some(text)
}

/// Redacted, and again capped at `MAX_LOG_BYTES` (redaction can lengthen a line a little).
fn prepare_log(path: &Path) -> Option<String> {
    let mut text = redact(&read_log_tail(path)?);
    let limit = MAX_LOG_BYTES as usize;
    if text.len() > limit {
        let mut start = text.len() - limit;
        while !text.is_char_boundary(start) {
            start += 1;
        }
        text = text[start..].to_string();
    }
    Some(text)
}

/// Writes the zip to `destination`. Returns the entry names.
///
/// The zip is written to a temporary file in the same folder and renamed over `destination` only
/// once it is complete, so a failure never touches a file the user already has there (the save
/// dialog may have been confirmed with "Replace"). On failure only the temporary file is removed.
pub(crate) fn write_bundle(
    destination: &Path,
    log_dir: &Path,
    info: &serde_json::Value,
) -> Result<SavedDiagnostics, String> {
    let name = destination
        .file_name()
        .ok_or_else(|| "저장할 파일 이름이 없어요.".to_string())?
        .to_string_lossy();
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos())
        .unwrap_or_default();
    let temp = destination.with_file_name(format!(".{name}.{}-{nonce}.tmp", std::process::id()));
    let result = write_bundle_to(&temp, log_dir, info).and_then(|files| {
        fs::rename(&temp, destination)
            .map_err(|error| format!("진단 파일을 저장하지 못했어요. ({error})"))?;
        Ok(SavedDiagnostics {
            path: destination.to_string_lossy().to_string(),
            files,
        })
    });
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

fn write_bundle_to(
    path: &Path,
    log_dir: &Path,
    info: &serde_json::Value,
) -> Result<Vec<String>, String> {
    let file = File::create(path)
        .map_err(|error| format!("저장할 위치에 파일을 만들지 못했어요. ({error})"))?;
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    let fail = |error: &dyn std::fmt::Display| format!("진단 파일을 쓰지 못했어요. ({error})");

    let mut files = Vec::new();
    for name in LOG_FILES {
        let Some(text) = prepare_log(&log_dir.join(name)) else {
            continue;
        };
        zip.start_file(name, options).map_err(|e| fail(&e))?;
        zip.write_all(text.as_bytes()).map_err(|e| fail(&e))?;
        files.push(name.to_string());
    }

    let mut info = info.clone();
    if let Some(map) = info.as_object_mut() {
        map.insert(
            "included_files".to_string(),
            serde_json::json!(files.clone()),
        );
    }
    let info_text = serde_json::to_string_pretty(&info).map_err(|e| fail(&e))?;
    zip.start_file("info.json", options).map_err(|e| fail(&e))?;
    zip.write_all(info_text.as_bytes()).map_err(|e| fail(&e))?;
    files.push("info.json".to_string());

    zip.finish().map_err(|e| fail(&e))?;
    Ok(files)
}

// ---------------------------------------------------------------------------
// info.json
// ---------------------------------------------------------------------------

/// Civil UTC date and time of a unix timestamp, as `YYYY-MM-DDTHH:MM:SSZ`.
fn iso_utc(unix_seconds: i64) -> String {
    let days = unix_seconds.div_euclid(86_400);
    let seconds = unix_seconds.rem_euclid(86_400);
    // Howard Hinnant's days-to-civil algorithm.
    let shifted = days + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_index + 2) / 5 + 1;
    let month = if month_index < 10 {
        month_index + 3
    } else {
        month_index - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        seconds / 3_600,
        seconds % 3_600 / 60,
        seconds % 60
    )
}

#[cfg(target_os = "macos")]
fn macos_version() -> Option<String> {
    let output = std::process::Command::new("/usr/bin/sw_vers")
        .arg("-productVersion")
        .output()
        .ok()?;
    let version = String::from_utf8(output.stdout).ok()?.trim().to_string();
    (!version.is_empty()).then_some(version)
}

#[cfg(not(target_os = "macos"))]
fn macos_version() -> Option<String> {
    None
}

fn build_info(
    app_version: &str,
    unix_seconds: i64,
    backend: Option<&crate::backend::BackendStatus>,
) -> serde_json::Value {
    serde_json::json!({
        "app_version": app_version,
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "macos_version": macos_version(),
        "created_at": iso_utc(unix_seconds),
        "backend": backend.map(|status| serde_json::json!({
            "state": status.state,
            "mode": status.mode,
            "error": status.error.as_deref().map(redact),
        })),
    })
}

// ---------------------------------------------------------------------------
// Command
// ---------------------------------------------------------------------------

/// `async` so that Tauri runs it off the main thread: reading, redacting and compressing the
/// logs (and `sw_vers`) must not freeze the window.
#[tauri::command(async)]
pub(crate) fn save_diagnostics(
    app: tauri::AppHandle,
    destination: String,
) -> Result<SavedDiagnostics, String> {
    if destination.trim().is_empty() {
        return Err("저장할 위치가 비어 있어요.".to_string());
    }
    let destination = PathBuf::from(destination);
    let log_dir = app
        .path()
        .app_log_dir()
        .map_err(|error| format!("앱 로그 폴더를 찾지 못했어요. ({error})"))?;
    let backend = app
        .try_state::<Arc<BackendManager>>()
        .map(|manager| manager.status());
    let info = build_info(
        &app.package_info().version.to_string(),
        crate::reminders::unix_now(),
        backend.as_ref(),
    );
    write_bundle(&destination, &log_dir, &info)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "ssu-diagnostics-{tag}-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        fs::create_dir_all(&dir).expect("temp dir");
        dir
    }

    fn zip_entries(path: &Path) -> Vec<(String, String)> {
        let mut archive = zip::ZipArchive::new(File::open(path).expect("zip")).expect("archive");
        (0..archive.len())
            .map(|index| {
                let mut entry = archive.by_index(index).expect("entry");
                let mut text = String::new();
                entry.read_to_string(&mut text).expect("utf8 entry");
                (entry.name().expect("entry name").to_string(), text)
            })
            .collect()
    }

    #[test]
    fn redacts_tokens_bearer_values_and_cookies() {
        assert_eq!(
            redact("GET /x?token=abc123&page=2 200"),
            "GET /x?token=[redacted]&page=2 200"
        );
        assert_eq!(redact("TOKEN=Secret99"), "TOKEN=[redacted]");
        assert_eq!(
            redact("got Bearer abc.def-ghi done"),
            "got Bearer [redacted] done"
        );
        assert_eq!(
            redact("Cookie: xn_api_token=abc; session=def"),
            "Cookie: [redacted]"
        );
        assert_eq!(redact("Set-Cookie: a=b; Path=/"), "Set-Cookie: [redacted]");
        assert_eq!(
            redact(r#"{"token": "abc", "n": 1}"#),
            r#"{"token": "[redacted]", "n": 1}"#
        );
        assert_eq!(redact("password=hunter2"), "password=[redacted]");
    }

    #[test]
    fn redacts_quoted_dict_keys_and_every_authorization_scheme() {
        assert_eq!(
            redact("headers={'Cookie': 'a=1; b=2', 'Accept': '*/*'}"),
            "headers={'Cookie': '[redacted]', 'Accept': '*/*'}"
        );
        assert_eq!(
            redact(r#"{"Authorization": "Basic dXNlcjpwYXNz", "Host": "x"}"#),
            r#"{"Authorization": "[redacted]", "Host": "x"}"#
        );
        assert_eq!(
            redact("Authorization: Basic dXNlcjpwYXNz"),
            "Authorization: [redacted]"
        );
        assert_eq!(
            redact("authorization: Digest username=x, realm=y"),
            "authorization: [redacted]"
        );
        assert_eq!(
            redact("Authorization: Bearer abc"),
            "Authorization: [redacted]"
        );
    }

    #[test]
    fn redacts_session_secret_key_and_signature_parameters() {
        for (input, expected) in [
            ("cookie=abc123; other=1", "cookie=[redacted]"),
            ("session=abc&x=1", "session=[redacted]&x=1"),
            ("JSESSIONID=ABCDEF; Path=/", "JSESSIONID=[redacted]; Path=/"),
            ("sid=abc x", "sid=[redacted] x"),
            ("client_secret=abc", "client_secret=[redacted]"),
            ("api_key=abc", "api_key=[redacted]"),
            ("apikey=abc", "apikey=[redacted]"),
            ("X-Amz-Signature=abc123", "X-Amz-Signature=[redacted]"),
            ("signature=abc", "signature=[redacted]"),
            (
                "Policy=eyJ&Key-Pair-Id=K123",
                "Policy=[redacted]&Key-Pair-Id=[redacted]",
            ),
            ("{'session_id': 'abc'}", "{'session_id': '[redacted]'}"),
        ] {
            assert_eq!(redact(input), expected, "{input}");
        }
    }

    #[test]
    fn redacts_values_of_secret_name_value_pairs() {
        assert_eq!(
            redact("[{'name': 'xn_api_token', 'value': 'abc123', 'domain': 'ssu.ac.kr'}]"),
            "[{'name': 'xn_api_token', 'value': '[redacted]', 'domain': 'ssu.ac.kr'}]"
        );
        assert_eq!(
            redact(
                r#"[{"name":"SESSION","value":"s1"},{"name":"theme","value":"dark"},{"name":"my_Secret","value":"s2"}]"#
            ),
            r#"[{"name":"SESSION","value":"[redacted]"},{"name":"theme","value":"dark"},{"name":"my_Secret","value":"[redacted]"}]"#
        );
        assert_eq!(
            redact("{'name': 'locale', 'value': 'ko'}"),
            "{'name': 'locale', 'value': 'ko'}"
        );
    }

    #[test]
    fn urls_keep_scheme_host_and_path_only() {
        assert_eq!(
            redact("GET https://canvas.ssu.ac.kr/courses/1/items/2?a=1&b=2#frag 200"),
            "GET https://canvas.ssu.ac.kr/courses/1/items/2 200"
        );
        assert_eq!(
            redact(r#"open "http://127.0.0.1:8795/x?y=1" ok"#),
            r#"open "http://127.0.0.1:8795/x" ok"#
        );
        assert_eq!(
            redact("https://user:pw@host.example/p?q=1"),
            "https://host.example/p"
        );
        assert_eq!(
            redact("https://commons.ssu.ac.kr/em/abc and (https://ssu.ac.kr/?x=1)"),
            "https://commons.ssu.ac.kr/em/abc and (https://ssu.ac.kr/)"
        );
    }

    #[test]
    fn redaction_keeps_ordinary_text_and_is_idempotent() {
        for text in [
            "강의 시청 시작 index=3 module_item_id=4410\nstall seconds=30\n마지막 줄",
            "순서대로 시청을 시작했어요. 세션이 끝났어요: 다시 로그인해 주세요.",
            "다운로드 완료: 강의 12개, 쿠키 설정은 건드리지 않았어요",
            "run done done=3 skipped=1 phase=finished",
            "inside the sidebar: open, besides the policies we consider that",
            "no token here and no session either",
        ] {
            assert_eq!(redact(text), text);
        }
        let once = redact(
            "a token=zzz\nBearer qqq\nCookie: x=y\n{'Cookie': 'k=v'}\nhttps://h/p?q=1\n{'name': 'session', 'value': 'v'}\n",
        );
        assert_eq!(redact(&once), once);
        assert!(once.ends_with('\n'));
        for secret in ["zzz", "qqq", "x=y", "k=v", "q=1", "'v'"] {
            assert!(!once.contains(secret), "{secret} in {once}");
        }
    }

    #[test]
    fn zip_holds_the_present_logs_redacted_and_info_and_skips_missing_ones() {
        let logs = temp_dir("logs");
        let out = temp_dir("out");
        fs::write(logs.join("backend.log"), "start\nGET /v?token=SECRET1 ok\n").expect("log");
        fs::write(logs.join("watch.log"), "watch Cookie: sid=SECRET2\n").expect("log");
        // backend.previous.log does not exist: skipped, not an error.
        let destination = out.join("diag.zip");
        let info = serde_json::json!({ "app_version": "9.9.9" });

        let saved = write_bundle(&destination, &logs, &info).expect("bundle");
        assert_eq!(saved.files, ["backend.log", "watch.log", "info.json"]);
        assert_eq!(saved.path, destination.to_string_lossy());

        let entries = zip_entries(&destination);
        let names: Vec<&str> = entries.iter().map(|(name, _)| name.as_str()).collect();
        assert_eq!(names, ["backend.log", "watch.log", "info.json"]);
        assert_eq!(entries[0].1, "start\nGET /v?token=[redacted] ok\n");
        assert_eq!(entries[1].1, "watch Cookie: [redacted]\n");
        let info: serde_json::Value = serde_json::from_str(&entries[2].1).expect("info json");
        assert_eq!(info["app_version"], "9.9.9");
        assert_eq!(
            info["included_files"],
            serde_json::json!(["backend.log", "watch.log"])
        );
        assert!(!entries.iter().any(|(_, text)| text.contains("SECRET")));

        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn no_logs_at_all_still_gives_a_zip_with_info() {
        let logs = temp_dir("empty-logs");
        let out = temp_dir("empty-out");
        let destination = out.join("diag.zip");
        let saved = write_bundle(&destination, &logs, &serde_json::json!({})).expect("bundle");
        assert_eq!(saved.files, ["info.json"]);
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn a_log_over_the_cap_keeps_its_last_5_mb_from_a_line_start() {
        let logs = temp_dir("big-logs");
        let out = temp_dir("big-out");
        let line = "0123456789 progress item=1 seconds=12\n";
        let repeats = (MAX_LOG_BYTES as usize / line.len()) + 50;
        let mut text = "FIRST-LINE-DROPPED\n".repeat(10);
        text.push_str(&line.repeat(repeats));
        text.push_str("LAST-LINE\n");
        fs::write(logs.join("backend.log"), &text).expect("log");
        let destination = out.join("diag.zip");

        write_bundle(&destination, &logs, &serde_json::json!({})).expect("bundle");
        let entries = zip_entries(&destination);
        let log = &entries[0].1;
        assert_eq!(entries[0].0, "backend.log");
        assert!(log.len() <= MAX_LOG_BYTES as usize, "{}", log.len());
        assert!(log.len() > MAX_LOG_BYTES as usize - 200, "{}", log.len());
        assert!(!log.contains("FIRST-LINE-DROPPED"));
        assert!(log.starts_with("0123456789 progress"), "starts at a line");
        assert!(log.ends_with("LAST-LINE\n"));

        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    fn leftovers(dir: &Path) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(dir)
            .expect("read dir")
            .map(|entry| {
                entry
                    .expect("entry")
                    .file_name()
                    .to_string_lossy()
                    .to_string()
            })
            .collect();
        names.sort();
        names
    }

    #[test]
    fn an_unwritable_destination_is_an_error_and_leaves_nothing_behind() {
        let logs = temp_dir("fail-logs");
        let missing_parent = logs.join("no-such-folder").join("diag.zip");
        let error = write_bundle(&missing_parent, &logs, &serde_json::json!({}))
            .expect_err("no folder to write into");
        assert!(error.contains("만들지 못했어요"), "{error}");
        assert!(!missing_parent.exists());
        fs::remove_dir_all(logs).ok();
    }

    #[cfg(unix)]
    #[test]
    fn a_failed_save_keeps_the_file_the_user_already_has() {
        use std::os::unix::fs::PermissionsExt;
        let logs = temp_dir("keep-logs");
        let out = temp_dir("keep-out");
        let destination = out.join("diag.zip");
        fs::write(&destination, b"the user's own zip").expect("existing file");
        fs::set_permissions(&destination, fs::Permissions::from_mode(0o444)).expect("read-only");
        // The folder refuses new files, so the temporary file cannot even be created.
        fs::set_permissions(&out, fs::Permissions::from_mode(0o555)).expect("read-only folder");
        let can_still_create = File::create(out.join("probe")).is_ok();

        let result = write_bundle(&destination, &logs, &serde_json::json!({}));

        fs::set_permissions(&out, fs::Permissions::from_mode(0o755)).expect("restore folder");
        if can_still_create {
            // Running with rights that ignore permissions (root): nothing to prove here.
            fs::remove_dir_all(logs).ok();
            fs::remove_dir_all(out).ok();
            return;
        }
        assert!(result.is_err(), "{result:?}");
        assert_eq!(fs::read(&destination).expect("kept"), b"the user's own zip");
        assert_eq!(leftovers(&out), ["diag.zip"], "no temporary file is left");
        fs::set_permissions(&destination, fs::Permissions::from_mode(0o644)).ok();
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn a_save_that_fails_at_the_end_removes_only_its_temporary_file() {
        let logs = temp_dir("rename-logs");
        let out = temp_dir("rename-out");
        // A folder cannot be replaced by a file: the zip is written, the final rename fails.
        let destination = out.join("diag.zip");
        fs::create_dir_all(destination.join("keep")).expect("folder in the way");

        let error = write_bundle(&destination, &logs, &serde_json::json!({}))
            .expect_err("rename over a folder fails");
        assert!(error.contains("저장하지 못했어요"), "{error}");
        assert!(
            destination.join("keep").is_dir(),
            "the user's folder is untouched"
        );
        assert_eq!(leftovers(&out), ["diag.zip"], "the temporary file is gone");
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn a_successful_save_replaces_the_old_file_and_leaves_no_temporary_file() {
        let logs = temp_dir("replace-logs");
        let out = temp_dir("replace-out");
        let destination = out.join("diag.zip");
        fs::write(&destination, b"old").expect("old file");
        write_bundle(&destination, &logs, &serde_json::json!({})).expect("bundle");
        assert_eq!(leftovers(&out), ["diag.zip"]);
        assert_eq!(zip_entries(&destination)[0].0, "info.json");
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn info_has_version_os_arch_time_and_the_backend_summary() {
        let status = crate::backend::BackendStatus {
            state: crate::backend::BackendState::Failed,
            mode: "managed".to_string(),
            error: Some("boom token=abc".to_string()),
            log_path: Some("/x/backend.log".to_string()),
        };
        let info = build_info("0.2.0", 1_761_868_800, Some(&status));
        assert_eq!(info["app_version"], "0.2.0");
        assert_eq!(info["os"], std::env::consts::OS);
        assert_eq!(info["arch"], std::env::consts::ARCH);
        assert_eq!(info["created_at"], "2025-10-31T00:00:00Z");
        assert_eq!(info["backend"]["state"], "failed");
        assert_eq!(info["backend"]["mode"], "managed");
        assert_eq!(info["backend"]["error"], "boom token=[redacted]");
        assert!(build_info("0.2.0", 0, None)["backend"].is_null());
    }

    #[test]
    fn iso_utc_handles_the_epoch_and_leap_days() {
        assert_eq!(iso_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(iso_utc(951_782_400), "2000-02-29T00:00:00Z");
        assert_eq!(iso_utc(1_761_912_345), "2025-10-31T12:05:45Z");
    }
}
