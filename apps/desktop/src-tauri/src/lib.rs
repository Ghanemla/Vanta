use serde::{Deserialize, Serialize};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager, RunEvent, State};
use uuid::Uuid;

const SIDECAR_FILE: &str = "vanta-orchestrator-x86_64-pc-windows-msvc.exe";
const HEALTH_ATTEMPTS: u32 = 12;

#[derive(Debug, Clone, Serialize)]
struct ServiceInfo {
    state: String,
    phase: String,
    base_url: Option<String>,
    launch_token: Option<String>,
    sidecar_path: Option<String>,
    application_data_path: String,
    database_path: String,
    logs_path: String,
    port: Option<u16>,
    health_check_state: String,
    last_process_exit_code: Option<i32>,
    last_sanitized_error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct StorageInfo {
    current_root: String,
    default_root: String,
    bootstrap_config: String,
    current_bytes: u64,
    current_files: u64,
    destination_free_bytes: u64,
    redirected_target: Option<String>,
    operation: String,
    phase: String,
    destination: Option<String>,
    current_file: Option<String>,
    copied_bytes: u64,
    total_bytes: u64,
    copied_files: u64,
    total_files: u64,
    elapsed_seconds: u64,
    eta_seconds: Option<u64>,
    can_cancel: bool,
    last_error: Option<String>,
    previous_root: Option<String>,
    default_export_folder: Option<String>,
}

#[derive(Debug, Default, Deserialize, Serialize)]
struct StorageBootstrap {
    studio_data_root: Option<PathBuf>,
    default_export_folder: Option<PathBuf>,
}

#[derive(Deserialize)]
struct NativeMediaLocation {
    path: String,
    mime_type: String,
}

struct StorageOperation {
    state: String,
    phase: String,
    destination: Option<PathBuf>,
    current_file: Option<String>,
    copied_bytes: u64,
    total_bytes: u64,
    copied_files: u64,
    total_files: u64,
    started_at: Option<Instant>,
    cancel_requested: bool,
    last_error: Option<String>,
    previous_root: Option<PathBuf>,
}

impl Default for StorageOperation {
    fn default() -> Self {
        Self {
            state: "idle".into(),
            phase: "Studio data is ready".into(),
            destination: None,
            current_file: None,
            copied_bytes: 0,
            total_bytes: 0,
            copied_files: 0,
            total_files: 0,
            started_at: None,
            cancel_requested: false,
            last_error: None,
            previous_root: None,
        }
    }
}

struct RuntimeInner {
    state: String,
    phase: String,
    port: Option<u16>,
    token: Option<String>,
    sidecar_path: Option<PathBuf>,
    data_dir: PathBuf,
    default_data_dir: PathBuf,
    bootstrap_path: PathBuf,
    logs_dir: PathBuf,
    desktop_capability: String,
    storage: StorageOperation,
    child: Option<Child>,
    job: Option<ProcessJob>,
    health_check_state: String,
    last_process_exit_code: Option<i32>,
    last_sanitized_error: Option<String>,
    restart_count: u8,
}

#[derive(Clone)]
struct RuntimeManager {
    inner: Arc<Mutex<RuntimeInner>>,
}

struct DesktopLock {
    path: PathBuf,
    _file: File,
}

/// Owns the Windows Job Object that ties the local sidecar to this desktop process.
/// Closing the last job handle terminates assigned work, including after force quit.
#[cfg(windows)]
struct ProcessJob {
    handle: windows_sys::Win32::Foundation::HANDLE,
}

#[cfg(windows)]
unsafe impl Send for ProcessJob {}

#[cfg(windows)]
impl Drop for ProcessJob {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.handle);
        }
    }
}

#[cfg(not(windows))]
struct ProcessJob;

impl RuntimeManager {
    fn new(
        data_dir: PathBuf,
        default_data_dir: PathBuf,
        bootstrap_path: PathBuf,
        logs_dir: PathBuf,
    ) -> Self {
        Self {
            inner: Arc::new(Mutex::new(RuntimeInner {
                state: "not_started".into(),
                phase: "Preparing local workspace".into(),
                port: None,
                token: None,
                sidecar_path: None,
                data_dir,
                default_data_dir,
                bootstrap_path,
                logs_dir,
                desktop_capability: Uuid::new_v4().simple().to_string()
                    + &Uuid::new_v4().simple().to_string(),
                storage: StorageOperation::default(),
                child: None,
                job: None,
                health_check_state: "not_started".into(),
                last_process_exit_code: None,
                last_sanitized_error: None,
                restart_count: 0,
            })),
        }
    }

    fn snapshot(&self) -> ServiceInfo {
        let inner = self.inner.lock().expect("runtime state lock");
        ServiceInfo {
            state: inner.state.clone(),
            phase: inner.phase.clone(),
            base_url: inner.port.map(|port| format!("http://127.0.0.1:{port}")),
            launch_token: (inner.state == "ready")
                .then(|| inner.token.clone())
                .flatten(),
            sidecar_path: inner
                .sidecar_path
                .as_ref()
                .map(|path| path.display().to_string()),
            application_data_path: inner.data_dir.display().to_string(),
            database_path: inner.data_dir.join("vanta.db").display().to_string(),
            logs_path: inner.logs_dir.display().to_string(),
            port: inner.port,
            health_check_state: inner.health_check_state.clone(),
            last_process_exit_code: inner.last_process_exit_code,
            last_sanitized_error: inner.last_sanitized_error.clone(),
        }
    }

    fn storage_snapshot(&self) -> StorageInfo {
        let inner = self.inner.lock().expect("runtime state lock");
        let (files, bytes) = tree_totals(&inner.data_dir).unwrap_or((0, 0));
        let elapsed = inner
            .storage
            .started_at
            .map(|start| start.elapsed().as_secs())
            .unwrap_or(0);
        let eta = if inner.storage.copied_bytes > 0
            && inner.storage.total_bytes > inner.storage.copied_bytes
            && elapsed > 0
        {
            Some(
                ((inner.storage.total_bytes - inner.storage.copied_bytes) as f64
                    / (inner.storage.copied_bytes as f64 / elapsed as f64))
                    .ceil() as u64,
            )
        } else {
            None
        };
        StorageInfo {
            current_root: inner.data_dir.display().to_string(),
            default_root: inner.default_data_dir.display().to_string(),
            bootstrap_config: inner.bootstrap_path.display().to_string(),
            current_bytes: bytes,
            current_files: files,
            destination_free_bytes: free_bytes(&inner.data_dir).unwrap_or(0),
            redirected_target: redirected_target(&inner.default_data_dir)
                .map(|path| path.display().to_string()),
            operation: inner.storage.state.clone(),
            phase: inner.storage.phase.clone(),
            destination: inner
                .storage
                .destination
                .as_ref()
                .map(|path| path.display().to_string()),
            current_file: inner.storage.current_file.clone(),
            copied_bytes: inner.storage.copied_bytes,
            total_bytes: inner.storage.total_bytes,
            copied_files: inner.storage.copied_files,
            total_files: inner.storage.total_files,
            elapsed_seconds: elapsed,
            eta_seconds: eta,
            can_cancel: matches!(inner.storage.state.as_str(), "scanning" | "copying"),
            last_error: inner.storage.last_error.clone(),
            previous_root: inner
                .storage
                .previous_root
                .as_ref()
                .map(|path| path.display().to_string()),
            default_export_folder: read_bootstrap(&inner.bootstrap_path)
                .default_export_folder
                .map(|path| path.display().to_string()),
        }
    }
}

fn allocate_loopback_port() -> Result<u16, String> {
    TcpListener::bind("127.0.0.1:0")
        .map_err(|error| format!("Unable to reserve a loopback port: {error}"))?
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("Unable to inspect reserved loopback port: {error}"))
}

fn sanitize_error(message: impl ToString) -> String {
    message
        .to_string()
        .replace('\n', " ")
        .replace('\r', " ")
        .chars()
        .take(300)
        .collect()
}

fn read_bootstrap(path: &Path) -> StorageBootstrap {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<StorageBootstrap>(&text).ok())
        .unwrap_or_default()
}

fn bootstrap_root(default_root: &Path, bootstrap_path: &Path) -> PathBuf {
    let configured = read_bootstrap(bootstrap_path)
        .studio_data_root
        .filter(|path| path.is_absolute());
    configured.unwrap_or_else(|| {
        redirected_target(default_root).unwrap_or_else(|| default_root.to_path_buf())
    })
}

fn write_bootstrap(path: &Path, root: &Path) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "Storage bootstrap has no parent directory".to_string())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("Unable to prepare storage bootstrap: {error}"))?;
    let temporary = path.with_extension("json.pending");
    let mut bootstrap = read_bootstrap(path);
    bootstrap.studio_data_root = Some(root.to_path_buf());
    let content = serde_json::to_vec_pretty(&bootstrap)
        .map_err(|error| format!("Unable to encode storage bootstrap: {error}"))?;
    fs::write(&temporary, content)
        .map_err(|error| format!("Unable to write storage bootstrap: {error}"))?;
    fs::rename(&temporary, path)
        .map_err(|error| format!("Unable to activate storage bootstrap: {error}"))
}

fn is_reparse_point(path: &Path) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        return fs::symlink_metadata(path)
            .map(|metadata| metadata.file_attributes() & 0x400 != 0)
            .unwrap_or(false);
    }
    #[cfg(not(windows))]
    {
        fs::symlink_metadata(path)
            .map(|metadata| metadata.file_type().is_symlink())
            .unwrap_or(false)
    }
}

fn redirected_target(path: &Path) -> Option<PathBuf> {
    is_reparse_point(path)
        .then(|| path.canonicalize().ok())
        .flatten()
        .filter(|target| target != path)
}

fn tree_totals(root: &Path) -> Result<(u64, u64), String> {
    fn visit(path: &Path, files: &mut u64, bytes: &mut u64) -> Result<(), String> {
        if is_reparse_point(path) {
            return Err(format!(
                "Storage contains a junction or link that must be adopted instead of copied: {}",
                path.display()
            ));
        }
        for entry in
            fs::read_dir(path).map_err(|error| format!("Unable to scan storage: {error}"))?
        {
            let entry = entry.map_err(|error| error.to_string())?;
            let child = entry.path();
            let metadata = fs::symlink_metadata(&child).map_err(|error| error.to_string())?;
            if metadata.is_dir() {
                visit(&child, files, bytes)?;
            } else if metadata.is_file() {
                *files += 1;
                *bytes += metadata.len();
            }
        }
        Ok(())
    }
    if !root.exists() {
        return Ok((0, 0));
    }
    let mut files = 0;
    let mut bytes = 0;
    visit(root, &mut files, &mut bytes)?;
    Ok((files, bytes))
}

fn free_bytes(path: &Path) -> Result<u64, String> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
        let mut wide: Vec<u16> = path.as_os_str().encode_wide().collect();
        wide.push(0);
        let mut available = 0u64;
        let ok = unsafe {
            GetDiskFreeSpaceExW(
                wide.as_ptr(),
                &mut available,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        };
        if ok == 0 {
            return Err("Unable to determine free space for this destination".into());
        }
        Ok(available)
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        Ok(0)
    }
}

fn validate_storage_destination(source: &Path, destination: &Path) -> Result<(), String> {
    if !destination.is_absolute() || destination.to_string_lossy().starts_with("\\\\") {
        return Err("Choose a local absolute folder, not a network location".into());
    }
    if destination.starts_with(source) || source.starts_with(destination) {
        return Err(
            "The destination cannot contain the current studio data or contain its parent".into(),
        );
    }
    let source = source
        .canonicalize()
        .unwrap_or_else(|_| source.to_path_buf());
    let destination = destination
        .canonicalize()
        .unwrap_or_else(|_| destination.to_path_buf());
    if source == destination || destination.starts_with(&source) || source.starts_with(&destination)
    {
        return Err(
            "The destination cannot contain the current studio data or contain its parent".into(),
        );
    }
    if destination.exists()
        && (is_reparse_point(&destination)
            || fs::read_dir(&destination)
                .map_err(|error| error.to_string())?
                .next()
                .is_some())
    {
        return Err("The destination already contains data or is redirected. Choose an empty local folder or adopt its existing target.".into());
    }
    Ok(())
}

fn copy_storage_tree(
    manager: &RuntimeManager,
    source: &Path,
    destination: &Path,
) -> Result<(), String> {
    fn copy_dir(manager: &RuntimeManager, source: &Path, destination: &Path) -> Result<(), String> {
        fs::create_dir_all(destination)
            .map_err(|error| format!("Unable to create destination folder: {error}"))?;
        for entry in fs::read_dir(source).map_err(|error| error.to_string())? {
            let entry = entry.map_err(|error| error.to_string())?;
            let from = entry.path();
            let to = destination.join(entry.file_name());
            if is_reparse_point(&from) {
                return Err(format!(
                    "Refusing to follow redirected storage item: {}",
                    from.display()
                ));
            }
            let metadata = entry.metadata().map_err(|error| error.to_string())?;
            if metadata.is_dir() {
                copy_dir(manager, &from, &to)?;
                continue;
            }
            let cancelled = manager
                .inner
                .lock()
                .expect("runtime state lock")
                .storage
                .cancel_requested;
            if cancelled {
                return Err("Storage move cancelled before switching locations".into());
            }
            {
                let mut inner = manager.inner.lock().expect("runtime state lock");
                inner.storage.current_file = Some(from.display().to_string());
            }
            fs::copy(&from, &to)
                .map_err(|error| format!("Unable to copy {}: {error}", from.display()))?;
            let mut inner = manager.inner.lock().expect("runtime state lock");
            inner.storage.copied_files += 1;
            inner.storage.copied_bytes += metadata.len();
        }
        Ok(())
    }
    copy_dir(manager, source, destination)
}

fn sidecar_path(app: &AppHandle) -> Result<PathBuf, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("Unable to resolve application resources: {error}"))?;
    let executable_dir = std::env::current_exe()
        .map_err(|error| format!("Unable to resolve application executable path: {error}"))?
        .parent()
        .ok_or_else(|| "Unable to resolve application executable directory".to_string())?
        .to_path_buf();
    let candidates = [
        resource_dir.join("binaries").join(SIDECAR_FILE),
        resource_dir.join(SIDECAR_FILE),
        executable_dir.join(SIDECAR_FILE),
        executable_dir.join("vanta-orchestrator.exe"),
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("binaries")
            .join(SIDECAR_FILE),
    ];
    candidates
        .into_iter()
        .find(|candidate| candidate.is_file())
        .ok_or_else(|| {
            format!(
                "The packaged local service is missing. Expected {} beneath the application resources.",
                SIDECAR_FILE
            )
        })
}

fn service_is_healthy(port: u16, token: &str) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}")
            .parse()
            .expect("loopback address is valid"),
        Duration::from_millis(400),
    ) else {
        return false;
    };
    let request = format!(
        "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Vanta-Token: {token}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200")
}

fn resolve_native_media(
    manager: &RuntimeManager,
    entity: &str,
    id: &str,
    variant: &str,
) -> Result<PathBuf, String> {
    let (port, token, capability, root) = {
        let inner = manager
            .inner
            .lock()
            .map_err(|_| "Runtime state is unavailable")?;
        (
            inner.port,
            inner.token.clone(),
            inner.desktop_capability.clone(),
            inner.data_dir.clone(),
        )
    };
    let (port, token) = match (port, token) {
        (Some(port), Some(token)) => (port, token),
        _ => return Err("The local Vanta service is not ready".into()),
    };
    let mut stream = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}")
            .parse()
            .map_err(|_| "Invalid local service address")?,
        Duration::from_secs(2),
    )
    .map_err(|_| "Unable to reach the local Vanta service")?;
    let request = format!("GET /api/native-media/{}/{}/{} HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Vanta-Token: {}\r\nX-Vanta-Desktop-Capability: {}\r\nConnection: close\r\n\r\n", entity, id, variant, token, capability);
    stream
        .write_all(request.as_bytes())
        .map_err(|error| error.to_string())?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| error.to_string())?;
    let (head, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| "Invalid native media response".to_string())?;
    if !head.starts_with("HTTP/1.1 200") {
        return Err(
            "The selected managed media file is unavailable. Run Repair media from Settings."
                .into(),
        );
    }
    let location: NativeMediaLocation =
        serde_json::from_str(body).map_err(|_| "Invalid native media response")?;
    let path = PathBuf::from(location.path);
    if !path.is_file() || !path.starts_with(&root) {
        return Err("The requested file is not inside the current Vanta storage root".into());
    }
    let _ = location.mime_type;
    Ok(path)
}

fn health_backoff(attempt: u32) -> Duration {
    Duration::from_millis((250_u64 * 2_u64.pow(attempt.min(3))).min(2_000))
}

#[cfg(windows)]
fn hide_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x08000000);
}

#[cfg(not(windows))]
fn hide_console(_command: &mut Command) {}

#[cfg(windows)]
fn own_sidecar_with_job(child: &Child) -> Result<ProcessJob, String> {
    use std::{mem::size_of, os::windows::io::AsRawHandle, ptr};
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
    };

    unsafe {
        let handle = CreateJobObjectW(ptr::null(), ptr::null());
        if handle.is_null() {
            return Err("Unable to create the Windows sidecar ownership job".into());
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const _,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
        {
            CloseHandle(handle);
            return Err("Unable to configure the Windows sidecar ownership job".into());
        }
        if AssignProcessToJobObject(handle, child.as_raw_handle() as _) == 0 {
            CloseHandle(handle);
            return Err("Unable to assign the local service to the Windows ownership job".into());
        }
        Ok(ProcessJob { handle })
    }
}

#[cfg(not(windows))]
fn own_sidecar_with_job(_child: &Child) -> Result<ProcessJob, String> {
    Ok(ProcessJob)
}

fn write_runtime_log(logs_dir: &Path, message: &str) {
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(logs_dir.join("desktop-runtime.log"))
    {
        let _ = writeln!(file, "{message}");
    }
}

fn launch_sidecar(app: AppHandle, manager: &RuntimeManager, restart: bool) -> Result<(), String> {
    let (data_dir, logs_dir, desktop_capability) = {
        let mut inner = manager.inner.lock().expect("runtime state lock");
        inner.state = if restart { "restarting" } else { "preparing" }.into();
        inner.phase = "Preparing local workspace".into();
        inner.health_check_state = "pending".into();
        inner.last_sanitized_error = None;
        (
            inner.data_dir.clone(),
            inner.logs_dir.clone(),
            inner.desktop_capability.clone(),
        )
    };
    fs::create_dir_all(&data_dir)
        .and_then(|_| fs::create_dir_all(&logs_dir))
        .map_err(|error| format!("Unable to prepare Vanta application data: {error}"))?;
    {
        let mut inner = manager.inner.lock().expect("runtime state lock");
        inner.phase = "Opening database".into();
    }
    let executable = sidecar_path(&app)?;
    let port = allocate_loopback_port()?;
    let token = Uuid::new_v4().simple().to_string() + &Uuid::new_v4().simple().to_string();
    let stdout = File::create(logs_dir.join("orchestrator-stdout.log"))
        .map_err(|error| format!("Unable to create sidecar stdout log: {error}"))?;
    let stderr = File::create(logs_dir.join("orchestrator-stderr.log"))
        .map_err(|error| format!("Unable to create sidecar stderr log: {error}"))?;
    let mut command = Command::new(&executable);
    command
        .arg("--self-test")
        .env("VANTA_HOST", "127.0.0.1")
        .env("VANTA_PORT", port.to_string())
        .env("VANTA_LAUNCH_TOKEN", &token)
        .env("VANTA_DESKTOP_CAPABILITY", &desktop_capability)
        .env(
            "VANTA_RUNTIME_MODE",
            if cfg!(debug_assertions) {
                "development"
            } else {
                "production"
            },
        )
        .env("VANTA_DIAGNOSTICS", "1")
        .env("VANTA_DATA_DIR", &data_dir)
        .env("VANTA_LOGS_DIR", &logs_dir)
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    hide_console(&mut command);
    let test_status = command
        .status()
        .map_err(|error| format!("Unable to run packaged service self-test: {error}"))?;
    if !test_status.success() {
        return Err(format!(
            "The packaged local service self-test failed with exit code {:?}",
            test_status.code()
        ));
    }
    let stdout = File::create(logs_dir.join("orchestrator-stdout.log"))
        .map_err(|error| format!("Unable to create sidecar stdout log: {error}"))?;
    let stderr = File::create(logs_dir.join("orchestrator-stderr.log"))
        .map_err(|error| format!("Unable to create sidecar stderr log: {error}"))?;
    let mut command = Command::new(&executable);
    command
        .env("VANTA_HOST", "127.0.0.1")
        .env("VANTA_PORT", port.to_string())
        .env("VANTA_LAUNCH_TOKEN", &token)
        .env("VANTA_DESKTOP_CAPABILITY", &desktop_capability)
        .env(
            "VANTA_RUNTIME_MODE",
            if cfg!(debug_assertions) {
                "development"
            } else {
                "production"
            },
        )
        .env("VANTA_DIAGNOSTICS", "1")
        .env("VANTA_DATA_DIR", &data_dir)
        .env("VANTA_LOGS_DIR", &logs_dir)
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    hide_console(&mut command);
    let mut child = command
        .spawn()
        .map_err(|error| format!("Unable to launch packaged local service: {error}"))?;
    let job = match own_sidecar_with_job(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    {
        let mut inner = manager.inner.lock().expect("runtime state lock");
        inner.state = "starting".into();
        inner.phase = "Starting local service".into();
        inner.port = Some(port);
        inner.token = Some(token);
        inner.sidecar_path = Some(executable.clone());
        inner.child = Some(child);
        inner.job = Some(job);
    }
    write_runtime_log(
        &logs_dir,
        &format!("sidecar launched path={} port={port}", executable.display()),
    );
    let monitor_app = app.clone();
    let monitor_manager = manager.clone();
    thread::spawn(move || supervise_sidecar(monitor_app, monitor_manager));
    Ok(())
}

fn supervise_sidecar(app: AppHandle, manager: RuntimeManager) {
    for attempt in 0..HEALTH_ATTEMPTS {
        let (port, token, logs_dir) = {
            let mut inner = manager.inner.lock().expect("runtime state lock");
            inner.state = "waiting_for_health".into();
            inner.phase = "Verifying local service".into();
            (inner.port, inner.token.clone(), inner.logs_dir.clone())
        };
        if let (Some(port), Some(token)) = (port, token) {
            if service_is_healthy(port, &token) {
                let mut inner = manager.inner.lock().expect("runtime state lock");
                inner.state = "ready".into();
                inner.phase = "Ready".into();
                inner.health_check_state = "ready".into();
                write_runtime_log(&logs_dir, "sidecar health check ready");
                break;
            }
        }
        let exited = {
            let mut inner = manager.inner.lock().expect("runtime state lock");
            inner
                .child
                .as_mut()
                .and_then(|child| child.try_wait().ok())
                .flatten()
        };
        if let Some(status) = exited {
            handle_sidecar_exit(app, &manager, status.code());
            return;
        }
        thread::sleep(health_backoff(attempt));
    }
    {
        let mut inner = manager.inner.lock().expect("runtime state lock");
        if inner.state != "ready" {
            inner.state = "degraded".into();
            inner.phase = "Local service did not become ready".into();
            inner.health_check_state = "timeout".into();
            inner.last_sanitized_error = Some("Timed out while verifying the local service".into());
        }
    }
    for _ in 0..600 {
        thread::sleep(Duration::from_secs(1));
        let status = {
            let mut inner = manager.inner.lock().expect("runtime state lock");
            if inner.state == "stopping" || inner.state == "stopped" {
                return;
            }
            inner
                .child
                .as_mut()
                .and_then(|child| child.try_wait().ok())
                .flatten()
        };
        if let Some(status) = status {
            handle_sidecar_exit(app, &manager, status.code());
            return;
        }
    }
}

fn handle_sidecar_exit(app: AppHandle, manager: &RuntimeManager, exit_code: Option<i32>) {
    let should_restart = {
        let mut inner = manager.inner.lock().expect("runtime state lock");
        if inner.state == "stopping" || inner.state == "stopped" {
            return;
        }
        inner.last_process_exit_code = exit_code;
        inner.child = None;
        inner.job = None;
        if inner.restart_count == 0 {
            inner.restart_count = 1;
            inner.state = "restarting".into();
            inner.phase = "Restarting local service after an unexpected exit".into();
            true
        } else {
            inner.state = "failed".into();
            inner.phase = "Local service stopped unexpectedly".into();
            inner.health_check_state = "failed".into();
            inner.last_sanitized_error = Some(
                "The local service stopped twice. Open diagnostics or repair the runtime.".into(),
            );
            false
        }
    };
    if should_restart {
        if let Err(error) = launch_sidecar(app, manager, true) {
            let mut inner = manager.inner.lock().expect("runtime state lock");
            inner.state = "failed".into();
            inner.last_sanitized_error = Some(sanitize_error(error));
        }
    }
}

fn shutdown_sidecar(manager: &RuntimeManager) {
    let (child, job) = {
        let mut inner = manager.inner.lock().expect("runtime state lock");
        inner.state = "stopping".into();
        inner.phase = "Stopping local service".into();
        (inner.child.take(), inner.job.take())
    };
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
    drop(job);
    let mut inner = manager.inner.lock().expect("runtime state lock");
    inner.state = "stopped".into();
    inner.phase = "Stopped".into();
}

#[tauri::command]
fn service_info(manager: State<'_, RuntimeManager>) -> ServiceInfo {
    manager.snapshot()
}

#[tauri::command]
fn restart_local_service(
    app: AppHandle,
    manager: State<'_, RuntimeManager>,
) -> Result<ServiceInfo, String> {
    shutdown_sidecar(&manager);
    manager
        .inner
        .lock()
        .expect("runtime state lock")
        .restart_count = 0;
    launch_sidecar(app, &manager, true)?;
    Ok(manager.snapshot())
}

#[tauri::command]
fn repair_application_runtime(
    app: AppHandle,
    manager: State<'_, RuntimeManager>,
) -> Result<ServiceInfo, String> {
    if !sidecar_path(&app)?.is_file() {
        return Err(
            "The packaged local service is missing. Reinstall Vanta to repair its runtime.".into(),
        );
    }
    restart_local_service(app, manager)
}

#[tauri::command]
fn storage_info(manager: State<'_, RuntimeManager>) -> StorageInfo {
    manager.storage_snapshot()
}

#[tauri::command]
fn choose_storage_location() -> Option<String> {
    rfd::FileDialog::new()
        .pick_folder()
        .map(|path| path.display().to_string())
}

#[tauri::command]
fn start_storage_move(
    app: AppHandle,
    destination: String,
    manager: State<'_, RuntimeManager>,
) -> Result<StorageInfo, String> {
    let destination = PathBuf::from(destination);
    let source = {
        let mut inner = manager
            .inner
            .lock()
            .map_err(|_| "Storage state is unavailable")?;
        if matches!(
            inner.storage.state.as_str(),
            "scanning" | "copying" | "switching"
        ) {
            return Err("A storage move is already in progress".into());
        }
        validate_storage_destination(&inner.data_dir, &destination)?;
        fs::create_dir_all(&destination)
            .map_err(|error| format!("Unable to create storage destination: {error}"))?;
        let probe = destination.join(".vanta-write-test");
        fs::write(&probe, b"vanta")
            .map_err(|error| format!("The destination is not writable: {error}"))?;
        fs::remove_file(&probe).map_err(|error| error.to_string())?;
        inner.storage = StorageOperation {
            state: "scanning".into(),
            phase: "Scanning studio data before safe copy".into(),
            destination: Some(destination.clone()),
            started_at: Some(Instant::now()),
            previous_root: Some(inner.data_dir.clone()),
            ..StorageOperation::default()
        };
        inner.data_dir.clone()
    };
    let manager_clone = RuntimeManager {
        inner: manager.inner.clone(),
    };
    thread::spawn(move || execute_storage_move(app, manager_clone, source, destination));
    Ok(manager.storage_snapshot())
}

#[tauri::command]
fn cancel_storage_move(manager: State<'_, RuntimeManager>) -> Result<StorageInfo, String> {
    let mut inner = manager
        .inner
        .lock()
        .map_err(|_| "Storage state is unavailable")?;
    if !matches!(inner.storage.state.as_str(), "scanning" | "copying") {
        return Err("There is no cancellable storage move".into());
    }
    inner.storage.cancel_requested = true;
    inner.storage.phase = "Cancelling before storage switch".into();
    drop(inner);
    Ok(manager.storage_snapshot())
}

#[tauri::command]
fn adopt_redirected_storage(manager: State<'_, RuntimeManager>) -> Result<StorageInfo, String> {
    let mut inner = manager
        .inner
        .lock()
        .map_err(|_| "Storage state is unavailable")?;
    let target = redirected_target(&inner.default_data_dir)
        .ok_or_else(|| "No existing redirected default storage was found".to_string())?;
    write_bootstrap(&inner.bootstrap_path, &target)?;
    inner.data_dir = target.clone();
    inner.logs_dir = target.join("logs");
    inner.storage.phase = "Adopted existing redirected storage target".into();
    drop(inner);
    Ok(manager.storage_snapshot())
}

#[tauri::command]
fn set_default_export_folder(
    folder: String,
    manager: State<'_, RuntimeManager>,
) -> Result<StorageInfo, String> {
    let folder = PathBuf::from(folder);
    if !folder.is_absolute() || folder.to_string_lossy().starts_with("\\\\") || !folder.is_dir() {
        return Err("Choose an existing local export folder".into());
    }
    let bootstrap_path = manager
        .inner
        .lock()
        .map_err(|_| "Storage state is unavailable")?
        .bootstrap_path
        .clone();
    let mut bootstrap = read_bootstrap(&bootstrap_path);
    bootstrap.default_export_folder = Some(folder);
    let parent = bootstrap_path
        .parent()
        .ok_or_else(|| "Storage bootstrap has no parent directory".to_string())?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temporary = bootstrap_path.with_extension("json.pending");
    fs::write(
        &temporary,
        serde_json::to_vec_pretty(&bootstrap).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    fs::rename(temporary, bootstrap_path).map_err(|error| error.to_string())?;
    Ok(manager.storage_snapshot())
}

fn execute_storage_move(
    app: AppHandle,
    manager: RuntimeManager,
    source: PathBuf,
    destination: PathBuf,
) {
    let result = (|| -> Result<(), String> {
        let (total_files, total_bytes) = tree_totals(&source)?;
        let free = free_bytes(&destination)?;
        if free < total_bytes {
            return Err(format!("The destination has insufficient free space. Need {total_bytes} bytes, found {free}."));
        }
        {
            let mut inner = manager.inner.lock().expect("runtime state lock");
            inner.storage.state = "copying".into();
            inner.storage.phase = "Copying studio data; the original remains untouched".into();
            inner.storage.total_files = total_files;
            inner.storage.total_bytes = total_bytes;
        }
        shutdown_sidecar(&manager);
        copy_storage_tree(&manager, &source, &destination)?;
        let (copied_files, copied_bytes) = tree_totals(&destination)?;
        if copied_files != total_files || copied_bytes != total_bytes {
            return Err(
                "Copied storage verification failed; the original location was preserved".into(),
            );
        }
        if !destination.join("vanta.db").is_file() {
            return Err("Copied storage is missing its SQLite database; the original location was preserved".into());
        }
        {
            let mut inner = manager.inner.lock().expect("runtime state lock");
            inner.storage.state = "switching".into();
            inner.storage.phase = "Verifying copied storage and restarting local services".into();
            inner.data_dir = destination.clone();
            inner.logs_dir = destination.join("logs");
        }
        launch_sidecar(app.clone(), &manager, true)?;
        let mut ready = false;
        for attempt in 0..HEALTH_ATTEMPTS {
            let (port, token) = {
                let inner = manager.inner.lock().expect("runtime state lock");
                (inner.port, inner.token.clone())
            };
            if let (Some(port), Some(token)) = (port, token) {
                if service_is_healthy(port, &token) {
                    ready = true;
                    break;
                }
            }
            thread::sleep(health_backoff(attempt));
        }
        if !ready {
            return Err("The copied storage did not pass the local service health check".into());
        }
        let bootstrap = manager
            .inner
            .lock()
            .expect("runtime state lock")
            .bootstrap_path
            .clone();
        write_bootstrap(&bootstrap, &destination)?;
        Ok(())
    })();
    if let Err(error) = result {
        shutdown_sidecar(&manager);
        let (previous, app_for_restart) = {
            let mut inner = manager.inner.lock().expect("runtime state lock");
            let previous = inner
                .storage
                .previous_root
                .clone()
                .unwrap_or_else(|| source.clone());
            inner.data_dir = previous.clone();
            inner.logs_dir = previous.join("logs");
            inner.storage.state = if inner.storage.cancel_requested {
                "cancelled".into()
            } else {
                "failed".into()
            };
            inner.storage.phase = "Original studio data remains active".into();
            inner.storage.last_error = Some(sanitize_error(error));
            (previous, app.clone())
        };
        let _ = launch_sidecar(app_for_restart, &manager, true);
        let _ = previous;
    } else {
        let mut inner = manager.inner.lock().expect("runtime state lock");
        inner.storage.state = "completed".into();
        inner.storage.phase =
            "Storage moved and verified. The original remains until you remove it manually.".into();
        inner.storage.current_file = None;
    }
}

#[tauri::command]
fn choose_local_model_file() -> Option<String> {
    rfd::FileDialog::new()
        .add_filter("SafeTensors checkpoints", &["safetensors"])
        .pick_file()
        .map(|path| path.display().to_string())
}

#[tauri::command]
fn choose_local_image_file() -> Option<String> {
    rfd::FileDialog::new()
        .add_filter("Reference images", &["png", "jpg", "jpeg", "webp"])
        .pick_file()
        .map(|path| path.display().to_string())
}

#[tauri::command]
fn choose_local_training_images() -> Vec<String> {
    rfd::FileDialog::new()
        .add_filter("Owned training images", &["png", "jpg", "jpeg", "webp"])
        .pick_files()
        .unwrap_or_default()
        .into_iter()
        .map(|path| path.display().to_string())
        .collect()
}

#[tauri::command]
fn choose_local_video_file() -> Option<String> {
    rfd::FileDialog::new()
        .add_filter("Motion references", &["mp4", "mov", "webm", "mkv"])
        .pick_file()
        .map(|path| path.display().to_string())
}

#[tauri::command]
fn choose_local_lora_file() -> Option<String> {
    rfd::FileDialog::new()
        .add_filter("SafeTensors LoRAs", &["safetensors"])
        .pick_file()
        .map(|path| path.display().to_string())
}

#[tauri::command]
fn choose_local_upscaler_file() -> Option<String> {
    rfd::FileDialog::new()
        .add_filter("Local upscale models", &["pth", "pt"])
        .pick_file()
        .map(|path| path.display().to_string())
}

#[tauri::command]
fn open_local_path(kind: String, manager: State<'_, RuntimeManager>) -> Result<(), String> {
    let inner = manager
        .inner
        .lock()
        .map_err(|_| "Runtime state is unavailable")?;
    let path = match kind.as_str() {
        "data" => inner.data_dir.clone(),
        "models" => inner.data_dir.join("engine").join("models"),
        "logs" => inner.logs_dir.clone(),
        "database" => inner.data_dir.clone(),
        _ => return Err("Unsupported local path".into()),
    };
    fs::create_dir_all(&path).map_err(|error| format!("Unable to open local path: {error}"))?;
    drop(inner);
    let mut command = Command::new("explorer.exe");
    command.arg(&path);
    hide_console(&mut command);
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Unable to open Windows Explorer: {error}"))
}

#[tauri::command]
fn open_managed_media(
    entity: String,
    id: String,
    variant: String,
    manager: State<'_, RuntimeManager>,
) -> Result<(), String> {
    let path = resolve_native_media(&manager, &entity, &id, &variant)?;
    let mut command = Command::new("explorer.exe");
    command.arg(&path);
    hide_console(&mut command);
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Unable to open the selected file: {error}"))
}

#[tauri::command]
fn reveal_managed_media(
    entity: String,
    id: String,
    variant: String,
    manager: State<'_, RuntimeManager>,
) -> Result<(), String> {
    let path = resolve_native_media(&manager, &entity, &id, &variant)?;
    let mut command = Command::new("explorer.exe");
    command.arg("/select,").arg(&path);
    hide_console(&mut command);
    command
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Unable to reveal the selected file: {error}"))
}

#[tauri::command]
fn save_managed_media_copy(
    entity: String,
    id: String,
    variant: String,
    manager: State<'_, RuntimeManager>,
) -> Result<String, String> {
    let source = resolve_native_media(&manager, &entity, &id, &variant)?;
    let extension = source
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("bin");
    let initial = source
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("vanta-media");
    let configured_folder = {
        let inner = manager
            .inner
            .lock()
            .map_err(|_| "Runtime state is unavailable")?;
        read_bootstrap(&inner.bootstrap_path).default_export_folder
    };
    let pictures = configured_folder.or_else(|| {
        std::env::var_os("USERPROFILE")
            .map(PathBuf::from)
            .map(|root| root.join("Pictures"))
    });
    let mut dialog = rfd::FileDialog::new()
        .set_file_name(initial)
        .add_filter("Vanta media", &[extension]);
    if let Some(folder) = pictures.filter(|path| path.is_dir()) {
        dialog = dialog.set_directory(folder);
    }
    let Some(mut destination) = dialog.save_file() else {
        return Err("Export cancelled".into());
    };
    if destination.extension().is_none() {
        destination.set_extension(extension);
    }
    if destination.exists() {
        return Err("Choose a new filename; Vanta will not overwrite an existing export".into());
    }
    fs::copy(&source, &destination).map_err(|error| format!("Unable to save a copy: {error}"))?;
    Ok(destination.display().to_string())
}

#[tauri::command]
fn copy_managed_media_path(
    entity: String,
    id: String,
    variant: String,
    manager: State<'_, RuntimeManager>,
) -> Result<(), String> {
    let path = resolve_native_media(&manager, &entity, &id, &variant)?;
    let mut command = Command::new("cmd.exe");
    command.args(["/C", "clip"]).stdin(Stdio::piped());
    hide_console(&mut command);
    let mut child = command
        .spawn()
        .map_err(|error| format!("Unable to access the clipboard: {error}"))?;
    if let Some(stdin) = child.stdin.as_mut() {
        stdin
            .write_all(path.to_string_lossy().as_bytes())
            .map_err(|error| error.to_string())?;
    }
    child.wait().map_err(|error| error.to_string())?;
    Ok(())
}

fn acquire_desktop_lock(data_dir: &Path) -> Result<DesktopLock, String> {
    let path = data_dir.join("desktop-instance.lock");
    let open_lock = || OpenOptions::new().write(true).create_new(true).open(&path);
    let mut file = match open_lock() {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            let owner_pid = fs::read_to_string(&path)
                .ok()
                .and_then(|value| value.trim().parse::<u32>().ok());
            if owner_pid.is_some_and(process_is_running) {
                return Err("Vanta is already running. Use the existing Vanta window.".to_string());
            }
            fs::remove_file(&path).map_err(|cleanup_error| {
                format!("Unable to recover Vanta's stale instance lock: {cleanup_error}")
            })?;
            open_lock().map_err(|lock_error| {
                format!("Unable to create Vanta's instance lock: {lock_error}")
            })?
        }
        Err(error) => return Err(format!("Unable to create Vanta's instance lock: {error}")),
    };
    writeln!(file, "{}", std::process::id())
        .map_err(|error| format!("Unable to write Vanta's instance lock: {error}"))?;
    Ok(DesktopLock { path, _file: file })
}

#[cfg(windows)]
fn process_is_running(pid: u32) -> bool {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, STILL_ACTIVE},
        System::Threading::{GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION},
    };
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return false;
        }
        let mut exit_code = 0;
        let active =
            GetExitCodeProcess(handle, &mut exit_code) != 0 && exit_code == STILL_ACTIVE as u32;
        CloseHandle(handle);
        active
    }
}

#[cfg(not(windows))]
fn process_is_running(_pid: u32) -> bool {
    false
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .setup(|app| {
            let default_data_dir = app
                .path()
                .app_data_dir()
                .map_err(|error| format!("Unable to resolve Vanta application data: {error}"))?;
            let bootstrap_dir = app
                .path()
                .app_local_data_dir()
                .map_err(|error| format!("Unable to resolve Vanta storage bootstrap: {error}"))?
                .join("storage-bootstrap");
            let bootstrap_path = bootstrap_dir.join("studio-data.json");
            let data_dir = bootstrap_root(&default_data_dir, &bootstrap_path);
            let logs_dir = data_dir.join("logs");
            fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;
            fs::create_dir_all(&bootstrap_dir).map_err(|error| error.to_string())?;
            app.manage(acquire_desktop_lock(&bootstrap_dir)?);
            app.manage(RuntimeManager::new(
                data_dir,
                default_data_dir,
                bootstrap_path,
                logs_dir,
            ));
            let manager = app.state::<RuntimeManager>();
            if let Err(error) = launch_sidecar(app.handle().clone(), &manager, false) {
                let mut inner = manager.inner.lock().expect("runtime state lock");
                inner.state = "failed".into();
                inner.phase = "Unable to start local service".into();
                inner.last_sanitized_error = Some(sanitize_error(error));
                write_runtime_log(
                    &inner.logs_dir,
                    &format!(
                        "sidecar startup failed: {}",
                        inner
                            .last_sanitized_error
                            .as_deref()
                            .unwrap_or("unknown error")
                    ),
                );
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            service_info,
            restart_local_service,
            repair_application_runtime,
            storage_info,
            choose_storage_location,
            start_storage_move,
            cancel_storage_move,
            adopt_redirected_storage,
            set_default_export_folder,
            choose_local_model_file,
            choose_local_image_file,
            choose_local_training_images,
            choose_local_video_file,
            choose_local_lora_file,
            choose_local_upscaler_file,
            open_local_path,
            open_managed_media,
            reveal_managed_media,
            save_managed_media_copy,
            copy_managed_media_path
        ]);

    let app = builder
        .build(tauri::generate_context!())
        .expect("error while building Vanta");
    app.run(|app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. }) {
            let manager = app_handle.state::<RuntimeManager>();
            shutdown_sidecar(&manager);
            let lock = app_handle.state::<DesktopLock>();
            let _ = fs::remove_file(&lock.path);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dynamic_port_allocation_returns_a_loopback_port() {
        assert!(allocate_loopback_port().expect("port") > 0);
    }

    #[test]
    fn health_backoff_is_bounded() {
        assert_eq!(health_backoff(0), Duration::from_millis(250));
        assert_eq!(health_backoff(8), Duration::from_secs(2));
    }

    #[test]
    fn sanitization_removes_newlines_and_bounds_length() {
        let message = format!("secret\n{}", "x".repeat(400));
        assert!(!sanitize_error(message).contains('\n'));
        assert_eq!(sanitize_error("x".repeat(400)).len(), 300);
    }

    #[test]
    fn bootstrap_configuration_preserves_a_selected_storage_root() {
        let root = std::env::temp_dir().join(format!("vanta-storage-test-{}", Uuid::new_v4()));
        let selected = root.join("F-drive-style-data");
        let bootstrap = root.join("bootstrap").join("studio-data.json");
        write_bootstrap(&bootstrap, &selected).expect("bootstrap writes atomically");
        assert_eq!(bootstrap_root(&root.join("default"), &bootstrap), selected);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn storage_totals_count_real_files_and_reject_nested_destinations() {
        let root = std::env::temp_dir().join(format!("vanta-storage-test-{}", Uuid::new_v4()));
        let source = root.join("source");
        fs::create_dir_all(source.join("media")).expect("source directory");
        fs::write(source.join("vanta.db"), b"SQLite format 3\0").expect("database fixture");
        fs::write(source.join("media").join("frame.png"), b"pixels").expect("media fixture");
        assert_eq!(tree_totals(&source).expect("totals"), (2, 22));
        assert!(validate_storage_destination(&source, &source.join("nested")).is_err());
        let _ = fs::remove_dir_all(root);
    }
}
