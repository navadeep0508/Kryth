use std::sync::{Arc, Mutex};
use std::path::{Path, PathBuf};
use serde::Serialize;
use tauri::Manager;

// Holds a handle to the Python bridge process (production sidecar mode)
struct BridgeProcess(Arc<Mutex<Option<std::process::Child>>>);

#[derive(Serialize)]
struct FileEntry {
    name: String,
    path: String,
    is_dir: bool,
    size: u64,
}

#[tauri::command]
fn get_platform() -> String {
    std::env::consts::OS.to_string()
}

#[tauri::command]
async fn open_browser_window(_app: tauri::AppHandle, _url: String) -> Result<(), String> {
    // Browser opens as external Chromium window — no embedding
    Ok(())
}

#[tauri::command]
async fn navigate_browser(_app: tauri::AppHandle, _url: String) -> Result<(), String> {
    Ok(())
}

#[tauri::command]
async fn close_browser_window(_app: tauri::AppHandle) -> Result<(), String> {
    Ok(())
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

#[tauri::command]
async fn list_files(path: String) -> Result<Vec<FileEntry>, String> {
    let dir = PathBuf::from(&path);
    if !dir.is_dir() {
        return Err(format!("Not a directory: {}", path));
    }
    let mut entries = Vec::new();
    let read = std::fs::read_dir(&dir).map_err(|e| e.to_string())?;
    let mut items: Vec<_> = read.filter_map(|e| e.ok()).collect();
    items.sort_by(|a, b| {
        let a_dir = a.file_type().map(|t| t.is_dir()).unwrap_or(false);
        let b_dir = b.file_type().map(|t| t.is_dir()).unwrap_or(false);
        b_dir.cmp(&a_dir).then_with(|| {
            a.file_name().to_ascii_lowercase().cmp(&b.file_name().to_ascii_lowercase())
        })
    });
    for entry in items {
        let meta = entry.metadata().map_err(|e| e.to_string())?;
        entries.push(FileEntry {
            name: entry.file_name().to_string_lossy().into_owned(),
            path: entry.path().to_string_lossy().into_owned(),
            is_dir: meta.is_dir(),
            size: if meta.is_file() { meta.len() } else { 0 },
        });
    }
    Ok(entries)
}

#[tauri::command]
async fn read_file(path: String) -> Result<String, String> {
    let file_path = PathBuf::from(&path);
    if !file_path.is_file() {
        return Err(format!("Not a file: {}", path));
    }
    std::fs::read_to_string(&file_path).map_err(|e| e.to_string())
}

#[tauri::command]
async fn write_file(path: String, content: String) -> Result<(), String> {
    let file_path = PathBuf::from(&path);
    if let Some(parent) = file_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&file_path, &content).map_err(|e| e.to_string())
}

#[tauri::command]
async fn run_shell(command: String, cwd: String) -> Result<String, String> {
    let dir = if cwd.is_empty() { std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")) } else { PathBuf::from(&cwd) };
    let shell = if cfg!(windows) { "cmd" } else { "sh" };
    let flag = if cfg!(windows) { "/C" } else { "-c" };
    let output = std::process::Command::new(shell)
        .arg(flag)
        .arg(&command)
        .current_dir(&dir)
        .output()
        .map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if output.status.success() {
        Ok(stdout.into_owned())
    } else {
        Err(format!("{}{}", stdout, stderr))
    }
}

#[tauri::command]
async fn search_files(dir: String, pattern: String) -> Result<Vec<String>, String> {
    let root = PathBuf::from(&dir);
    if !root.is_dir() {
        return Err(format!("Not a directory: {}", dir));
    }
    let pattern_lower = pattern.to_lowercase();
    let mut results = Vec::new();
    fn walk(path: &Path, pattern: &str, results: &mut Vec<String>, depth: u32) {
        if depth > 10 { return; }
        if let Ok(entries) = std::fs::read_dir(path) {
            for entry in entries.flatten() {
                let entry_path = entry.path();
                let name = entry.file_name().to_string_lossy().to_lowercase();
                // Skip hidden and common large dirs
                if name.starts_with('.') || name == "node_modules" || name == "target" || name == "__pycache__" {
                    continue;
                }
                if name.contains(pattern) {
                    results.push(entry_path.to_string_lossy().into_owned());
                }
                if entry_path.is_dir() && results.len() < 100 {
                    walk(&entry_path, pattern, results, depth + 1);
                }
            }
        }
    }
    walk(&root, &pattern_lower, &mut results, 0);
    results.truncate(100);
    Ok(results)
}

#[tauri::command]
async fn grep_files(dir: String, pattern: String, max_results: u32) -> Result<Vec<String>, String> {
    let root = PathBuf::from(&dir);
    if !root.is_dir() {
        return Err(format!("Not a directory: {}", dir));
    }
    let max = max_results.min(200) as usize;
    let mut results = Vec::new();
    fn walk_grep(path: &Path, pattern: &str, results: &mut Vec<String>, max: usize, depth: u32) {
        if depth > 8 || results.len() >= max { return; }
        if let Ok(entries) = std::fs::read_dir(path) {
            for entry in entries.flatten() {
                if results.len() >= max { return; }
                let entry_path = entry.path();
                let name = entry.file_name().to_string_lossy().to_lowercase();
                if name.starts_with('.') || name == "node_modules" || name == "target" || name == "__pycache__" {
                    continue;
                }
                if entry_path.is_dir() {
                    walk_grep(&entry_path, pattern, results, max, depth + 1);
                } else if entry_path.is_file() {
                    // Only search text files under 1MB
                    if let Ok(meta) = entry_path.metadata() {
                        if meta.len() > 1_000_000 { continue; }
                    }
                    if let Ok(content) = std::fs::read_to_string(&entry_path) {
                        for (i, line) in content.lines().enumerate() {
                            if line.contains(pattern) {
                                results.push(format!("{}:{}:{}", entry_path.to_string_lossy(), i + 1, line.trim()));
                                if results.len() >= max { return; }
                            }
                        }
                    }
                }
            }
        }
    }
    walk_grep(&root, &pattern, &mut results, max, 0);
    Ok(results)
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
        .invoke_handler(tauri::generate_handler![
            get_platform,
            open_folder_dialog,
            open_browser_window,
            navigate_browser,
            close_browser_window,
            list_files,
            read_file,
            write_file,
            run_shell,
            search_files,
            grep_files,
        ])
        .run(tauri::generate_context!())
        .expect("error while running KRYTH Desktop");
}
