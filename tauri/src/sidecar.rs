use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use once_cell::sync::Lazy;

static VOICE_CHILD: Lazy<Mutex<Option<Child>>> = Lazy::new(|| Mutex::new(None));

/// Repo root: parent of `tauri/` when running `cargo run` from workspace, or CWD in release.
pub fn repo_root() -> PathBuf {
    if let Ok(cwd) = std::env::current_dir() {
        if cwd.join("sidecar").join("src").exists() {
            return cwd;
        }
        if cwd.file_name().map(|n| n == "tauri").unwrap_or(false) {
            if let Some(parent) = cwd.parent() {
                return parent.to_path_buf();
            }
        }
    }
    PathBuf::from(r"C:\Users\frank\starlight-voice")
}

pub fn sidecar_src_path() -> Result<PathBuf, String> {
    Ok(repo_root().join("sidecar").join("src"))
}

pub fn python_exe() -> PathBuf {
    let venv = repo_root().join(".venv").join("Scripts").join("python.exe");
    if venv.exists() {
        return venv;
    }
    PathBuf::from("python")
}

fn sidecar_env() -> Result<(PathBuf, String), String> {
    let pythonpath = sidecar_src_path()?;
    Ok((python_exe(), pythonpath.to_string_lossy().to_string()))
}

pub fn health_json() -> Result<String, String> {
    let (python, pythonpath) = sidecar_env()?;
    let output = Command::new(&python)
        .args(["-m", "starlight_voice", "health"])
        .env("PYTHONPATH", pythonpath)
        .output()
        .map_err(|err| format!("failed to start Python sidecar: {err}"))?;

    if !output.status.success() {
        return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
    }

    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

pub fn run_doctor() -> Result<String, String> {
    let (python, pythonpath) = sidecar_env()?;
    let output = Command::new(&python)
        .args(["-m", "starlight_voice", "doctor"])
        .env("PYTHONPATH", pythonpath)
        .output()
        .map_err(|err| format!("doctor failed to start: {err}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() && stdout.trim().is_empty() {
        return Err(stderr.trim().to_string());
    }
    Ok(format!("{stdout}{stderr}"))
}

pub fn open_dashboard() -> Result<(), String> {
    let root = repo_root();
    let script = root.join("scripts").join("open-dashboard.ps1");
    if script.exists() {
        Command::new("pwsh")
            .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script.to_string_lossy().as_ref()])
            .spawn()
            .map_err(|e| e.to_string())?;
        return Ok(());
    }
    Command::new("pwsh")
        .args([
            "-NoProfile",
            "-Command",
            &format!(
                "Start-Process 'http://127.0.0.1:8765/dashboard/cockpit.html'; Set-Location '{}'; $env:PYTHONPATH='sidecar/src'; Start-Process -WindowStyle Hidden python -ArgumentList '-m','dashboard.server'",
                root.display()
            ),
        ])
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

pub fn run_morning_brief() -> Result<(), String> {
    let (python, pythonpath) = sidecar_env()?;
    let repos = std::env::var("STARLIGHT_BRIEF_REPOS").unwrap_or_else(|_| {
        "C:\\Users\\frank\\FrankX,C:\\Users\\frank\\Starlight-Intelligence-System,C:\\Users\\frank\\starlight-voice,C:\\Users\\frank\\Arcanea".to_string()
    });
    Command::new(&python)
        .args(["-m", "starlight_voice", "brief", "--speak"])
        .env("PYTHONPATH", pythonpath)
        .env("STARLIGHT_BRIEF_REPOS", repos)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Toggle push-to-talk voice loop: start on press if idle, stop if running.
pub fn toggle_voice_loop() -> Result<String, String> {
    let mut guard = VOICE_CHILD
        .lock()
        .map_err(|_| "voice child lock poisoned".to_string())?;

    if let Some(child) = guard.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            let _ = child.kill();
            let _ = child.wait();
            *guard = None;
            return Ok("voice loop stopped".to_string());
        }
        *guard = None;
    }

    let (python, pythonpath) = sidecar_env()?;
    let child = Command::new(&python)
        .args(["-m", "starlight_voice", "voice", "--run"])
        .env("PYTHONPATH", pythonpath)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("failed to spawn voice loop: {e}"))?;
    *guard = Some(child);
    Ok("voice loop started (Ctrl+Shift+Space again to stop)".to_string())
}

pub fn stop_voice_loop() {
    if let Ok(mut guard) = VOICE_CHILD.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}