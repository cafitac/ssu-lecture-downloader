//! Rust-native download engine: job registry, single/batch workers and the
//! resumable HTTP transfer into `.part` files.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

const MAX_NATIVE_JOB_PROGRESS_EVENTS: usize = 400;
const COALESCED_NATIVE_PROGRESS_STAGES: &[&str] = &["download_progress"];
/// How many finished jobs stay queryable before the oldest are evicted.
const MAX_FINISHED_NATIVE_JOBS: usize = 20;
/// Fail fast when the TCP connection cannot be established.
const NATIVE_CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
/// Maximum idle time (no response headers / no body bytes) before the transfer
/// is treated as stalled. This bounds how long a cancel request can be blocked
/// by a dead connection, while a `.part` file is kept so the next attempt resumes.
const NATIVE_READ_IDLE_TIMEOUT: Duration = Duration::from_secs(20);
const DOWNLOAD_CANCELLED: &str = "DownloadCancelled";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct NativeProgressEvent {
    stage: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    index: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    total: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    module_item_id: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    output_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes_written: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    total_bytes: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    resume_from: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    download_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    max_parallel_downloads: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    file_size: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    http_status: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    content_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    content_length: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    response_host: Option<String>,
}

impl NativeProgressEvent {
    pub(crate) fn new(stage: &str) -> Self {
        Self {
            stage: stage.to_string(),
            index: None,
            total: None,
            module_item_id: None,
            title: None,
            output_path: None,
            bytes_written: None,
            total_bytes: None,
            resume_from: None,
            error_code: None,
            download_count: None,
            max_parallel_downloads: None,
            file_size: None,
            http_status: None,
            content_type: None,
            content_length: None,
            response_host: None,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct NativeDownloadJobStatus {
    job_id: String,
    status: String,
    cancel_requested: bool,
    progress: Vec<NativeProgressEvent>,
    result: Option<serde_json::Value>,
    error_code: Option<String>,
}

#[derive(Debug)]
pub(crate) struct NativeDownloadJob {
    job_id: String,
    status: String,
    cancel_requested: bool,
    progress: Vec<NativeProgressEvent>,
    result: Option<serde_json::Value>,
    error_code: Option<String>,
}

fn is_terminal_status(status: &str) -> bool {
    matches!(status, "succeeded" | "failed" | "cancelled")
}

impl NativeDownloadJob {
    pub(crate) fn new(job_id: String) -> Self {
        Self {
            job_id,
            status: "queued".to_string(),
            cancel_requested: false,
            progress: Vec::new(),
            result: None,
            error_code: None,
        }
    }

    pub(crate) fn snapshot(&self) -> NativeDownloadJobStatus {
        NativeDownloadJobStatus {
            job_id: self.job_id.clone(),
            status: self.status.clone(),
            cancel_requested: self.cancel_requested,
            progress: self.progress.clone(),
            result: self.result.clone(),
            error_code: self.error_code.clone(),
        }
    }

    fn push_progress(&mut self, event: NativeProgressEvent) {
        if COALESCED_NATIVE_PROGRESS_STAGES.contains(&event.stage.as_str()) {
            if let Some(index) = self.progress.iter().rposition(|existing| {
                native_progress_identity(existing) == native_progress_identity(&event)
            }) {
                self.progress[index] = event;
            } else {
                self.progress.push(event);
            }
        } else {
            self.progress.push(event);
        }
        if self.progress.len() > MAX_NATIVE_JOB_PROGRESS_EVENTS {
            let overflow = self.progress.len() - MAX_NATIVE_JOB_PROGRESS_EVENTS;
            self.progress.drain(0..overflow);
        }
    }

    /// Queued -> running, unless the user already cancelled while it was queued.
    /// Returns whether the worker may start transferring.
    fn begin(&mut self) -> bool {
        if self.cancel_requested || self.status == "cancelled" {
            self.mark_cancelled();
            return false;
        }
        if self.status == "queued" {
            self.status = "running".to_string();
        }
        true
    }

    fn mark_cancelled(&mut self) {
        self.status = "cancelled".to_string();
        self.error_code = Some(DOWNLOAD_CANCELLED.to_string());
    }

    /// Request cancellation. A queued job is cancelled immediately; a running
    /// one is cancelled cooperatively by its worker.
    fn request_cancel(&mut self) {
        self.cancel_requested = true;
        if self.status == "queued" {
            self.status = "cancelled".to_string();
        }
    }
}

type SharedNativeDownloadJob = Arc<Mutex<NativeDownloadJob>>;

fn native_progress_identity(
    event: &NativeProgressEvent,
) -> (
    &str,
    Option<usize>,
    Option<&serde_json::Value>,
    Option<&str>,
) {
    (
        event.stage.as_str(),
        event.index,
        event.module_item_id.as_ref(),
        event.output_path.as_deref().or(event.title.as_deref()),
    )
}

pub(crate) struct NativeDownloadJobs {
    jobs: Mutex<HashMap<String, SharedNativeDownloadJob>>,
    next_id: AtomicU64,
}

impl NativeDownloadJobs {
    pub(crate) fn new() -> Self {
        Self {
            jobs: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
        }
    }

    fn create(&self) -> Result<SharedNativeDownloadJob, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let job = Arc::new(Mutex::new(NativeDownloadJob::new(format!("native-{id}"))));
        let job_id = job
            .lock()
            .map_err(|_| "native job lock poisoned".to_string())?
            .job_id
            .clone();
        let mut jobs = self
            .jobs
            .lock()
            .map_err(|_| "native job registry lock poisoned".to_string())?;
        jobs.insert(job_id, Arc::clone(&job));
        evict_finished_jobs(&mut jobs, MAX_FINISHED_NATIVE_JOBS);
        Ok(job)
    }

    fn get(&self, job_id: &str) -> Result<Option<SharedNativeDownloadJob>, String> {
        Ok(self
            .jobs
            .lock()
            .map_err(|_| "native job registry lock poisoned".to_string())?
            .get(job_id)
            .cloned())
    }
}

fn native_job_sequence(job_id: &str) -> u64 {
    job_id
        .strip_prefix("native-")
        .and_then(|value| value.parse().ok())
        .unwrap_or(0)
}

/// Keep only the newest `keep` finished jobs; active jobs are never evicted.
fn evict_finished_jobs(jobs: &mut HashMap<String, SharedNativeDownloadJob>, keep: usize) {
    let mut finished: Vec<(u64, String)> = jobs
        .iter()
        .filter_map(|(job_id, job)| {
            // A locked job is being updated right now, so it is not finished.
            let guard = job.try_lock().ok()?;
            is_terminal_status(&guard.status).then(|| (native_job_sequence(job_id), job_id.clone()))
        })
        .collect();
    if finished.len() <= keep {
        return;
    }
    finished.sort();
    let overflow = finished.len() - keep;
    for (_, job_id) in finished.into_iter().take(overflow) {
        jobs.remove(&job_id);
    }
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct NativeTransferPlan {
    download_url: String,
    referer: Option<String>,
    output_path: String,
    total_bytes: Option<u64>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct NativeDownloadStartRequest {
    result: serde_json::Value,
    native_download: Option<NativeTransferPlan>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct NativeBatchDownloadStartRequest {
    course_id: serde_json::Value,
    course_name: String,
    items: Vec<NativeDownloadStartRequest>,
}

fn native_part_path(path: &Path) -> PathBuf {
    PathBuf::from(format!("{}.part", path.to_string_lossy()))
}

fn update_native_job(job: &SharedNativeDownloadJob, f: impl FnOnce(&mut NativeDownloadJob)) {
    if let Ok(mut guard) = job.lock() {
        f(&mut guard);
    }
}

fn native_job_cancel_requested(job: &SharedNativeDownloadJob) -> bool {
    job.lock()
        .map(|guard| guard.cancel_requested)
        .unwrap_or(true)
}

/// Returns false when the job was cancelled before the worker got to run.
fn begin_native_job(job: &SharedNativeDownloadJob) -> bool {
    job.lock().map(|mut guard| guard.begin()).unwrap_or(false)
}

fn set_json_bool(value: &mut serde_json::Value, key: &str, next: bool) {
    if let serde_json::Value::Object(map) = value {
        map.insert(key.to_string(), serde_json::Value::Bool(next));
    }
}

fn set_json_u64(value: &mut serde_json::Value, key: &str, next: u64) {
    if let serde_json::Value::Object(map) = value {
        map.insert(key.to_string(), serde_json::Value::Number(next.into()));
    }
}

fn response_content_type(response: &reqwest::Response) -> Option<String> {
    response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(|value| value.to_string())
}

fn response_host(response: &reqwest::Response) -> Option<String> {
    response.url().host_str().map(|value| value.to_string())
}

fn attach_response_diagnostics(
    event: &mut NativeProgressEvent,
    status: reqwest::StatusCode,
    content_type: Option<String>,
    content_length: Option<u64>,
    response_host: Option<String>,
) {
    event.http_status = Some(status.as_u16());
    event.content_type = content_type;
    event.content_length = content_length;
    event.response_host = response_host;
}

/// Stable "HTTP 403"-style code. The frontend matches these exact strings
/// (see `AUTHENTICATION_ERROR_CODES` in `src/lib/api.ts`), so never embed the
/// reason phrase.
fn http_status_error(status: reqwest::StatusCode) -> String {
    format!("HTTP {}", status.as_u16())
}

/// Parse `Content-Range: bytes <start>-<end>/<total>` or `bytes */<total>`.
#[derive(Debug, PartialEq, Eq)]
struct ContentRange {
    start: Option<u64>,
    total: Option<u64>,
}

fn parse_content_range(value: &str) -> Option<ContentRange> {
    let rest = value.trim().strip_prefix("bytes")?.trim_start();
    let (range, total) = rest.split_once('/')?;
    let total = total.trim().parse::<u64>().ok();
    let range = range.trim();
    let start = if range == "*" {
        None
    } else {
        Some(range.split_once('-')?.0.trim().parse::<u64>().ok()?)
    };
    Some(ContentRange { start, total })
}

fn response_content_range(response: &reqwest::Response) -> Option<ContentRange> {
    response
        .headers()
        .get(reqwest::header::CONTENT_RANGE)
        .and_then(|value| value.to_str().ok())
        .and_then(parse_content_range)
}

fn build_download_client() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::limited(10))
        .connect_timeout(NATIVE_CONNECT_TIMEOUT)
        .read_timeout(NATIVE_READ_IDLE_TIMEOUT)
        .build()
        .map_err(|error| format!("native download client failed: {error}"))
}

fn cancelled_event(
    output_path: &str,
    bytes_written: u64,
    total_bytes: Option<u64>,
) -> NativeProgressEvent {
    let mut event = NativeProgressEvent::new("download_cancelled");
    event.output_path = Some(output_path.to_string());
    event.bytes_written = Some(bytes_written);
    event.total_bytes = total_bytes;
    event
}

/// Move a finished `.part` file into place and emit the `download_done` event.
fn finalize_download(
    part_path: &Path,
    output_path: &Path,
    transfer: &NativeTransferPlan,
    total_bytes: Option<u64>,
    diagnostics: Option<(
        reqwest::StatusCode,
        Option<String>,
        Option<u64>,
        Option<String>,
    )>,
    on_progress: &mut impl FnMut(NativeProgressEvent),
) -> Result<u64, String> {
    fs::rename(part_path, output_path)
        .map_err(|error| format!("download finalize failed: {error}"))?;
    let file_size = output_path
        .metadata()
        .map_err(|error| format!("download stat failed: {error}"))?
        .len();
    let mut done = NativeProgressEvent::new("download_done");
    done.output_path = Some(transfer.output_path.clone());
    done.bytes_written = Some(file_size);
    done.total_bytes = Some(total_bytes.unwrap_or(file_size));
    done.file_size = Some(file_size);
    if let Some((status, content_type, content_length, host)) = diagnostics {
        attach_response_diagnostics(&mut done, status, content_type, content_length, host);
    }
    on_progress(done);
    Ok(file_size)
}

/// Blocking entry point used by the job worker threads.
pub(crate) fn native_transfer_to_path(
    transfer: &NativeTransferPlan,
    on_progress: impl FnMut(NativeProgressEvent),
    should_cancel: impl FnMut() -> bool,
) -> Result<u64, String> {
    tauri::async_runtime::block_on(native_transfer_async(transfer, on_progress, should_cancel))
}

async fn native_transfer_async(
    transfer: &NativeTransferPlan,
    mut on_progress: impl FnMut(NativeProgressEvent),
    mut should_cancel: impl FnMut() -> bool,
) -> Result<u64, String> {
    let output_path = PathBuf::from(&transfer.output_path);
    if let Some(parent) = output_path.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("download dir create failed: {error}"))?;
    }
    let part_path = native_part_path(&output_path);
    let mut resume_from = part_path.metadata().map(|meta| meta.len()).unwrap_or(0);
    let client = build_download_client()?;

    // At most: one ranged attempt, then one full restart (416 / range mismatch).
    let mut response = None;
    for _ in 0..2 {
        if should_cancel() {
            on_progress(cancelled_event(
                &transfer.output_path,
                resume_from,
                transfer.total_bytes,
            ));
            return Err(DOWNLOAD_CANCELLED.to_string());
        }
        let mut request = client.get(&transfer.download_url);
        if let Some(referer) = &transfer.referer {
            request = request.header(reqwest::header::REFERER, referer);
        }
        if resume_from > 0 {
            request = request.header(reqwest::header::RANGE, format!("bytes={resume_from}-"));
        }
        let candidate = request
            .send()
            .await
            .map_err(|error| format!("native download request failed: {error}"))?;
        let status = candidate.status();

        if resume_from > 0 && status == reqwest::StatusCode::RANGE_NOT_SATISFIABLE {
            // The server says our offset is beyond the end of the file: either the
            // .part is already complete or it is stale.
            let known_total = response_content_range(&candidate)
                .and_then(|range| range.total)
                .or(transfer.total_bytes);
            let mut event = NativeProgressEvent::new("download_response");
            event.output_path = Some(transfer.output_path.clone());
            event.resume_from = Some(resume_from);
            attach_response_diagnostics(
                &mut event,
                status,
                response_content_type(&candidate),
                candidate.content_length(),
                response_host(&candidate),
            );
            on_progress(event);
            if known_total == Some(resume_from) {
                return finalize_download(
                    &part_path,
                    &output_path,
                    transfer,
                    known_total,
                    Some((
                        status,
                        response_content_type(&candidate),
                        candidate.content_length(),
                        response_host(&candidate),
                    )),
                    &mut on_progress,
                );
            }
            let _ = fs::remove_file(&part_path);
            resume_from = 0;
            continue;
        }

        if resume_from > 0 && status == reqwest::StatusCode::PARTIAL_CONTENT {
            let start = response_content_range(&candidate).and_then(|range| range.start);
            if start.is_some_and(|start| start != resume_from) {
                // Server resumed from a different offset; appending would corrupt the file.
                let _ = fs::remove_file(&part_path);
                resume_from = 0;
                continue;
            }
        }

        if !status.is_success() {
            // Keep the .part file: an HTTP error says nothing about its validity.
            let mut event = NativeProgressEvent::new("download_response");
            event.output_path = Some(transfer.output_path.clone());
            event.resume_from = Some(resume_from);
            attach_response_diagnostics(
                &mut event,
                status,
                response_content_type(&candidate),
                candidate.content_length(),
                response_host(&candidate),
            );
            on_progress(event);
            return Err(http_status_error(status));
        }

        if resume_from > 0 && status != reqwest::StatusCode::PARTIAL_CONTENT {
            // 200 with a full body: the server ignored Range, so restart from byte 0.
            let _ = fs::remove_file(&part_path);
            resume_from = 0;
        }
        response = Some(candidate);
        break;
    }
    let Some(mut response) = response else {
        return Err("native download could not restart after range failure".to_string());
    };

    let status = response.status();
    let response_content_type = response_content_type(&response);
    let response_host = response_host(&response);
    let response_length = response.content_length();
    let expected_total = if status == reqwest::StatusCode::PARTIAL_CONTENT {
        response_content_range(&response)
            .and_then(|range| range.total)
            .or_else(|| response_length.map(|size| size + resume_from))
    } else {
        response_length
    };
    let total_bytes = expected_total.or(transfer.total_bytes);

    let mut response_event = NativeProgressEvent::new("download_response");
    response_event.output_path = Some(transfer.output_path.clone());
    response_event.resume_from = Some(resume_from);
    attach_response_diagnostics(
        &mut response_event,
        status,
        response_content_type.clone(),
        response_length,
        response_host.clone(),
    );
    on_progress(response_event);

    let mut bytes_written = resume_from;
    let mut started = NativeProgressEvent::new("download_started");
    started.output_path = Some(transfer.output_path.clone());
    started.bytes_written = Some(bytes_written);
    started.total_bytes = total_bytes;
    started.resume_from = Some(resume_from);
    attach_response_diagnostics(
        &mut started,
        status,
        response_content_type.clone(),
        response_length,
        response_host.clone(),
    );
    on_progress(started);

    let mut file = if resume_from > 0 {
        File::options()
            .append(true)
            .open(&part_path)
            .map_err(|error| format!("partial file open failed: {error}"))?
    } else {
        File::create(&part_path).map_err(|error| format!("partial file create failed: {error}"))?
    };
    loop {
        if should_cancel() {
            on_progress(cancelled_event(
                &transfer.output_path,
                bytes_written,
                total_bytes,
            ));
            return Err(DOWNLOAD_CANCELLED.to_string());
        }
        let chunk = response
            .chunk()
            .await
            .map_err(|error| format!("native download read failed: {error}"))?;
        let Some(chunk) = chunk else {
            break;
        };
        file.write_all(&chunk[..])
            .map_err(|error| format!("native download write failed: {error}"))?;
        bytes_written += chunk.len() as u64;
        let mut event = NativeProgressEvent::new("download_progress");
        event.output_path = Some(transfer.output_path.clone());
        event.bytes_written = Some(bytes_written);
        event.total_bytes = total_bytes;
        on_progress(event);
    }
    drop(file);
    if let Some(expected) = expected_total {
        if bytes_written != expected {
            // Truncated body: keep the .part so the next attempt can resume it.
            return Err(format!(
                "native download incomplete: {bytes_written} of {expected} bytes"
            ));
        }
    }
    finalize_download(
        &part_path,
        &output_path,
        transfer,
        total_bytes,
        Some((
            status,
            response_content_type,
            response_length,
            response_host,
        )),
        &mut on_progress,
    )
}

fn mark_item_written(item: &serde_json::Value, file_size: u64) -> serde_json::Value {
    let mut result = item.clone();
    set_json_bool(&mut result, "dry_run", false);
    set_json_bool(&mut result, "written", true);
    set_json_u64(&mut result, "file_size", file_size);
    result
}

fn start_native_single_job(job: SharedNativeDownloadJob, request: NativeDownloadStartRequest) {
    std::thread::spawn(move || {
        if !begin_native_job(&job) {
            return;
        }
        let Some(transfer) = request.native_download.clone() else {
            let mut result = request.result.clone();
            set_json_bool(&mut result, "skipped", true);
            update_native_job(&job, |job| {
                job.status = "succeeded".to_string();
                job.result = Some(result);
            });
            return;
        };
        let outcome = native_transfer_to_path(
            &transfer,
            |event| update_native_job(&job, |job| job.push_progress(event)),
            || native_job_cancel_requested(&job),
        );
        match outcome {
            Ok(file_size) => {
                let result = mark_item_written(&request.result, file_size);
                update_native_job(&job, |job| {
                    job.status = "succeeded".to_string();
                    job.result = Some(result);
                });
            }
            Err(error) if error == DOWNLOAD_CANCELLED || native_job_cancel_requested(&job) => {
                update_native_job(&job, |job| job.mark_cancelled());
            }
            Err(error) => {
                update_native_job(&job, |job| {
                    job.status = "failed".to_string();
                    job.error_code = Some(error);
                });
            }
        }
    });
}

fn start_native_batch_job(job: SharedNativeDownloadJob, request: NativeBatchDownloadStartRequest) {
    std::thread::spawn(move || {
        if !begin_native_job(&job) {
            return;
        }
        update_native_job(&job, |job| {
            let mut event = NativeProgressEvent::new("batch_downloads_started");
            event.total = Some(request.items.len());
            event.download_count = Some(
                request
                    .items
                    .iter()
                    .filter(|item| item.native_download.is_some())
                    .count(),
            );
            event.max_parallel_downloads = Some(1);
            job.push_progress(event);
        });
        let total = request.items.len();
        let mut result_items = Vec::with_capacity(total);
        for (offset, item) in request.items.iter().cloned().enumerate() {
            let index = offset + 1;
            if native_job_cancel_requested(&job) {
                update_native_job(&job, |job| job.mark_cancelled());
                return;
            }
            let Some(transfer) = item.native_download.clone() else {
                let mut skipped = item.result.clone();
                set_json_bool(&mut skipped, "dry_run", false);
                set_json_bool(&mut skipped, "skipped", true);
                result_items.push(skipped);
                continue;
            };
            let outcome = native_transfer_to_path(
                &transfer,
                |mut event| {
                    event.index = Some(index);
                    event.total = Some(total);
                    if let Some(module_item_id) = item.result.get("module_item_id").cloned() {
                        event.module_item_id = Some(module_item_id);
                    }
                    if let Some(title) = item.result.get("title").and_then(|value| value.as_str()) {
                        event.title = Some(title.to_string());
                    }
                    update_native_job(&job, |job| job.push_progress(event));
                },
                || native_job_cancel_requested(&job),
            );
            match outcome {
                Ok(file_size) => result_items.push(mark_item_written(&item.result, file_size)),
                Err(error) if error == DOWNLOAD_CANCELLED || native_job_cancel_requested(&job) => {
                    update_native_job(&job, |job| job.mark_cancelled());
                    return;
                }
                Err(error) => {
                    let mut result = item.result.clone();
                    set_json_bool(&mut result, "dry_run", false);
                    set_json_bool(&mut result, "written", false);
                    if let serde_json::Value::Object(map) = &mut result {
                        map.insert("ok".to_string(), serde_json::Value::Bool(false));
                        map.insert("error_code".to_string(), serde_json::Value::String(error));
                    }
                    result_items.push(result);
                }
            }
        }
        let success_count = result_items
            .iter()
            .filter(|item| {
                item.get("ok")
                    .and_then(|value| value.as_bool())
                    .unwrap_or(false)
            })
            .count();
        let failure_count = result_items.len().saturating_sub(success_count);
        let result = serde_json::json!({
            "ok": failure_count == 0,
            "error_code": if failure_count == 0 { serde_json::Value::Null } else { serde_json::Value::String("partial_failure".to_string()) },
            "dry_run": false,
            "course_id": request.course_id,
            "course_name": request.course_name,
            "total_count": result_items.len(),
            "success_count": success_count,
            "failure_count": failure_count,
            "items": result_items,
            "progress": [],
        });
        update_native_job(&job, |job| {
            if job.status != "cancelled" {
                job.status = if failure_count == 0 {
                    "succeeded"
                } else {
                    "failed"
                }
                .to_string();
                job.result = Some(result);
            }
        });
    });
}

fn create_and_snapshot(
    state: &NativeDownloadJobs,
) -> Result<(SharedNativeDownloadJob, NativeDownloadJobStatus), String> {
    let job = state.create()?;
    let snapshot = job
        .lock()
        .map_err(|_| "native job lock poisoned".to_string())?
        .snapshot();
    Ok((job, snapshot))
}

#[tauri::command]
pub(crate) fn native_start_download_job(
    state: tauri::State<'_, NativeDownloadJobs>,
    request: NativeDownloadStartRequest,
) -> Result<NativeDownloadJobStatus, String> {
    let (job, snapshot) = create_and_snapshot(&state)?;
    start_native_single_job(job, request);
    Ok(snapshot)
}

#[tauri::command]
pub(crate) fn native_start_batch_download_job(
    state: tauri::State<'_, NativeDownloadJobs>,
    request: NativeBatchDownloadStartRequest,
) -> Result<NativeDownloadJobStatus, String> {
    let (job, snapshot) = create_and_snapshot(&state)?;
    start_native_batch_job(job, request);
    Ok(snapshot)
}

#[tauri::command]
pub(crate) fn native_download_job_status(
    state: tauri::State<'_, NativeDownloadJobs>,
    job_id: String,
) -> Result<NativeDownloadJobStatus, String> {
    let job = state
        .get(&job_id)?
        .ok_or_else(|| "native_download_job_not_found".to_string())?;
    job.lock()
        .map_err(|_| "native job lock poisoned".to_string())
        .map(|job| job.snapshot())
}

#[tauri::command]
pub(crate) fn native_cancel_download_job(
    state: tauri::State<'_, NativeDownloadJobs>,
    job_id: String,
) -> Result<NativeDownloadJobStatus, String> {
    let job = state
        .get(&job_id)?
        .ok_or_else(|| "native_download_job_not_found".to_string())?;
    let mut guard = job
        .lock()
        .map_err(|_| "native job lock poisoned".to_string())?;
    guard.request_cancel();
    Ok(guard.snapshot())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::TcpListener;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "ssu-native-{name}-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    /// Minimal one-shot-per-connection HTTP server: `handler` maps the raw
    /// request text to the raw response bytes.
    fn serve(
        connections: usize,
        handler: impl Fn(&str) -> Vec<u8> + Send + 'static,
    ) -> (String, std::thread::JoinHandle<()>) {
        let listener = TcpListener::bind(("127.0.0.1", 0)).expect("bind");
        let url = format!(
            "http://127.0.0.1:{}/lecture.mp4",
            listener.local_addr().unwrap().port()
        );
        let handle = std::thread::spawn(move || {
            for _ in 0..connections {
                let Ok((mut stream, _)) = listener.accept() else {
                    return;
                };
                let mut request = Vec::new();
                let mut buf = [0_u8; 1024];
                while !request.windows(4).any(|window| window == b"\r\n\r\n") {
                    match stream.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => request.extend_from_slice(&buf[..n]),
                    }
                }
                let response = handler(&String::from_utf8_lossy(&request));
                let _ = stream.write_all(&response);
            }
        });
        (url, handle)
    }

    fn response(status: &str, headers: &[(&str, String)], body: &[u8]) -> Vec<u8> {
        let mut out = format!("HTTP/1.1 {status}\r\nConnection: close\r\n");
        for (key, value) in headers {
            out.push_str(&format!("{key}: {value}\r\n"));
        }
        out.push_str("\r\n");
        let mut bytes = out.into_bytes();
        bytes.extend_from_slice(body);
        bytes
    }

    fn plan(url: &str, output: &Path) -> NativeTransferPlan {
        NativeTransferPlan {
            download_url: url.to_string(),
            referer: None,
            output_path: output.to_string_lossy().to_string(),
            total_bytes: None,
        }
    }

    fn run_transfer(
        transfer: &NativeTransferPlan,
    ) -> (Result<u64, String>, Vec<NativeProgressEvent>) {
        let mut events = Vec::new();
        let outcome = native_transfer_to_path(transfer, |event| events.push(event), || false);
        (outcome, events)
    }

    #[test]
    fn native_part_path_appends_part_after_existing_extension() {
        assert_eq!(
            native_part_path(Path::new("/tmp/course/lecture.mp4")),
            Path::new("/tmp/course/lecture.mp4.part")
        );
    }

    #[test]
    fn native_job_status_starts_as_queued_with_empty_progress() {
        let job = NativeDownloadJob::new("native-1".to_string());
        let snapshot = job.snapshot();
        assert_eq!(snapshot.job_id, "native-1");
        assert_eq!(snapshot.status, "queued");
        assert_eq!(snapshot.progress.len(), 0);
        assert!(!snapshot.cancel_requested);
    }

    #[test]
    fn native_job_compacts_progress_ticks_for_the_same_file() {
        let mut job = NativeDownloadJob::new("native-1".to_string());
        let mut first = NativeProgressEvent::new("download_progress");
        first.output_path = Some("/tmp/lecture.mp4".to_string());
        first.bytes_written = Some(1024);
        let mut second = first.clone();
        second.bytes_written = Some(2048);

        job.push_progress(first);
        job.push_progress(second);

        assert_eq!(job.progress.len(), 1);
        assert_eq!(job.progress[0].bytes_written, Some(2048));
    }

    #[test]
    fn http_errors_use_stable_status_code_without_reason_phrase() {
        assert_eq!(
            http_status_error(reqwest::StatusCode::FORBIDDEN),
            "HTTP 403"
        );
        assert_eq!(
            http_status_error(reqwest::StatusCode::UNAUTHORIZED),
            "HTTP 401"
        );
        assert_eq!(
            http_status_error(reqwest::StatusCode::NOT_FOUND),
            "HTTP 404"
        );
    }

    #[test]
    fn content_range_parsing_handles_partial_and_unsatisfied_forms() {
        assert_eq!(
            parse_content_range("bytes 100-199/1000"),
            Some(ContentRange {
                start: Some(100),
                total: Some(1000)
            })
        );
        assert_eq!(
            parse_content_range("bytes */1000"),
            Some(ContentRange {
                start: None,
                total: Some(1000)
            })
        );
        assert_eq!(
            parse_content_range("bytes 0-9/*"),
            Some(ContentRange {
                start: Some(0),
                total: None
            })
        );
        assert_eq!(parse_content_range("items 0-9/10"), None);
    }

    #[test]
    fn cancel_while_queued_is_not_overwritten_by_worker_start() {
        let mut job = NativeDownloadJob::new("native-1".to_string());
        job.request_cancel();
        assert_eq!(job.status, "cancelled");
        assert!(!job.begin(), "worker must not start a cancelled job");
        assert_eq!(job.status, "cancelled");
        assert_eq!(job.error_code.as_deref(), Some("DownloadCancelled"));

        let mut fresh = NativeDownloadJob::new("native-2".to_string());
        assert!(fresh.begin());
        assert_eq!(fresh.status, "running");
    }

    #[test]
    fn batch_job_cancelled_before_first_item_ends_cancelled_not_succeeded() {
        let job = Arc::new(Mutex::new(NativeDownloadJob::new("native-1".to_string())));
        update_native_job(&job, |job| job.request_cancel());
        let request = NativeBatchDownloadStartRequest {
            course_id: serde_json::json!(1),
            course_name: "c".to_string(),
            items: vec![NativeDownloadStartRequest {
                result: serde_json::json!({"module_item_id": 1}),
                native_download: None,
            }],
        };
        start_native_batch_job(Arc::clone(&job), request);
        std::thread::sleep(Duration::from_millis(200));
        let snapshot = job.lock().unwrap().snapshot();
        assert_eq!(snapshot.status, "cancelled");
        assert!(snapshot.result.is_none());
    }

    #[test]
    fn finished_jobs_are_evicted_oldest_first_but_active_jobs_stay() {
        let jobs = NativeDownloadJobs::new();
        let active = jobs.create().expect("active job");
        for _ in 0..(MAX_FINISHED_NATIVE_JOBS + 5) {
            let job = jobs.create().expect("job");
            update_native_job(&job, |job| job.status = "succeeded".to_string());
        }
        // Trigger eviction with one more create.
        jobs.create().expect("trigger");
        let registry = jobs.jobs.lock().unwrap();
        let finished = registry
            .values()
            .filter(|job| is_terminal_status(&job.lock().unwrap().status))
            .count();
        assert_eq!(finished, MAX_FINISHED_NATIVE_JOBS);
        let active_id = active.lock().unwrap().job_id.clone();
        assert!(registry.contains_key(&active_id));
        assert!(
            !registry.contains_key("native-2"),
            "oldest finished job evicted"
        );
    }

    #[test]
    fn transfer_downloads_full_body_and_removes_part_file() {
        let dir = temp_dir("full");
        let output = dir.join("lecture.mp4");
        let (url, server) = serve(1, |_| {
            response(
                "200 OK",
                &[("Content-Length", "10".to_string())],
                b"0123456789",
            )
        });
        let (outcome, events) = run_transfer(&plan(&url, &output));
        server.join().unwrap();
        assert_eq!(outcome, Ok(10));
        assert_eq!(fs::read(&output).unwrap(), b"0123456789");
        assert!(!native_part_path(&output).exists());
        assert!(events.iter().any(|event| event.stage == "download_done"));
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn transfer_resumes_part_file_with_range_request() {
        let dir = temp_dir("resume");
        let output = dir.join("lecture.mp4");
        fs::write(native_part_path(&output), b"01234").unwrap();
        let (url, server) = serve(1, |request| {
            assert!(
                request.to_ascii_lowercase().contains("range: bytes=5-"),
                "{request}"
            );
            response(
                "206 Partial Content",
                &[
                    ("Content-Length", "5".to_string()),
                    ("Content-Range", "bytes 5-9/10".to_string()),
                ],
                b"56789",
            )
        });
        let (outcome, _) = run_transfer(&plan(&url, &output));
        server.join().unwrap();
        assert_eq!(outcome, Ok(10));
        assert_eq!(fs::read(&output).unwrap(), b"0123456789");
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn transfer_finalizes_complete_part_file_when_server_answers_416() {
        let dir = temp_dir("416-complete");
        let output = dir.join("lecture.mp4");
        fs::write(native_part_path(&output), b"0123456789").unwrap();
        let (url, server) = serve(1, |_| {
            response(
                "416 Range Not Satisfiable",
                &[
                    ("Content-Range", "bytes */10".to_string()),
                    ("Content-Length", "0".to_string()),
                ],
                b"",
            )
        });
        let (outcome, _) = run_transfer(&plan(&url, &output));
        server.join().unwrap();
        assert_eq!(outcome, Ok(10));
        assert_eq!(fs::read(&output).unwrap(), b"0123456789");
        assert!(!native_part_path(&output).exists());
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn transfer_restarts_from_zero_when_416_does_not_match_part_size() {
        let dir = temp_dir("416-restart");
        let output = dir.join("lecture.mp4");
        fs::write(native_part_path(&output), b"stale-longer-than-file").unwrap();
        let (url, server) = serve(2, |request| {
            if request.to_ascii_lowercase().contains("range:") {
                response(
                    "416 Range Not Satisfiable",
                    &[
                        ("Content-Range", "bytes */10".to_string()),
                        ("Content-Length", "0".to_string()),
                    ],
                    b"",
                )
            } else {
                response(
                    "200 OK",
                    &[("Content-Length", "10".to_string())],
                    b"0123456789",
                )
            }
        });
        let (outcome, _) = run_transfer(&plan(&url, &output));
        server.join().unwrap();
        assert_eq!(outcome, Ok(10));
        assert_eq!(fs::read(&output).unwrap(), b"0123456789");
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn transfer_restarts_when_server_ignores_range_with_full_200() {
        let dir = temp_dir("range-ignored");
        let output = dir.join("lecture.mp4");
        fs::write(native_part_path(&output), b"abc").unwrap();
        let (url, server) = serve(1, |_| {
            response(
                "200 OK",
                &[("Content-Length", "10".to_string())],
                b"0123456789",
            )
        });
        let (outcome, _) = run_transfer(&plan(&url, &output));
        server.join().unwrap();
        assert_eq!(outcome, Ok(10));
        assert_eq!(fs::read(&output).unwrap(), b"0123456789");
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn transfer_keeps_part_file_on_http_error_and_reports_stable_code() {
        let dir = temp_dir("http-403");
        let output = dir.join("lecture.mp4");
        fs::write(native_part_path(&output), b"01234").unwrap();
        let (url, server) = serve(1, |_| {
            response("403 Forbidden", &[("Content-Length", "0".to_string())], b"")
        });
        let (outcome, _) = run_transfer(&plan(&url, &output));
        server.join().unwrap();
        assert_eq!(outcome, Err("HTTP 403".to_string()));
        assert_eq!(fs::read(native_part_path(&output)).unwrap(), b"01234");
        assert!(!output.exists());
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn transfer_rejects_truncated_body_and_keeps_part_for_resume() {
        let dir = temp_dir("truncated");
        let output = dir.join("lecture.mp4");
        let (url, server) = serve(1, |_| {
            // Promises 10 bytes, delivers 4, then closes.
            response("200 OK", &[("Content-Length", "10".to_string())], b"0123")
        });
        let (outcome, _) = run_transfer(&plan(&url, &output));
        server.join().unwrap();
        assert!(outcome.is_err(), "{outcome:?}");
        assert!(
            !output.exists(),
            "incomplete download must not be finalized"
        );
        assert!(native_part_path(&output).exists());
        fs::remove_dir_all(dir).ok();
    }

    #[test]
    fn transfer_stops_when_cancelled_between_chunks() {
        let dir = temp_dir("cancel");
        let output = dir.join("lecture.mp4");
        let (url, server) = serve(1, |_| {
            response(
                "200 OK",
                &[("Content-Length", "10".to_string())],
                b"0123456789",
            )
        });
        let mut calls = 0;
        let mut events = Vec::new();
        let outcome = native_transfer_to_path(
            &plan(&url, &output),
            |event| events.push(event),
            || {
                calls += 1;
                calls > 1
            },
        );
        server.join().unwrap();
        assert_eq!(outcome, Err("DownloadCancelled".to_string()));
        assert!(events
            .iter()
            .any(|event| event.stage == "download_cancelled"));
        assert!(!output.exists());
        fs::remove_dir_all(dir).ok();
    }
}
