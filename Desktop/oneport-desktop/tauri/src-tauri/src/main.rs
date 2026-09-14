// OnePort Desktop — native backend.
//
// This is a 1:1 port of the verified Python engine (oneport_mcp/gates.py). It
// does NOT reimplement any check: it drives the same installed OnePort CLIs a
// human would run, in their JSON modes, and normalises the results. The two
// #[tauri::command]s below are what the cockpit UI (ui/index.html) calls via
// window.__TAURI__.core.invoke(...) — the exact same UI the web server serves.
//
// Exit-code contract shared by the whole suite: 0 = pass, 1 = findings that
// block, anything else = usage/config error. A missing CLI or a missing login
// is a visible SKIP with the reason — never a fake pass.

// Hide the extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use serde::Serialize;
use wait_timeout::ChildExt;

struct Gate {
    id: &'static str,
    title: &'static str,
    sub: &'static str,
    pip: &'static str,
    exe: &'static str,
    args: &'static [&'static str], // {path} / {base} placeholders
    run_in_path: bool,             // git-based tools run with cwd=path, no path arg
    metered: bool,                 // needs OnePort login (AI judgment draws tokens)
    timeout_secs: u64,
}

// Mirrors GATES in oneport_mcp/gates.py exactly (ids, titles, args, flags).
const GATES: &[Gate] = &[
    Gate {
        id: "secrets",
        title: "Secret & credential scan",
        sub: "working tree + git history",
        pip: "oneport-secrets",
        exe: "oneport-secrets",
        args: &["scan", "{path}", "-f", "json", "--no-triage"],
        run_in_path: false,
        metered: false,
        timeout_secs: 180,
    },
    Gate {
        id: "dependencies",
        title: "Dependency CVEs (OSV)",
        sub: "manifests vs OSV.dev",
        pip: "oneport-depcheck",
        exe: "oneport-depcheck",
        args: &["scan", "{path}", "-f", "json", "--no-llm"],
        run_in_path: false,
        metered: false,
        timeout_secs: 180,
    },
    Gate {
        id: "migrations",
        title: "Migration safety",
        sub: "Django / Alembic / SQL",
        pip: "oneport-migrate",
        exe: "oneport-migrate",
        args: &["check", "{path}", "-f", "json", "--no-llm"],
        run_in_path: false,
        metered: false,
        timeout_secs: 180,
    },
    Gate {
        id: "api-breaks",
        title: "API breaking changes",
        sub: "public surface vs base branch",
        pip: "oneport-apidiff",
        exe: "oneport-apidiff",
        args: &["check", "--base", "{base}", "-f", "json", "--no-llm"],
        run_in_path: true,
        metered: false,
        timeout_secs: 180,
    },
    Gate {
        id: "test-gaps",
        title: "Test coverage gaps",
        sub: "changed lines · needs login",
        pip: "oneport-testgap",
        exe: "oneport-testgap",
        args: &["analyze", "--head", "-f", "json"],
        run_in_path: true,
        metered: true,
        timeout_secs: 300,
    },
    Gate {
        id: "review",
        title: "AI code review",
        sub: "last commit · needs login",
        pip: "oneport-review",
        exe: "oneport-review",
        args: &["review", "--head", "-f", "json"],
        run_in_path: true,
        metered: true,
        timeout_secs: 300,
    },
];

#[derive(Serialize)]
struct GateInfo {
    id: String,
    title: String,
    sub: String,
    installed: bool,
    metered: bool,
}

#[derive(Serialize)]
struct StatusPayload {
    logged_in: bool,
    gates: Vec<GateInfo>,
}

#[derive(Serialize)]
struct GateResult {
    gate: String,
    title: String,
    status: String, // pass | block | skip | error
    summary: String,
}

/// Resolve a CLI to a real path via PATH. On the shipped machine the OnePort
/// tools are installed on PATH (uv / pipx / pip), so PATH resolution is correct.
fn resolve(exe: &str) -> Option<PathBuf> {
    which::which(exe).ok()
}

fn is_installed(gate: &Gate) -> bool {
    resolve(gate.exe).is_some()
}

/// Logged in == ~/.oneport/credentials.json exists with a non-empty token.
/// Mirrors oneport_account.is_logged_in() (credentials.load() is not None).
fn logged_in() -> bool {
    let home = std::env::var("USERPROFILE")
        .ok()
        .or_else(|| std::env::var("HOME").ok())
        .unwrap_or_default();
    if home.is_empty() {
        return false;
    }
    let path = Path::new(&home).join(".oneport").join("credentials.json");
    let txt = match std::fs::read_to_string(&path) {
        Ok(t) => t,
        Err(_) => return false,
    };
    match serde_json::from_str::<serde_json::Value>(&txt) {
        Ok(v) => v
            .get("token")
            .and_then(|t| t.as_str())
            .map(|s| !s.is_empty())
            .unwrap_or(false),
        Err(_) => false,
    }
}

fn truncate(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// One honest human line from a tool's JSON output. Mirrors _summarize().
fn summarize(data: &serde_json::Value) -> String {
    if !data.is_object() {
        return String::new();
    }
    if data
        .get("skipped")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        let reason = data.get("reason").and_then(|v| v.as_str()).unwrap_or("");
        return truncate(&format!("skipped: {}", reason), 140);
    }
    for key in ["findings", "issues", "gaps"] {
        if let Some(arr) = data.get(key).and_then(|v| v.as_array()) {
            return if arr.is_empty() {
                "clean".into()
            } else {
                format!("{} finding(s)", arr.len())
            };
        }
    }
    if let Some(summary) = data.get("summary").and_then(|v| v.as_object()) {
        let total = summary
            .get("total")
            .or_else(|| summary.get("findings"))
            .and_then(|v| v.as_i64());
        if let Some(total) = total {
            return if total == 0 {
                "clean".into()
            } else {
                format!("{} finding(s)", total)
            };
        }
    }
    String::new()
}

#[tauri::command]
fn list_gates_status() -> StatusPayload {
    StatusPayload {
        logged_in: logged_in(),
        gates: GATES
            .iter()
            .map(|g| GateInfo {
                id: g.id.into(),
                title: g.title.into(),
                sub: g.sub.into(),
                installed: is_installed(g),
                metered: g.metered,
            })
            .collect(),
    }
}

#[tauri::command]
fn run_gate(id: String, path: String, base: String) -> GateResult {
    let gate = match GATES.iter().find(|g| g.id == id) {
        Some(g) => g,
        None => {
            return GateResult {
                gate: id.clone(),
                title: id,
                status: "error".into(),
                summary: "unknown gate".into(),
            }
        }
    };

    let mut res = GateResult {
        gate: gate.id.into(),
        title: gate.title.into(),
        status: "skip".into(),
        summary: String::new(),
    };

    let exe_path = match resolve(gate.exe) {
        Some(p) => p,
        None => {
            res.summary = format!("not installed — pip install {}", gate.pip);
            return res;
        }
    };
    if gate.metered && !logged_in() {
        res.summary =
            "needs OnePort login (AI judgment) — run: oneport-account login <token>".into();
        return res;
    }
    if !Path::new(&path).exists() {
        res.status = "error".into();
        res.summary = format!("path not found: {}", path);
        return res;
    }

    // Explicit placeholder substitution (never format!/{} on tool args — an arg
    // may legitimately contain a literal brace).
    let args: Vec<String> = gate
        .args
        .iter()
        .map(|a| a.replace("{path}", &path).replace("{base}", &base))
        .collect();

    let mut cmd = Command::new(&exe_path);
    cmd.args(&args).stdout(Stdio::piped()).stderr(Stdio::piped());
    if gate.run_in_path {
        cmd.current_dir(&path);
    }

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            res.status = "error".into();
            res.summary = format!("could not run: {}", e);
            return res;
        }
    };

    // Drain stdout/stderr on threads so a chatty tool can't fill a pipe buffer
    // and deadlock while we wait on the timeout.
    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();
    let out_handle = std::thread::spawn(move || {
        let mut s = String::new();
        if let Some(p) = stdout_pipe.as_mut() {
            let _ = p.read_to_string(&mut s);
        }
        s
    });
    let err_handle = std::thread::spawn(move || {
        let mut s = String::new();
        if let Some(p) = stderr_pipe.as_mut() {
            let _ = p.read_to_string(&mut s);
        }
        s
    });

    let status = match child.wait_timeout(Duration::from_secs(gate.timeout_secs)) {
        Ok(Some(s)) => s,
        Ok(None) => {
            let _ = child.kill();
            let _ = child.wait();
            let _ = out_handle.join();
            let _ = err_handle.join();
            res.status = "error".into();
            res.summary = format!("timed out after {}s", gate.timeout_secs);
            return res;
        }
        Err(e) => {
            let _ = out_handle.join();
            let _ = err_handle.join();
            res.status = "error".into();
            res.summary = format!("wait failed: {}", e);
            return res;
        }
    };

    let out = out_handle.join().unwrap_or_default();
    let err = err_handle.join().unwrap_or_default();
    let out = out.trim().to_string();
    let err_tail = err.trim().lines().last().unwrap_or("").to_string();

    // Parse the first JSON object out of stdout (tools may print a banner first).
    let data: Option<serde_json::Value> = out
        .find('{')
        .and_then(|i| serde_json::from_str(&out[i..]).ok());

    let code = status.code();
    if code == Some(0) {
        res.status = "pass".into();
        res.summary = data
            .as_ref()
            .map(summarize)
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "clean".into());
    } else if code == Some(1) && data.is_some() {
        res.status = "block".into();
        res.summary = data
            .as_ref()
            .map(summarize)
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "blocking findings".into());
    } else if code == Some(1) {
        // Exit 1 with no parseable findings = the tool failed, not "findings".
        res.status = "error".into();
        res.summary = if !err_tail.is_empty() {
            truncate(&err_tail, 200)
        } else {
            "failed with no output".into()
        };
    } else {
        res.status = "error".into();
        res.summary = if !err_tail.is_empty() {
            truncate(&err_tail, 200)
        } else if !out.is_empty() {
            truncate(&out, 200)
        } else {
            format!("exit {:?}", code)
        };
    }
    res
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![list_gates_status, run_gate])
        .run(tauri::generate_context!())
        .expect("error while running OnePort desktop");
}
