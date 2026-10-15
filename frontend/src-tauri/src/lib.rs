use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::ffi::CStr;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

const BACKEND_HOST: &str = "127.0.0.1";
const DEFAULT_BACKEND_PORT: u16 = 8000;
const BACKEND_HEALTH_PATH: &str = "/api/health";
const MAX_NATIVE_JOB_PROGRESS_EVENTS: usize = 400;
const COALESCED_NATIVE_PROGRESS_STAGES: &[&str] = &["download_progress"];

#[derive(Debug, Clone, Serialize, Deserialize)]
struct NativeProgressEvent {
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
    fn new(stage: &str) -> Self {
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
struct NativeDownloadJobStatus {
    job_id: String,
    status: String,
    cancel_requested: bool,
    progress: Vec<NativeProgressEvent>,
    result: Option<serde_json::Value>,
    error_code: Option<String>,
}

#[derive(Debug)]
struct NativeDownloadJob {
    job_id: String,
    status: String,
    cancel_requested: bool,
    progress: Vec<NativeProgressEvent>,
    result: Option<serde_json::Value>,
    error_code: Option<String>,
}

impl NativeDownloadJob {
    fn new(job_id: String) -> Self {
        Self {
            job_id,
            status: "queued".to_string(),
            cancel_requested: false,
            progress: Vec::new(),
            result: None,
            error_code: None,
        }
    }

    fn snapshot(&self) -> NativeDownloadJobStatus {
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

struct NativeDownloadJobs {
    jobs: Mutex<HashMap<String, SharedNativeDownloadJob>>,
    next_id: AtomicU64,
}

impl NativeDownloadJobs {
    fn new() -> Self {
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
        self.jobs
            .lock()
            .map_err(|_| "native job registry lock poisoned".to_string())?
            .insert(job_id, Arc::clone(&job));
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

#[derive(Debug, Clone, Deserialize)]
struct NativeTransferPlan {
    download_url: String,
    referer: Option<String>,
    output_path: String,
    total_bytes: Option<u64>,
}

#[derive(Debug, Clone, Deserialize)]
struct NativeDownloadStartRequest {
    result: serde_json::Value,
    native_download: Option<NativeTransferPlan>,
}

#[derive(Debug, Clone, Deserialize)]
struct NativeBatchDownloadStartRequest {
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

fn response_content_type(response: &reqwest::blocking::Response) -> Option<String> {
    response
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(|value| value.to_string())
}

fn response_host(response: &reqwest::blocking::Response) -> Option<String> {
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

const NATIVE_DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(3 * 60 * 60);

fn native_transfer_to_path(
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
    let client = reqwest::blocking::Client::builder()
        .redirect(reqwest::redirect::Policy::limited(10))
        .timeout(NATIVE_DOWNLOAD_TIMEOUT)
        .build()
        .map_err(|error| format!("native download client failed: {error}"))?;
    let mut request = client.get(&transfer.download_url);
    if let Some(referer) = &transfer.referer {
        request = request.header(reqwest::header::REFERER, referer);
    }
    if resume_from > 0 {
        request = request.header(reqwest::header::RANGE, format!("bytes={resume_from}-"));
    }
    let mut response = request
        .send()
        .map_err(|error| format!("native download request failed: {error}"))?;
    let status = response.status();
    let response_content_type = response_content_type(&response);
    let response_host = response_host(&response);
    let response_total = response.content_length();
    if resume_from > 0 && status != reqwest::StatusCode::PARTIAL_CONTENT {
        resume_from = 0;
        let _ = fs::remove_file(&part_path);
    }
    let mut response_event = NativeProgressEvent::new("download_response");
    response_event.output_path = Some(transfer.output_path.clone());
    response_event.resume_from = Some(resume_from);
    attach_response_diagnostics(
        &mut response_event,
        status,
        response_content_type.clone(),
        response_total,
        response_host.clone(),
    );
    on_progress(response_event);
    if !status.is_success() {
        return Err(format!("HTTP {status}"));
    }
    let total_bytes = response_total
        .map(|size| size + resume_from)
        .or(transfer.total_bytes);
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
        response_total,
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
    let mut buffer = [0_u8; 1024 * 256];
    loop {
        if should_cancel() {
            let mut event = NativeProgressEvent::new("download_cancelled");
            event.output_path = Some(transfer.output_path.clone());
            event.bytes_written = Some(bytes_written);
            event.total_bytes = total_bytes;
            on_progress(event);
            return Err("DownloadCancelled".to_string());
        }
        let read = response
            .read(&mut buffer)
            .map_err(|error| format!("native download read failed: {error}"))?;
        if read == 0 {
            break;
        }
        file.write_all(&buffer[..read])
            .map_err(|error| format!("native download write failed: {error}"))?;
        bytes_written += read as u64;
        let mut event = NativeProgressEvent::new("download_progress");
        event.output_path = Some(transfer.output_path.clone());
        event.bytes_written = Some(bytes_written);
        event.total_bytes = total_bytes;
        on_progress(event);
    }
    drop(file);
    fs::rename(&part_path, &output_path)
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
    attach_response_diagnostics(
        &mut done,
        status,
        response_content_type,
        response_total,
        response_host,
    );
    on_progress(done);
    Ok(file_size)
}

fn start_native_single_job(job: SharedNativeDownloadJob, request: NativeDownloadStartRequest) {
    std::thread::spawn(move || {
        update_native_job(&job, |job| job.status = "running".to_string());
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
                let mut result = request.result.clone();
                set_json_bool(&mut result, "dry_run", false);
                set_json_bool(&mut result, "written", true);
                set_json_u64(&mut result, "file_size", file_size);
                update_native_job(&job, |job| {
                    job.status = "succeeded".to_string();
                    job.result = Some(result);
                });
            }
            Err(error) if error == "DownloadCancelled" || native_job_cancel_requested(&job) => {
                update_native_job(&job, |job| {
                    job.status = "cancelled".to_string();
                    job.error_code = Some("DownloadCancelled".to_string());
                });
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
        update_native_job(&job, |job| {
            job.status = "running".to_string();
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
                break;
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
                Ok(file_size) => {
                    let mut result = item.result.clone();
                    set_json_bool(&mut result, "dry_run", false);
                    set_json_bool(&mut result, "written", true);
                    set_json_u64(&mut result, "file_size", file_size);
                    result_items.push(result);
                }
                Err(error) if error == "DownloadCancelled" || native_job_cancel_requested(&job) => {
                    update_native_job(&job, |job| {
                        job.status = "cancelled".to_string();
                        job.error_code = Some("DownloadCancelled".to_string());
                    });
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

#[derive(Debug, Clone, PartialEq, Eq)]
struct BackendLaunchCommand {
    program: String,
    args: Vec<String>,
    cwd: PathBuf,
    packaged_sidecar: bool,
}

struct BackendManager {
    child: Mutex<Option<Child>>,
    mode: Mutex<String>,
    port: Mutex<u16>,
    project_root: PathBuf,
    resource_dir: Option<PathBuf>,
}

impl BackendManager {
    fn new(project_root: PathBuf, resource_dir: Option<PathBuf>) -> Self {
        Self {
            child: Mutex::new(None),
            mode: Mutex::new("uninitialized".to_string()),
            port: Mutex::new(DEFAULT_BACKEND_PORT),
            project_root,
            resource_dir,
        }
    }

    fn status(&self) -> String {
        self.mode
            .lock()
            .map(|mode| mode.clone())
            .unwrap_or_else(|_| "unknown".to_string())
    }

    fn set_mode(&self, value: &str) {
        if let Ok(mut mode) = self.mode.lock() {
            *mode = value.to_string();
        }
    }

    fn port(&self) -> u16 {
        self.port
            .lock()
            .map(|port| *port)
            .unwrap_or(DEFAULT_BACKEND_PORT)
    }

    fn set_port(&self, value: u16) {
        if let Ok(mut port) = self.port.lock() {
            *port = value;
        }
    }

    fn ensure_started(&self) -> Result<(), String> {
        if backend_health_check(DEFAULT_BACKEND_PORT, Duration::from_millis(250)) {
            self.set_port(DEFAULT_BACKEND_PORT);
            self.set_mode("external");
            return Ok(());
        }
        if std::env::var("SSU_BACKEND_AUTOSTART").ok().as_deref() == Some("0") {
            self.set_mode("disabled");
            return Ok(());
        }

        let port = choose_backend_port()
            .map_err(|error| format!("Python API 서버용 빈 포트를 찾을 수 없습니다: {error}"))?;
        self.set_port(port);
        let launch = backend_launch_command(&self.project_root, self.resource_dir.as_deref(), port);
        let child = Command::new(&launch.program)
            .args(&launch.args)
            .current_dir(&launch.cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| format!("Python API 서버를 시작할 수 없습니다: {error}"))?;

        if let Ok(mut guard) = self.child.lock() {
            *guard = Some(child);
        }
        if launch.packaged_sidecar {
            self.set_mode("managed-sidecar-starting");
        } else {
            self.set_mode("managed-starting");
        }

        if wait_for_backend(port, Duration::from_secs(20)) {
            if launch.packaged_sidecar {
                self.set_mode("managed-sidecar");
            } else {
                self.set_mode("managed");
            }
            Ok(())
        } else {
            self.set_mode("failed");
            Err("Python API 서버가 20초 안에 준비되지 않았습니다.".to_string())
        }
    }
}

impl Drop for BackendManager {
    fn drop(&mut self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[tauri::command]
fn real_download_dir() -> Result<String, String> {
    real_user_home_dir()
        .map(|home| home.join("Downloads").to_string_lossy().to_string())
        .ok_or_else(|| "Could not resolve the macOS user Downloads folder".to_string())
}

#[cfg(target_os = "macos")]
fn real_user_home_dir() -> Option<PathBuf> {
    unsafe {
        let passwd = libc::getpwuid(libc::getuid());
        if passwd.is_null() {
            return fallback_home_dir();
        }
        let home = CStr::from_ptr((*passwd).pw_dir)
            .to_string_lossy()
            .to_string();
        if home.is_empty() {
            fallback_home_dir()
        } else {
            Some(PathBuf::from(home))
        }
    }
}

#[cfg(not(target_os = "macos"))]
fn real_user_home_dir() -> Option<PathBuf> {
    fallback_home_dir()
}

fn fallback_home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
}

fn folder_to_open(path: &Path) -> PathBuf {
    if path.is_file() {
        path.parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| path.to_path_buf())
    } else {
        path.to_path_buf()
    }
}

fn backend_addr(port: u16) -> String {
    format!("{BACKEND_HOST}:{port}")
}

fn backend_health_check(port: u16, timeout: Duration) -> bool {
    let Ok(mut stream) =
        TcpStream::connect_timeout(&backend_addr(port).parse().expect("backend addr"), timeout)
    else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let request = format!(
        "GET {BACKEND_HEALTH_PATH} HTTP/1.1\r\nHost: {BACKEND_HOST}:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && backend_health_response_is_compatible(&response)
}

fn backend_health_response_is_compatible(response: &str) -> bool {
    response.starts_with("HTTP/1.1 200")
        && response.contains("\"ok\":true")
        && response.contains("\"app\":\"ssu-lecture-downloader\"")
        && response.contains("artifact_download_jobs")
        && response.contains("stt_provider_status")
}

fn wait_for_backend(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if backend_health_check(port, Duration::from_millis(500)) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    false
}

fn choose_backend_port() -> std::io::Result<u16> {
    TcpListener::bind((BACKEND_HOST, 0))
        .and_then(|listener| listener.local_addr().map(|addr| addr.port()))
}

fn project_root_from_manifest_dir(manifest_dir: &Path) -> PathBuf {
    manifest_dir
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| manifest_dir.to_path_buf())
}

fn backend_launch_command(
    project_root: &Path,
    resource_dir: Option<&Path>,
    port: u16,
) -> BackendLaunchCommand {
    if let Some(executable) = packaged_backend_executable(project_root, resource_dir) {
        let cwd = executable
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| project_root.to_path_buf());
        return BackendLaunchCommand {
            program: executable.to_string_lossy().to_string(),
            args: vec![
                "--host".to_string(),
                BACKEND_HOST.to_string(),
                "--port".to_string(),
                port.to_string(),
            ],
            cwd,
            packaged_sidecar: true,
        };
    }

    BackendLaunchCommand {
        program: "uv".to_string(),
        args: vec![
            "run".to_string(),
            "uvicorn".to_string(),
            "ssu_lecture_downloader.api:app".to_string(),
            "--host".to_string(),
            BACKEND_HOST.to_string(),
            "--port".to_string(),
            port.to_string(),
        ],
        cwd: project_root.to_path_buf(),
        packaged_sidecar: false,
    }
}

fn packaged_backend_executable(
    project_root: &Path,
    resource_dir: Option<&Path>,
) -> Option<PathBuf> {
    if let Some(value) = std::env::var_os("SSU_BACKEND_EXECUTABLE") {
        let path = PathBuf::from(value);
        if path.is_file() {
            return Some(path);
        }
    }

    for candidate in resource_dir.into_iter().flat_map(|dir| {
        [
            dir.join("backend-sidecar").join("ssu-api"),
            dir.join("resources")
                .join("backend-sidecar")
                .join("ssu-api"),
        ]
    }) {
        if candidate.is_file() {
            return Some(candidate);
        }
    }

    let dev_candidate = project_root
        .join("frontend")
        .join("src-tauri")
        .join("resources")
        .join("backend-sidecar")
        .join("ssu-api");
    if dev_candidate.is_file() {
        Some(dev_candidate)
    } else {
        None
    }
}

#[tauri::command]
fn backend_supervision_status(state: tauri::State<'_, BackendManager>) -> Result<String, String> {
    Ok(state.status())
}

#[tauri::command]
fn backend_api_base_url(state: tauri::State<'_, BackendManager>) -> Result<String, String> {
    Ok(format!("http://{BACKEND_HOST}:{}", state.port()))
}

#[tauri::command]
fn open_path_in_finder(path: String) -> Result<(), String> {
    if path.trim().is_empty() {
        return Err("다운로드 폴더 경로가 비어 있습니다.".to_string());
    }
    let folder = folder_to_open(Path::new(&path));
    Command::new("open")
        .arg(&folder)
        .status()
        .map_err(|error| format!("Finder를 열 수 없습니다: {error}"))
        .and_then(|status| {
            if status.success() {
                Ok(())
            } else {
                Err(format!("Finder 열기 명령이 실패했습니다: {status}"))
            }
        })
}

#[tauri::command]
fn open_url_in_browser(url: String) -> Result<(), String> {
    if !(url.starts_with("https://") || url.starts_with("http://")) {
        return Err("공식 시청 주소가 올바르지 않습니다.".to_string());
    }
    Command::new("open")
        .arg(url)
        .status()
        .map_err(|error| format!("브라우저를 열 수 없습니다: {error}"))
        .and_then(|status| {
            if status.success() {
                Ok(())
            } else {
                Err(format!("브라우저 열기 명령이 실패했습니다: {status}"))
            }
        })
}

#[derive(Debug, Clone, Serialize)]
struct WatchAssistEvent {
    stage: String,
}

#[derive(Debug, Clone, Serialize)]
struct WatchDebugEvent {
    stage: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    frame: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    host: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    ready_state: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    videos: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    video_state: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    buttons: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    selector: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_message: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct OfficialViewerBridgeCookie {
    name: String,
    value: String,
    domain: String,
    path: String,
    secure: bool,
    http_only: bool,
    same_site: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct OfficialViewerSessionBridgeRequest {
    title: Option<String>,
    viewer_url: Option<String>,
    launch_url: Option<String>,
    cookies: Vec<OfficialViewerBridgeCookie>,
}

fn official_viewer_bootstrap_url() -> tauri::Url {
    tauri::Url::parse("https://commons.ssu.ac.kr/")
        .expect("static official viewer bootstrap URL must parse")
}

fn validate_official_watch_url(url: &str) -> Result<tauri::Url, String> {
    let parsed = tauri::Url::parse(url).map_err(|_| "invalid_watch_url".to_string())?;
    if parsed.scheme() != "https" {
        return Err("unsupported_watch_url_scheme".to_string());
    }
    let host = parsed.host_str().unwrap_or_default();
    if host == "commons.ssu.ac.kr"
        || host.ends_with(".commons.ssu.ac.kr")
        || host == "ssu-toast.commonscdn.com"
        || host == "canvas.ssu.ac.kr"
    {
        return Ok(parsed);
    }
    Err("unsupported_watch_url_host".to_string())
}

fn watch_debug_event_from_url(url: &tauri::Url) -> WatchDebugEvent {
    let mut values: HashMap<String, String> = HashMap::new();
    for (key, value) in url.query_pairs() {
        values.insert(key.into_owned(), value.into_owned());
    }
    WatchDebugEvent {
        stage: values
            .get("stage")
            .cloned()
            .unwrap_or_else(|| "unknown".to_string()),
        mode: values.get("mode").cloned(),
        frame: values.get("frame").cloned(),
        host: values.get("host").cloned(),
        ready_state: values.get("ready").cloned(),
        videos: values.get("videos").cloned(),
        video_state: values.get("video").cloned(),
        buttons: values.get("buttons").cloned(),
        selector: values.get("selector").cloned(),
        error_name: values.get("error").cloned(),
        error_message: values.get("message").cloned(),
    }
}

fn emit_watch_debug_navigation(app: &tauri::AppHandle, nav_url: &tauri::Url) -> bool {
    if nav_url.scheme() == "ssu-watch" && nav_url.host_str() == Some("debug") {
        let _ = app.emit("official-watch-debug", watch_debug_event_from_url(nav_url));
        return true;
    }
    false
}

fn bridge_cookie_to_tauri(
    cookie: &OfficialViewerBridgeCookie,
) -> Result<tauri::webview::Cookie<'static>, String> {
    if cookie.name.trim().is_empty() || cookie.domain.trim().is_empty() {
        return Err("invalid_bridge_cookie".to_string());
    }
    let mut builder = tauri::webview::Cookie::build((cookie.name.clone(), cookie.value.clone()))
        .domain(cookie.domain.trim_start_matches('.').to_string())
        .path(if cookie.path.is_empty() {
            "/".to_string()
        } else {
            cookie.path.clone()
        })
        .secure(cookie.secure)
        .http_only(cookie.http_only);
    if let Some(same_site) = &cookie.same_site {
        let parsed_same_site = match same_site.to_ascii_lowercase().as_str() {
            "lax" => Some(tauri::webview::cookie::SameSite::Lax),
            "strict" => Some(tauri::webview::cookie::SameSite::Strict),
            "none" => Some(tauri::webview::cookie::SameSite::None),
            _ => None,
        };
        if let Some(parsed_same_site) = parsed_same_site {
            builder = builder.same_site(parsed_same_site);
        }
    }
    Ok(builder.build())
}

fn official_watch_assist_script() -> &'static str {
    r#"
(() => {
  if (window.__SSU_WATCH_ASSIST_INSTALLED__) return;
  window.__SSU_WATCH_ASSIST_INSTALLED__ = true;

  const mode = 'sequential';
  const shouldEmitEnded = true;
  const frame = (() => {
    try { return window.top === window ? 'top' : 'child'; } catch (_) { return 'unknown'; }
  })();
  const host = (() => {
    try { return window.location.hostname || 'unknown'; } catch (_) { return 'unknown'; }
  })();
  let lastDebug = '';
  const debug = (stage, extra = {}) => {
    const payload = {
      stage,
      mode,
      frame,
      host,
      ready: document.readyState,
      videos: String(document.querySelectorAll('video').length),
      buttons: String(document.querySelectorAll('button,[role="button"],.vjs-big-play-button,.vjs-play-control').length),
      ...extra
    };
    const key = JSON.stringify(payload);
    if (key === lastDebug) return;
    lastDebug = key;
    const query = Object.entries(payload)
      .filter(([, value]) => value !== undefined && value !== null && String(value).length > 0)
      .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value)).slice(0, 180)}`)
      .join('&');
    try {
      if (window.top === window) {
        window.location.href = `ssu-watch://debug?${query}`;
      } else {
        window.top.postMessage({ __SSU_WATCH_DEBUG__: true, payload }, '*');
      }
    } catch (_) {}
  };
  try {
    if (window.top === window) {
      window.addEventListener('message', (event) => {
        if (!event.data || event.data.__SSU_WATCH_DEBUG__ !== true || !event.data.payload) return;
        const payload = { ...event.data.payload, frame: event.data.payload.frame || 'child' };
        const query = Object.entries(payload)
          .filter(([, value]) => value !== undefined && value !== null && String(value).length > 0)
          .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value)).slice(0, 180)}`)
          .join('&');
        try { window.location.href = `ssu-watch://debug?${query}`; } catch (_) {}
      });
    }
  } catch (_) {}

  const SHORT_INTRO_MAX_SECONDS = 10;
  const videoDuration = (video) => Number.isFinite(video && video.duration) ? video.duration : 0;
  const isLikelyMainVideo = (video) => videoDuration(video) > SHORT_INTRO_MAX_SECONDS;

  const notifyEnded = (video) => {
    if (!isLikelyMainVideo(video)) {
      debug('short-video-ended', { video: videoState(video) });
      return;
    }
    if (!shouldEmitEnded) {
      debug('main-video-ended', { video: videoState(video) });
      return;
    }
    debug('ended', { video: videoState(video) });
    try {
      window.location.href = 'ssu-watch://ended';
    } catch (_) {}
  };

  const videoState = (video) => {
    if (!video) return 'none';
    return [
      `paused=${video.paused}`,
      `ended=${video.ended}`,
      `muted=${video.muted}`,
      `ready=${video.readyState}`,
      `network=${video.networkState}`,
      `time=${Number.isFinite(video.currentTime) ? Math.round(video.currentTime) : 'na'}`,
      `duration=${Number.isFinite(video.duration) ? Math.round(video.duration) : 'na'}`
    ].join(',');
  };

  const findVideo = () => {
    const videos = Array.from(document.querySelectorAll('video'));
    return videos.find((video) => {
      const rect = video.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && (video.currentSrc || video.src || video.readyState > 0);
    }) || videos[0];
  };

  const attachVideo = (video) => {
    if (!video) return false;
    const attachmentKey = `${video.currentSrc || video.src || 'pending'}|${Math.round(videoDuration(video))}`;
    if (video.__SSU_WATCH_ASSIST_ATTACHED__ !== attachmentKey) {
      video.__SSU_WATCH_ASSIST_ATTACHED__ = attachmentKey;
      video.addEventListener('ended', () => notifyEnded(video), { once: true });
      debug('video-attached', { video: videoState(video), main: String(isLikelyMainVideo(video)) });
    }
    return true;
  };

  const tryPlayVideo = (video, attempt) => {
    if (!attachVideo(video)) return false;
    debug('video-found', { video: videoState(video) });
    if (!video.paused && !video.ended) return true;
    try {
      debug('play-attempt', { video: videoState(video), attempt });
      const result = video.play();
      if (result && typeof result.then === 'function') {
        result.then(() => debug('play-resolved', { video: videoState(video) })).catch((error) => {
          debug('play-rejected', {
            video: videoState(video),
            error: error && error.name ? error.name : 'unknown',
            message: error && error.message ? error.message : 'no-message'
          });
        });
      }
    } catch (error) {
      debug('play-threw', {
        video: videoState(video),
        error: error && error.name ? error.name : 'unknown',
        message: error && error.message ? error.message : 'no-message'
      });
    }
    return !video.paused && !video.ended;
  };

  const clickLikelyPlayControl = () => {
    const selectors = [
      '.vjs-big-play-button',
      '.vjs-play-control',
      '.play-button',
      '.btn-play',
      '[class*="play"]',
      'button[aria-label="Play"]',
      'button[title="Play"]',
      'button[aria-label="재생"]',
      'button[title="재생"]',
      '[role="button"][aria-label="Play"]',
      '[role="button"][aria-label="재생"]'
    ];
    for (const selector of selectors) {
      const button = document.querySelector(selector);
      if (button && typeof button.click === 'function') {
        try {
          debug('button-click-attempt', { selector });
          button.click();
          debug('button-clicked', { selector });
          return true;
        } catch (error) {
          debug('button-click-error', {
            selector,
            error: error && error.name ? error.name : 'unknown',
            message: error && error.message ? error.message : 'no-message'
          });
        }
      }
    }
    return false;
  };

  debug('installed');
  let attempts = 0;
  const tick = () => {
    attempts += 1;
    const video = findVideo();
    const playing = tryPlayVideo(video, String(attempts));
    if (!playing) clickLikelyPlayControl();
    if (!video && (attempts === 1 || attempts % 10 === 0)) debug('no-video', { attempt: String(attempts) });
    const current = findVideo();
    const done = current && !current.paused && !current.ended && isLikelyMainVideo(current);
    if (done) debug('playing', { video: videoState(current), attempt: String(attempts), main: 'true' });
    return done;
  };

  const timer = setInterval(() => {
    if (tick() || attempts >= 120) {
      if (attempts >= 120) debug('gave-up', { attempt: String(attempts), video: videoState(findVideo()) });
      clearInterval(timer);
    }
  }, 500);
  tick();
})();
"#
}

#[tauri::command]
fn open_assisted_official_viewer(
    app: tauri::AppHandle,
    url: String,
    title: Option<String>,
) -> Result<(), String> {
    let parsed = validate_official_watch_url(&url)?;
    let label = "official-viewer";
    if let Some(existing) = app.get_webview_window(label) {
        existing.close().map_err(|error| error.to_string())?;
    }

    let nav_app = app.clone();
    let nav_label = label.to_string();
    let builder =
        tauri::WebviewWindowBuilder::new(&app, label, tauri::WebviewUrl::External(parsed))
            .title(title.unwrap_or_else(|| "숭실 LMS 공식 시청 창".to_string()))
            .inner_size(1180.0, 760.0)
            .min_inner_size(900.0, 620.0)
            .center()
            .focused(true)
            .resizable(true)
            .visible(true)
            .decorations(true);
    builder
        .initialization_script_for_all_frames(official_watch_assist_script())
        .on_navigation(move |nav_url| {
            if emit_watch_debug_navigation(&nav_app, nav_url) {
                return false;
            }
            if nav_url.scheme() == "ssu-watch" && nav_url.host_str() == Some("ended") {
                let _ = nav_app.emit(
                    "official-watch-ended",
                    WatchAssistEvent {
                        stage: "ended".to_string(),
                    },
                );
                if let Some(window) = nav_app.get_webview_window(&nav_label) {
                    let _ = window.close();
                }
                return false;
            }
            true
        })
        .build()
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn open_assisted_official_viewer_with_session(
    app: tauri::AppHandle,
    request: OfficialViewerSessionBridgeRequest,
) -> Result<(), String> {
    let fallback_viewer_url = request
        .viewer_url
        .ok_or_else(|| "missing_watch_url".to_string())?;
    let navigation_url = request
        .launch_url
        .as_deref()
        .unwrap_or(&fallback_viewer_url);
    let parsed = validate_official_watch_url(navigation_url)?;
    let label = "official-viewer";
    if let Some(existing) = app.get_webview_window(label) {
        existing.close().map_err(|error| error.to_string())?;
    }

    let nav_app = app.clone();
    let nav_label = label.to_string();
    let builder = tauri::WebviewWindowBuilder::new(
        &app,
        label,
        tauri::WebviewUrl::External(official_viewer_bootstrap_url()),
    )
    .title(request.title.unwrap_or_else(|| "숭실 LMS 공식 시청 창".to_string()))
    .inner_size(1180.0, 760.0)
    .min_inner_size(900.0, 620.0)
    .center()
    .focused(true)
    .resizable(true)
    .visible(true)
    .decorations(true)
    .user_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36");
    let window = builder
        .initialization_script_for_all_frames(official_watch_assist_script())
        .on_navigation(move |nav_url| {
            if emit_watch_debug_navigation(&nav_app, nav_url) {
                return false;
            }
            if nav_url.scheme() == "ssu-watch" && nav_url.host_str() == Some("ended") {
                let _ = nav_app.emit(
                    "official-watch-ended",
                    WatchAssistEvent {
                        stage: "ended".to_string(),
                    },
                );
                if let Some(window) = nav_app.get_webview_window(&nav_label) {
                    let _ = window.close();
                }
                return false;
            }
            true
        })
        .build()
        .map_err(|error| error.to_string())?;

    for cookie in &request.cookies {
        window
            .set_cookie(bridge_cookie_to_tauri(cookie)?)
            .map_err(|error| error.to_string())?;
    }
    window.navigate(parsed).map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn open_official_viewer_with_session(
    app: tauri::AppHandle,
    request: OfficialViewerSessionBridgeRequest,
) -> Result<(), String> {
    let fallback_viewer_url = request
        .viewer_url
        .ok_or_else(|| "missing_watch_url".to_string())?;
    let navigation_url = request
        .launch_url
        .as_deref()
        .unwrap_or(&fallback_viewer_url);
    let parsed = validate_official_watch_url(navigation_url)?;
    let label = "official-viewer";
    if let Some(existing) = app.get_webview_window(label) {
        existing.close().map_err(|error| error.to_string())?;
    }

    let window = tauri::WebviewWindowBuilder::new(
        &app,
        label,
        tauri::WebviewUrl::External(official_viewer_bootstrap_url()),
    )
    .title(request.title.unwrap_or_else(|| "숭실 LMS 공식 시청 창".to_string()))
    .inner_size(1180.0, 760.0)
    .min_inner_size(900.0, 620.0)
    .center()
    .focused(true)
    .resizable(true)
    .visible(true)
    .decorations(true)
    .user_agent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")
    .build()
    .map_err(|error| error.to_string())?;

    for cookie in &request.cookies {
        window
            .set_cookie(bridge_cookie_to_tauri(cookie)?)
            .map_err(|error| error.to_string())?;
    }
    window.navigate(parsed).map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn native_start_download_job(
    state: tauri::State<'_, NativeDownloadJobs>,
    request: NativeDownloadStartRequest,
) -> Result<NativeDownloadJobStatus, String> {
    let job = state.create()?;
    let snapshot = job
        .lock()
        .map_err(|_| "native job lock poisoned".to_string())?
        .snapshot();
    start_native_single_job(Arc::clone(&job), request);
    Ok(snapshot)
}

#[tauri::command]
fn native_start_batch_download_job(
    state: tauri::State<'_, NativeDownloadJobs>,
    request: NativeBatchDownloadStartRequest,
) -> Result<NativeDownloadJobStatus, String> {
    let job = state.create()?;
    let snapshot = job
        .lock()
        .map_err(|_| "native job lock poisoned".to_string())?
        .snapshot();
    start_native_batch_job(Arc::clone(&job), request);
    Ok(snapshot)
}

#[tauri::command]
fn native_download_job_status(
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
fn native_cancel_download_job(
    state: tauri::State<'_, NativeDownloadJobs>,
    job_id: String,
) -> Result<NativeDownloadJobStatus, String> {
    let job = state
        .get(&job_id)?
        .ok_or_else(|| "native_download_job_not_found".to_string())?;
    let mut guard = job
        .lock()
        .map_err(|_| "native job lock poisoned".to_string())?;
    guard.cancel_requested = true;
    if guard.status == "queued" {
        guard.status = "cancelled".to_string();
    }
    Ok(guard.snapshot())
}

pub fn run() {
    let project_root = project_root_from_manifest_dir(Path::new(env!("CARGO_MANIFEST_DIR")));
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(move |app| {
            let resource_dir = app.path().resource_dir().ok();
            let manager = BackendManager::new(project_root.clone(), resource_dir);
            manager.ensure_started()?;
            app.manage(manager);
            app.manage(NativeDownloadJobs::new());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            real_download_dir,
            open_path_in_finder,
            open_url_in_browser,
            open_assisted_official_viewer,
            open_assisted_official_viewer_with_session,
            open_official_viewer_with_session,
            backend_supervision_status,
            backend_api_base_url,
            native_start_download_job,
            native_start_batch_download_job,
            native_download_job_status,
            native_cancel_download_job
        ])
        .run(tauri::generate_context!())
        .expect("error while running SSU Lecture Downloader Tauri app");
}

#[cfg(test)]
mod tests {
    use super::{
        backend_health_response_is_compatible, backend_launch_command, folder_to_open,
        native_part_path, official_watch_assist_script, packaged_backend_executable,
        project_root_from_manifest_dir, real_download_dir, validate_official_watch_url,
        NativeDownloadJob, NativeProgressEvent,
    };
    use std::fs;
    use std::path::Path;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn real_download_dir_uses_login_user_downloads_not_hermes_profile_home() {
        let downloads = real_download_dir().expect("downloads path");
        assert!(downloads.ends_with("/Downloads"), "{downloads}");
        assert!(!downloads.contains("/.hermes/profiles/"), "{downloads}");
    }

    #[test]
    fn folder_to_open_uses_parent_when_given_downloaded_file_path() {
        let root = std::env::temp_dir().join(format!(
            "ssu-lecture-downloader-test-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        fs::create_dir_all(&root).expect("create temp dir");
        let file = root.join("lecture.mp4");
        fs::write(&file, b"test").expect("write temp file");

        assert_eq!(folder_to_open(&file), root);

        fs::remove_file(&file).ok();
        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn project_root_resolves_two_levels_above_tauri_manifest_dir() {
        let manifest = Path::new("/repo/frontend/src-tauri");
        assert_eq!(project_root_from_manifest_dir(manifest), Path::new("/repo"));
    }

    #[test]
    fn backend_launch_command_runs_uvicorn_from_project_root_when_no_sidecar_exists() {
        let launch = backend_launch_command(Path::new("/repo"), None, 49152);
        assert_eq!(launch.program, "uv");
        assert_eq!(launch.cwd, Path::new("/repo"));
        assert!(!launch.packaged_sidecar);
        assert_eq!(
            launch.args,
            vec![
                "run",
                "uvicorn",
                "ssu_lecture_downloader.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                "49152"
            ]
        );
    }

    #[test]
    fn backend_launch_command_prefers_packaged_resource_sidecar() {
        let root = std::env::temp_dir().join(format!(
            "ssu-sidecar-test-{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .expect("time")
                .as_nanos()
        ));
        let resource_root = root.join("resources");
        let resource_sidecar = resource_root.join("resources").join("backend-sidecar");
        fs::create_dir_all(&resource_sidecar).expect("create sidecar dir");
        let executable = resource_sidecar.join("ssu-api");
        fs::write(&executable, b"#!/bin/sh\n").expect("write sidecar");

        assert_eq!(
            packaged_backend_executable(Path::new("/repo"), Some(&resource_root)),
            Some(executable.clone())
        );
        let launch = backend_launch_command(Path::new("/repo"), Some(&resource_root), 49153);
        assert_eq!(launch.program, executable.to_string_lossy());
        assert_eq!(launch.cwd, resource_sidecar);
        assert!(launch.packaged_sidecar);
        assert_eq!(launch.args, vec!["--host", "127.0.0.1", "--port", "49153"]);

        fs::remove_dir_all(root).ok();
    }

    #[test]
    fn backend_health_requires_artifact_and_stt_capabilities() {
        let old_backend = "HTTP/1.1 200 OK\r\n\r\n{\"ok\":true}";
        assert!(!backend_health_response_is_compatible(old_backend));

        let compatible_backend = "HTTP/1.1 200 OK\r\n\r\n{\"ok\":true,\"app\":\"ssu-lecture-downloader\",\"capabilities\":[\"runtime_memory_credentials\",\"download_jobs\",\"artifact_download_jobs\",\"stt_provider_status\"]}";
        assert!(backend_health_response_is_compatible(compatible_backend));
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
    fn source_no_longer_installs_macos_webview_configuration_hook() {
        let source = include_str!("lib.rs");
        let production_source = source
            .split("#[cfg(test)]")
            .next()
            .expect("source has production section");
        assert!(
            !production_source.contains("with_webview_configuration"),
            "{production_source}"
        );
        assert!(
            !production_source.contains("WKWebViewConfiguration"),
            "{production_source}"
        );
        assert!(
            !production_source.contains("objc2_web_kit"),
            "{production_source}"
        );
    }

    #[test]
    fn watch_viewer_script_plays_real_video_and_signals_only_on_main_video_ended() {
        let script = official_watch_assist_script();

        assert!(script.contains("querySelectorAll('video')"), "{script}");
        assert!(script.contains("video.play()"), "{script}");
        assert!(
            script.contains("video.addEventListener('ended'"),
            "{script}"
        );
        assert!(script.contains("SHORT_INTRO_MAX_SECONDS = 10"), "{script}");
        assert!(script.contains("shouldEmitEnded = true"), "{script}");
        assert!(script.contains("isLikelyMainVideo(video)"), "{script}");
        assert!(script.contains("short-video-ended"), "{script}");
        assert!(script.contains("ssu-watch://ended"), "{script}");
        assert!(!script.contains("progress"), "{script}");
    }

    #[test]
    fn manual_official_viewer_stays_minimal_without_assist_or_debug_hooks() {
        let source = include_str!("lib.rs");
        let start = source
            .find("fn open_official_viewer_with_session(")
            .expect("manual viewer function exists");
        let tail = &source[start..];
        let end = tail
            .find("\n}\n\n#[tauri::command]")
            .expect("manual viewer function boundary exists");
        let body = &tail[..end];

        assert!(body.contains("official_viewer_bootstrap_url()"), "{body}");
        assert!(body.contains("window.navigate(parsed)"), "{body}");
        assert!(!body.contains("initialization_script"), "{body}");
        assert!(!body.contains("on_navigation"), "{body}");
        assert!(!body.contains("official_watch_assist_script"), "{body}");
        assert!(
            !body.contains("official_viewer_autoplay_configuration"),
            "{body}"
        );
        assert!(!body.contains("ssu-watch://debug"), "{body}");
    }

    #[test]
    fn official_watch_url_validation_rejects_non_https_and_unrelated_hosts() {
        assert!(validate_official_watch_url("https://commons.ssu.ac.kr/viewer").is_ok());
        assert!(
            validate_official_watch_url("https://ssu-toast.commonscdn.com/contents/media.mp4")
                .is_ok()
        );
        assert!(validate_official_watch_url("http://commons.ssu.ac.kr/viewer").is_err());
        assert!(validate_official_watch_url("https://example.com/viewer").is_err());
    }
}
