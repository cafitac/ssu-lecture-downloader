//! "진단 파일 저장": a zip of the app's own log files plus a small `info.json`, written only where
//! the user chose in the save dialog. Nothing is uploaded or sent anywhere.
//!
//! The shell and the backend already write `backend.log`, `watch.log` and the one rotated
//! `*.previous.log` of each into the app log dir. Before a log goes into the zip it is redacted once more (defence in
//! depth: the writers already keep tokens and cookies out) and cut to its last 5 MB.

use serde::Serialize;
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tauri::Manager;

use crate::backend::BackendManager;

/// The log files that go into the zip, by their names in the app log dir.
const LOG_FILES: [&str; 4] = [
    "backend.log",
    "backend.previous.log",
    "watch.log",
    "watch.previous.log",
];
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

/// How far an unquoted value reaches.
#[derive(Clone, Copy, PartialEq)]
enum Reach {
    /// Up to the value's delimiter (`token=abc&x=1`).
    Value,
    /// To the end of the line after `:` (`Cookie: a=b; c=d`), but only up to the delimiter after `=`
    /// (`cookies=2 launch=true` is a count, not a header).
    LineAfterColon,
    /// To the end of the line, whatever the separator and suffix: it holds a scheme and a credential.
    Line,
}

/// Words that mark a value as secret: `(word, whole word only, reach)`.
/// A word listed as not whole-word also matches inside names (`xn_api_token`, `JSESSIONID`, ...)
/// and may be followed by more name characters (`session_id=`).
const SECRET_KEYS: [(&str, bool, Reach); 14] = [
    ("cookie", false, Reach::LineAfterColon),
    ("authorization", false, Reach::Line),
    ("token", false, Reach::Value),
    ("password", false, Reach::Value),
    ("passwd", false, Reach::Value),
    ("secret", false, Reach::Value),
    ("session", false, Reach::Value),
    ("api_key", false, Reach::Value),
    ("api-key", false, Reach::Value),
    ("apikey", false, Reach::Value),
    ("signature", false, Reach::Value),
    ("key-pair-id", false, Reach::Value),
    ("sid", true, Reach::Value),
    ("policy", true, Reach::Value),
];

/// Words that open a credential (`Bearer abc`): when a redacted value is one of them, the next
/// token is the credential and goes too.
const AUTH_SCHEMES: [&str; 5] = ["bearer", "basic", "digest", "negotiate", "token"];

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

/// When the value `start..end` is an auth scheme word, extends `end` over the next
/// whitespace-delimited token (the credential that follows the scheme).
fn include_credential_after_scheme(bytes: &[u8], start: usize, end: usize) -> usize {
    if !AUTH_SCHEMES
        .iter()
        .any(|scheme| scheme.as_bytes() == &bytes[start..end])
    {
        return end;
    }
    let mut next = end;
    while next < bytes.len() && bytes[next].is_ascii_whitespace() {
        next += 1;
    }
    if next == end || next >= bytes.len() {
        return end;
    }
    let mut token_end = next;
    while token_end < bytes.len() && !bytes[token_end].is_ascii_whitespace() {
        token_end += 1;
    }
    token_end
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
        for (key, whole_word, reach) in SECRET_KEYS {
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
                let to_line_end = reach == Reach::Line
                    || (reach == Reach::LineAfterColon && bytes[separator] == b':');
                if let Some((start, mut end)) = read_value(bytes, separator + 1, to_line_end) {
                    if !to_line_end && !is_quote(bytes[start - 1]) {
                        end = include_credential_after_scheme(bytes, start, end);
                    }
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

const GENERIC_SAVE_ERROR: &str =
    "진단 파일을 저장하지 못했어요. 다른 폴더를 골라 다시 시도해 주세요.";
const NO_PERMISSION_ERROR: &str =
    "이 폴더에는 파일을 저장할 권한이 없어요. 다른 폴더를 골라 주세요.";
const DISK_FULL_ERROR: &str = "저장 공간이 부족해요. 디스크 공간을 확보한 뒤 다시 시도해 주세요.";
const NAME_TAKEN_ERROR: &str = "같은 이름의 파일이 이미 있어요. 다른 이름으로 저장해 주세요.";
/// The same three causes for the automatic save (the "문제가 생겼어요" dialog), which has no folder
/// to pick: it points to Settings instead.
const AUTO_NO_PERMISSION_ERROR: &str =
    "다운로드 폴더에 저장할 권한이 없어요. 설정 → 문제 보내기에서 다른 위치를 골라 저장해 주세요.";
const AUTO_DISK_FULL_ERROR: &str = "저장 공간이 부족해요. 공간을 확보한 뒤 다시 시도해 주세요.";
const AUTO_GENERIC_ERROR: &str =
    "진단 파일을 저장하지 못했어요. 설정 → 문제 보내기에서 다시 시도해 주세요.";
const NOT_A_FILE_ERROR: &str = "같은 이름의 폴더가 이미 있어요. 다른 이름이나 폴더를 골라 주세요.";

/// The automatic save's copy for an error text of the manual save: permission and disk-full keep
/// their cause, everything else gets the generic sentence.
fn automatic_error_text(error: &str) -> String {
    match error {
        NO_PERMISSION_ERROR => AUTO_NO_PERMISSION_ERROR,
        DISK_FULL_ERROR => AUTO_DISK_FULL_ERROR,
        _ => AUTO_GENERIC_ERROR,
    }
    .to_string()
}

/// Plain Korean for the user. The OS detail goes to stderr (the app's log channel), not to the UI.
fn io_error_text(error: &std::io::Error) -> String {
    eprintln!("diagnostics: {error}");
    match error.kind() {
        std::io::ErrorKind::PermissionDenied => NO_PERMISSION_ERROR,
        std::io::ErrorKind::StorageFull => DISK_FULL_ERROR,
        _ => GENERIC_SAVE_ERROR,
    }
    .to_string()
}

fn zip_error_text(error: &zip::result::ZipError) -> String {
    match error {
        zip::result::ZipError::Io(io) => io_error_text(io),
        other => {
            eprintln!("diagnostics: {other}");
            GENERIC_SAVE_ERROR.to_string()
        }
    }
}

/// The destination with a `.zip` extension, and whether it was appended (the user did not type it).
fn with_zip_extension(destination: &Path) -> Option<(PathBuf, bool)> {
    let name = destination.file_name()?.to_string_lossy().to_string();
    if name.to_ascii_lowercase().ends_with(".zip") {
        Some((destination.to_path_buf(), false))
    } else {
        Some((destination.with_file_name(format!("{name}.zip")), true))
    }
}

/// Writes the zip to `destination` (`.zip` is appended when missing). Returns the entry names and
/// the path that was written.
///
/// The zip is written to a temporary file in the same folder, flushed to disk and renamed over the
/// destination only once it is complete, so a failure never touches a file the user already has
/// there (the save dialog may have been confirmed with "Replace"). On failure only the temporary
/// file is removed. A destination that exists but is not a regular file (a folder, a device) is
/// refused; a symlink to a regular file is written through, so the link stays a link.
pub(crate) fn write_bundle(
    destination: &Path,
    log_dir: &Path,
    info: &serde_json::Value,
) -> Result<SavedDiagnostics, String> {
    let (destination, appended) =
        with_zip_extension(destination).ok_or_else(|| "저장할 파일 이름이 없어요.".to_string())?;
    // The save dialog confirmed "Replace" for the name the user typed, not for `name.zip`.
    if appended && fs::symlink_metadata(&destination).is_ok() {
        return Err(match fs::metadata(&destination) {
            Ok(metadata) if metadata.is_file() => NAME_TAKEN_ERROR,
            _ => NOT_A_FILE_ERROR,
        }
        .to_string());
    }
    let target = match fs::metadata(&destination) {
        Ok(metadata) if metadata.is_file() => {
            fs::canonicalize(&destination).unwrap_or_else(|_| destination.clone())
        }
        Ok(_) => return Err(NOT_A_FILE_ERROR.to_string()),
        Err(_) => destination.clone(),
    };
    let name = target
        .file_name()
        .ok_or_else(|| "저장할 파일 이름이 없어요.".to_string())?
        .to_string_lossy();
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos())
        .unwrap_or_default();
    let temp = target.with_file_name(format!(".{name}.{}-{nonce}.tmp", std::process::id()));
    let result = write_bundle_to(&temp, log_dir, info).and_then(|files| {
        fs::rename(&temp, &target).map_err(|error| io_error_text(&error))?;
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
    let file = File::create(path).map_err(|error| io_error_text(&error))?;
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated);
    let zip_fail = |error: zip::result::ZipError| zip_error_text(&error);

    let mut files = Vec::new();
    for name in LOG_FILES {
        let Some(text) = prepare_log(&log_dir.join(name)) else {
            continue;
        };
        zip.start_file(name, options).map_err(zip_fail)?;
        zip.write_all(text.as_bytes())
            .map_err(|error| io_error_text(&error))?;
        files.push(name.to_string());
    }

    let mut info = info.clone();
    if let Some(map) = info.as_object_mut() {
        map.insert(
            "included_files".to_string(),
            serde_json::json!(files.clone()),
        );
    }
    let info_text = serde_json::to_string_pretty(&info).map_err(|error| {
        eprintln!("diagnostics: {error}");
        GENERIC_SAVE_ERROR.to_string()
    })?;
    zip.start_file("info.json", options).map_err(zip_fail)?;
    zip.write_all(info_text.as_bytes())
        .map_err(|error| io_error_text(&error))?;
    files.push("info.json".to_string());

    let file = zip.finish().map_err(zip_fail)?;
    file.sync_all().map_err(|error| io_error_text(&error))?;
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

/// The folder the automatic save (the "문제가 생겼어요" dialog) writes into, under Downloads.
const AUTO_SAVE_FOLDER: &str = "SSU 진단";
/// How many `(2)`, `(3)`, ... names are tried before giving up.
const AUTO_SAVE_MAX_COPIES: u32 = 999;

/// `folder/<file_name>`, or `folder/<name> (2).zip`, `(3)`, ... when that name is taken. Creates
/// the folder. `file_name` must be a plain file name (no folder parts).
pub(crate) fn unique_zip_destination(folder: &Path, file_name: &str) -> Result<PathBuf, String> {
    let name = file_name.trim();
    let plain = !name.is_empty()
        && !name.starts_with('.')
        && !name.contains(['/', '\\', '\0'])
        && Path::new(name).file_name().is_some_and(|only| only == name);
    if !plain {
        return Err("저장할 파일 이름이 올바르지 않아요.".to_string());
    }
    let stem = name
        .strip_suffix(".zip")
        .or_else(|| name.strip_suffix(".ZIP"))
        .unwrap_or(name);
    fs::create_dir_all(folder).map_err(|error| io_error_text(&error))?;
    let taken = |path: &Path| fs::symlink_metadata(path).is_ok();
    let first = folder.join(format!("{stem}.zip"));
    if !taken(&first) {
        return Ok(first);
    }
    for copy in 2..=AUTO_SAVE_MAX_COPIES {
        let candidate = folder.join(format!("{stem} ({copy}).zip"));
        if !taken(&candidate) {
            return Ok(candidate);
        }
    }
    Err(NAME_TAKEN_ERROR.to_string())
}

/// Where the automatic save puts the zip: `~/Downloads/SSU 진단/<file_name>` (made unique, the
/// folder created). The frontend then calls `save_diagnostics` with this path.
#[tauri::command]
pub(crate) fn diagnostics_auto_destination(file_name: String) -> Result<String, String> {
    let home = crate::platform::real_user_home_dir()
        .ok_or_else(|| "다운로드 폴더를 찾지 못했어요.".to_string())?;
    let folder = home.join("Downloads").join(AUTO_SAVE_FOLDER);
    unique_zip_destination(&folder, &file_name).map(|path| path.to_string_lossy().to_string())
}

/// `async` so that Tauri runs it off the main thread: reading, redacting and compressing the
/// logs (and `sw_vers`) must not freeze the window.
#[tauri::command(async)]
pub(crate) fn save_diagnostics(
    app: tauri::AppHandle,
    destination: String,
    automatic: Option<bool>,
) -> Result<SavedDiagnostics, String> {
    let result = save_diagnostics_to(&app, destination);
    if automatic == Some(true) {
        result.map_err(|error| automatic_error_text(&error))
    } else {
        result
    }
}

fn save_diagnostics_to(
    app: &tauri::AppHandle,
    destination: String,
) -> Result<SavedDiagnostics, String> {
    if destination.trim().is_empty() {
        return Err("저장할 위치가 비어 있어요.".to_string());
    }
    let destination = PathBuf::from(destination);
    let log_dir = app.path().app_log_dir().map_err(|error| {
        eprintln!("diagnostics: {error}");
        "앱 로그 폴더를 찾지 못했어요.".to_string()
    })?;
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
    fn the_automatic_save_has_its_own_copy_without_a_folder_to_pick() {
        assert_eq!(
            automatic_error_text(NO_PERMISSION_ERROR),
            "다운로드 폴더에 저장할 권한이 없어요. 설정 → 문제 보내기에서 다른 위치를 골라 저장해 주세요."
        );
        assert_eq!(
            automatic_error_text(DISK_FULL_ERROR),
            "저장 공간이 부족해요. 공간을 확보한 뒤 다시 시도해 주세요."
        );
        for other in [
            GENERIC_SAVE_ERROR,
            NAME_TAKEN_ERROR,
            NOT_A_FILE_ERROR,
            "저장할 위치가 비어 있어요.",
        ] {
            assert_eq!(
                automatic_error_text(other),
                "진단 파일을 저장하지 못했어요. 설정 → 문제 보내기에서 다시 시도해 주세요.",
                "{other}"
            );
        }
        for text in [
            AUTO_NO_PERMISSION_ERROR,
            AUTO_DISK_FULL_ERROR,
            AUTO_GENERIC_ERROR,
        ] {
            assert!(!text.contains("폴더를 골라"), "{text}");
        }
        // The manual save keeps its sentences.
        assert!(NO_PERMISSION_ERROR.contains("다른 폴더를 골라 주세요"));
    }

    #[test]
    fn automatic_destination_is_unique_and_creates_the_folder() {
        let root = temp_dir("auto");
        let folder = root.join("Downloads").join(AUTO_SAVE_FOLDER);
        let first = unique_zip_destination(&folder, "ssu-진단-20261030-1105.zip").unwrap();
        assert_eq!(first, folder.join("ssu-진단-20261030-1105.zip"));
        assert!(folder.is_dir(), "the folder was created");
        fs::write(&first, b"zip").unwrap();
        let second = unique_zip_destination(&folder, "ssu-진단-20261030-1105.zip").unwrap();
        assert_eq!(second, folder.join("ssu-진단-20261030-1105 (2).zip"));
        fs::write(&second, b"zip").unwrap();
        let third = unique_zip_destination(&folder, "ssu-진단-20261030-1105.zip").unwrap();
        assert_eq!(third, folder.join("ssu-진단-20261030-1105 (3).zip"));
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn automatic_destination_refuses_names_with_folder_parts() {
        let root = temp_dir("auto-bad");
        for name in ["", "  ", "../x.zip", "a/b.zip", "a\\b.zip", ".hidden.zip"] {
            assert!(unique_zip_destination(&root, name).is_err(), "{name:?}");
        }
        // A name without `.zip` gets it.
        let path = unique_zip_destination(&root, "diag").unwrap();
        assert_eq!(path, root.join("diag.zip"));
        fs::remove_dir_all(root).ok();
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
            ("cookie=abc123; other=1", "cookie=[redacted]; other=1"),
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
    fn only_the_header_form_takes_the_rest_of_the_line() {
        // A diagnostic line from watch.log keeps everything but the value itself.
        assert_eq!(
            redact("plan ok cookies=2 launch=true viewer=true"),
            "plan ok cookies=[redacted] launch=true viewer=true"
        );
        assert_eq!(
            redact("cookie_count=3 launch=true"),
            "cookie_count=[redacted] launch=true"
        );
        assert_eq!(
            redact("cookie=a1 launch=true"),
            "cookie=[redacted] launch=true"
        );
        // The header form redacts the whole header, whatever it contains.
        assert_eq!(redact("Cookie: a=b; c=d"), "Cookie: [redacted]");
        assert_eq!(
            redact("Set-Cookie: a=b; Path=/; HttpOnly"),
            "Set-Cookie: [redacted]"
        );
        assert_eq!(
            redact("Authorization: Basic abc, realm=x"),
            "Authorization: [redacted]"
        );
        assert_eq!(redact("cookie : a=b; c=d"), "cookie : [redacted]");
        assert_eq!(
            redact(r#"{"Cookie": "a=b; c=d", "Host": "x"}"#),
            r#"{"Cookie": "[redacted]", "Host": "x"}"#
        );
    }

    #[test]
    fn authorization_always_takes_the_rest_of_the_line_and_cookie_does_after_a_colon() {
        for (input, expected) in [
            ("authorization=Bearer abc123xyz", "authorization=[redacted]"),
            (
                "authorization=Basic dXNlcjpwYXNz more",
                "authorization=[redacted]",
            ),
            (
                "authorization = Bearer abc.def tail",
                "authorization = [redacted]",
            ),
            (
                "proxy_authorization: Digest a=b, c=d",
                "proxy_authorization: [redacted]",
            ),
            ("authorizations=Negotiate YII", "authorizations=[redacted]"),
            (
                r#"{"authorization_header": "Bearer abc", "n": 1}"#,
                r#"{"authorization_header": "[redacted]", "n": 1}"#,
            ),
            ("cookies: a=b; c=d", "cookies: [redacted]"),
            ("cookie_jar: a=b; c=d", "cookie_jar: [redacted]"),
            ("Set-Cookie: a=b; Path=/", "Set-Cookie: [redacted]"),
            // `=`-separated cookie values and counts stay value-bounded.
            (
                "plan ok cookies=2 launch=true viewer=true",
                "plan ok cookies=[redacted] launch=true viewer=true",
            ),
            (
                "cookie_count=3 launch=true",
                "cookie_count=[redacted] launch=true",
            ),
        ] {
            assert_eq!(redact(input), expected, "{input}");
        }
    }

    #[test]
    fn a_redacted_auth_scheme_word_takes_the_credential_after_it() {
        for (input, expected) in [
            ("token=Bearer abc123 next", "token=[redacted] next"),
            (
                "session: basic dXNlcjpwYXNz next",
                "session: [redacted] next",
            ),
            ("api_key=Digest abc more=1", "api_key=[redacted] more=1"),
            ("secret=Negotiate YII= end", "secret=[redacted] end"),
            ("password=token abc next", "password=[redacted] next"),
            // Not a scheme word: only the value goes.
            ("token=basics next", "token=[redacted] next"),
            // A scheme word with nothing after it.
            ("token=bearer", "token=[redacted]"),
            ("token=bearer ", "token=[redacted] "),
            // A quoted value already holds both words.
            (
                r#"{"token": "Bearer abc", "n": 1}"#,
                r#"{"token": "[redacted]", "n": 1}"#,
            ),
        ] {
            assert_eq!(redact(input), expected, "{input}");
        }
        // Applying it again changes nothing.
        let once = redact("token=Bearer abc123 next\nauthorization=Basic x y\ncookies: a=b; c=d\n");
        assert_eq!(redact(&once), once);
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
        // The rotated watch log is included; backend.previous.log does not exist: skipped, not an error.
        fs::write(logs.join("watch.previous.log"), "older watch line\n").expect("log");
        let destination = out.join("diag.zip");
        let info = serde_json::json!({ "app_version": "9.9.9" });

        let saved = write_bundle(&destination, &logs, &info).expect("bundle");
        assert_eq!(
            saved.files,
            [
                "backend.log",
                "watch.log",
                "watch.previous.log",
                "info.json"
            ]
        );
        assert_eq!(saved.path, destination.to_string_lossy());

        let entries = zip_entries(&destination);
        let names: Vec<&str> = entries.iter().map(|(name, _)| name.as_str()).collect();
        assert_eq!(
            names,
            [
                "backend.log",
                "watch.log",
                "watch.previous.log",
                "info.json"
            ]
        );
        assert_eq!(entries[0].1, "start\nGET /v?token=[redacted] ok\n");
        assert_eq!(entries[1].1, "watch Cookie: [redacted]\n");
        assert_eq!(entries[2].1, "older watch line\n");
        let info: serde_json::Value = serde_json::from_str(&entries[3].1).expect("info json");
        assert_eq!(info["app_version"], "9.9.9");
        assert_eq!(
            info["included_files"],
            serde_json::json!(["backend.log", "watch.log", "watch.previous.log"])
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
    fn a_missing_folder_is_a_plain_korean_error_and_leaves_nothing_behind() {
        let logs = temp_dir("fail-logs");
        let missing_parent = logs.join("no-such-folder").join("diag.zip");
        let error = write_bundle(&missing_parent, &logs, &serde_json::json!({}))
            .expect_err("no folder to write into");
        assert_eq!(error, GENERIC_SAVE_ERROR);
        assert!(!missing_parent.exists());
        fs::remove_dir_all(logs).ok();
    }

    #[test]
    fn io_errors_become_a_cause_in_plain_korean_without_the_os_detail() {
        use std::io::{Error, ErrorKind};
        assert_eq!(
            io_error_text(&Error::from(ErrorKind::PermissionDenied)),
            NO_PERMISSION_ERROR
        );
        assert_eq!(
            io_error_text(&Error::from_raw_os_error(13)),
            NO_PERMISSION_ERROR
        );
        assert_eq!(
            io_error_text(&Error::from(ErrorKind::StorageFull)),
            DISK_FULL_ERROR
        );
        #[cfg(unix)]
        assert_eq!(
            io_error_text(&Error::from_raw_os_error(28)),
            DISK_FULL_ERROR
        );
        assert_eq!(
            io_error_text(&Error::new(ErrorKind::Other, "boom (os error 5)")),
            GENERIC_SAVE_ERROR
        );
        for text in [
            NO_PERMISSION_ERROR,
            DISK_FULL_ERROR,
            GENERIC_SAVE_ERROR,
            NOT_A_FILE_ERROR,
        ] {
            assert!(!text.contains("os error") && !text.is_ascii(), "{text}");
        }
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
        assert_eq!(
            result.expect_err("no write permission"),
            NO_PERMISSION_ERROR
        );
        assert_eq!(fs::read(&destination).expect("kept"), b"the user's own zip");
        assert_eq!(leftovers(&out), ["diag.zip"], "no temporary file is left");
        fs::set_permissions(&destination, fs::Permissions::from_mode(0o644)).ok();
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn a_destination_that_is_a_folder_is_refused_and_nothing_is_created() {
        let logs = temp_dir("dir-logs");
        let out = temp_dir("dir-out");
        let destination = out.join("diag.zip");
        fs::create_dir_all(destination.join("keep")).expect("folder in the way");

        let error = write_bundle(&destination, &logs, &serde_json::json!({}))
            .expect_err("a folder is not replaced");
        assert_eq!(error, NOT_A_FILE_ERROR);
        assert!(
            destination.join("keep").is_dir(),
            "the user's folder is untouched"
        );
        assert_eq!(leftovers(&out), ["diag.zip"], "no temporary file is left");
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[cfg(unix)]
    #[test]
    fn a_symlink_to_a_regular_file_is_written_through_and_stays_a_link() {
        let logs = temp_dir("link-logs");
        let out = temp_dir("link-out");
        let real = out.join("real.zip");
        let link = out.join("link.zip");
        fs::write(&real, b"old").expect("real file");
        std::os::unix::fs::symlink(&real, &link).expect("symlink");

        let saved = write_bundle(&link, &logs, &serde_json::json!({})).expect("bundle");
        assert_eq!(saved.path, link.to_string_lossy());
        assert!(fs::symlink_metadata(&link)
            .expect("link")
            .file_type()
            .is_symlink());
        assert_eq!(
            zip_entries(&real)[0].0,
            "info.json",
            "the target got the zip"
        );
        assert_eq!(leftovers(&out), ["link.zip", "real.zip"]);
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[cfg(unix)]
    #[test]
    fn a_symlink_to_a_folder_is_refused() {
        let logs = temp_dir("linkdir-logs");
        let out = temp_dir("linkdir-out");
        let folder = out.join("folder");
        fs::create_dir_all(&folder).expect("folder");
        let link = out.join("link.zip");
        std::os::unix::fs::symlink(&folder, &link).expect("symlink");
        let error = write_bundle(&link, &logs, &serde_json::json!({})).expect_err("refused");
        assert_eq!(error, NOT_A_FILE_ERROR);
        assert_eq!(leftovers(&out), ["folder", "link.zip"]);
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn an_appended_zip_name_that_is_taken_is_refused_not_replaced() {
        let logs = temp_dir("taken-logs");
        let out = temp_dir("taken-out");
        // The user typed `diag`; `diag.zip` is another file they never agreed to replace.
        fs::write(out.join("diag.zip"), b"someone else's zip").expect("existing file");
        let error =
            write_bundle(&out.join("diag"), &logs, &serde_json::json!({})).expect_err("name taken");
        assert_eq!(error, NAME_TAKEN_ERROR);
        assert_eq!(
            fs::read(out.join("diag.zip")).expect("kept"),
            b"someone else's zip"
        );
        assert_eq!(leftovers(&out), ["diag.zip"]);
        // Typing the full name is the confirmed replace.
        write_bundle(&out.join("diag.zip"), &logs, &serde_json::json!({})).expect("replace");
        assert_eq!(zip_entries(&out.join("diag.zip"))[0].0, "info.json");
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn an_appended_zip_name_that_is_a_folder_gives_the_not_a_file_error() {
        let logs = temp_dir("takendir-logs");
        let out = temp_dir("takendir-out");
        fs::create_dir_all(out.join("diag.zip").join("keep")).expect("folder");
        let error = write_bundle(&out.join("diag"), &logs, &serde_json::json!({}))
            .expect_err("a folder is in the way");
        assert_eq!(error, NOT_A_FILE_ERROR);
        assert!(out.join("diag.zip").join("keep").is_dir());
        assert_eq!(leftovers(&out), ["diag.zip"]);
        fs::remove_dir_all(logs).ok();
        fs::remove_dir_all(out).ok();
    }

    #[test]
    fn the_zip_extension_is_appended_when_missing() {
        let logs = temp_dir("ext-logs");
        let out = temp_dir("ext-out");
        let saved = write_bundle(&out.join("diag"), &logs, &serde_json::json!({})).expect("bundle");
        assert_eq!(saved.path, out.join("diag.zip").to_string_lossy());
        assert_eq!(leftovers(&out), ["diag.zip"]);
        // An existing .zip name, in any case, is kept as it is.
        let kept =
            write_bundle(&out.join("Report.ZIP"), &logs, &serde_json::json!({})).expect("bundle");
        assert_eq!(kept.path, out.join("Report.ZIP").to_string_lossy());
        assert_eq!(leftovers(&out), ["Report.ZIP", "diag.zip"]);
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
