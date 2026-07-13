use serde::Serialize;
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::Duration,
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

struct RuntimeInner {
    state: String,
    phase: String,
    port: Option<u16>,
    token: Option<String>,
    sidecar_path: Option<PathBuf>,
    data_dir: PathBuf,
    logs_dir: PathBuf,
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
    fn new(data_dir: PathBuf, logs_dir: PathBuf) -> Self {
        Self {
            inner: Arc::new(Mutex::new(RuntimeInner {
                state: "not_started".into(),
                phase: "Preparing local workspace".into(),
                port: None,
                token: None,
                sidecar_path: None,
                data_dir,
                logs_dir,
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
    let (data_dir, logs_dir) = {
        let mut inner = manager.inner.lock().expect("runtime state lock");
        inner.state = if restart { "restarting" } else { "preparing" }.into();
        inner.phase = "Preparing local workspace".into();
        inner.health_check_state = "pending".into();
        inner.last_sanitized_error = None;
        (inner.data_dir.clone(), inner.logs_dir.clone())
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
fn choose_local_model_file() -> Option<String> {
    rfd::FileDialog::new()
        .add_filter("SafeTensors checkpoints", &["safetensors"])
        .pick_file()
        .map(|path| path.display().to_string())
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
            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|error| format!("Unable to resolve Vanta application data: {error}"))?;
            let logs_dir = data_dir.join("logs");
            fs::create_dir_all(&data_dir).map_err(|error| error.to_string())?;
            app.manage(acquire_desktop_lock(&data_dir)?);
            app.manage(RuntimeManager::new(data_dir, logs_dir));
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
            choose_local_model_file
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
}
