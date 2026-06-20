use std::sync::{Arc, Mutex};
use tauri::Manager;

// Holds a handle to the Python bridge process (production sidecar mode)
struct BridgeProcess(Arc<Mutex<Option<std::process::Child>>>);

#[tauri::command]
fn get_platform() -> String {
    std::env::consts::OS.to_string()
}

#[tauri::command]
async fn open_folder_dialog(app: tauri::AppHandle) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    app.dialog()
        .file()
        .blocking_pick_folder()
        .and_then(|p| {
            use tauri_plugin_dialog::FilePath;
            match p {
                FilePath::Path(buf) => Some(buf.to_string_lossy().into_owned()),
                _ => None,
            }
        })
}

/// In dev mode the Python server is started manually.
/// In production the sidecar binary is bundled and started here.
fn maybe_start_sidecar() -> Option<std::process::Child> {
    // Check if the bridge is already running
    if is_bridge_alive() {
        return None;
    }

    // Try to start via Python (dev/installed path)
    let python = find_python()?;
    let child = std::process::Command::new(&python)
        .args(["-m", "kryth.desktop_main"])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .ok()?;

    Some(child)
}

fn find_python() -> Option<String> {
    for candidate in ["python", "python3", "py"] {
        if std::process::Command::new(candidate)
            .arg("--version")
            .output()
            .is_ok()
        {
            return Some(candidate.to_string());
        }
    }
    None
}

fn is_bridge_alive() -> bool {
    // Try a quick TCP connect to 127.0.0.1:7765
    std::net::TcpStream::connect_timeout(
        &"127.0.0.1:7765".parse().unwrap(),
        std::time::Duration::from_millis(200),
    )
    .is_ok()
}

fn wait_for_bridge(timeout_ms: u64) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed().as_millis() < timeout_ms as u128 {
        if is_bridge_alive() {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    false
}

pub fn run() {
    let bridge_proc: BridgeProcess = BridgeProcess(Arc::new(Mutex::new(None)));

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(bridge_proc)
        .setup(|app| {
            let proc_state = app.state::<BridgeProcess>();

            // Spawn Python bridge if not already running
            if !is_bridge_alive() {
                if let Some(child) = maybe_start_sidecar() {
                    *proc_state.0.lock().unwrap() = Some(child);
                    // Wait up to 10 s for bridge to accept connections
                    if !wait_for_bridge(10_000) {
                        eprintln!("[KRYTH] Warning: bridge did not start in time");
                    }
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                // Kill the Python process when the window closes
                if let Some(proc_state) = window.try_state::<BridgeProcess>() {
                    if let Ok(mut guard) = proc_state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![get_platform, open_folder_dialog])
        .run(tauri::generate_context!())
        .expect("error while running KRYTH Desktop");
}
