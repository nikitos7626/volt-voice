#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use tao::{
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoopBuilder},
    platform::windows::WindowBuilderExtWindows,
    window::{Icon, WindowBuilder},
};
use wry::{PermissionResponse, WebViewBuilder};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const ICON_32_BYTES: &[u8] = include_bytes!("../icon_32.rgba");
const ICON_64_BYTES: &[u8] = include_bytes!("../icon_64.rgba");
const APP_VERSION: &str = env!("CARGO_PKG_VERSION");
const VOLT_SERVER_URL: &str = "https://panel.v0lt.tech/";

static UPDATE_BUSY: AtomicBool = AtomicBool::new(false);

#[derive(Debug)]
enum UserEvent {
    EvalJs(String),
    ReloadFresh(String),
    RestartNewExe(PathBuf),
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("Ошибка чтения файла: {}", e))?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    let digest = hasher.finalize();
    Ok(digest.iter().map(|b| format!("{:02x}", b)).collect())
}

fn cleanup_old_exe_background() {
    if let Ok(cur) = std::env::current_exe() {
        let old_path = cur.with_extension("exe.old");
        let new_path = cur.with_extension("exe.new");
        if old_path.exists() || new_path.exists() {
            std::thread::spawn(move || {
                for _ in 0..15 {
                    let ok_old = !old_path.exists() || std::fs::remove_file(&old_path).is_ok();
                    let ok_new = !new_path.exists() || std::fs::remove_file(&new_path).is_ok();
                    if ok_old && ok_new {
                        break;
                    }
                    std::thread::sleep(std::time::Duration::from_millis(250));
                }
            });
        }
    }
}

fn js_escape(s: &str) -> String {
    s.replace('\\', "\\\\")
        .replace('\'', "\\'")
        .replace('\n', " ")
        .replace('\r', "")
}

fn get_windows_machine_guid() -> String {
    let mut cmd = Command::new("reg.exe");
    cmd.args([
        "query",
        "HKLM\\SOFTWARE\\Microsoft\\Cryptography",
        "/v",
        "MachineGuid",
    ]);
    #[cfg(windows)]
    cmd.creation_flags(0x08000000);
    if let Ok(out) = cmd.output() {
        if out.status.success() {
            let s = String::from_utf8_lossy(&out.stdout);
            for line in s.lines() {
                if line.contains("MachineGuid") {
                    let parts: Vec<&str> = line.split_whitespace().collect();
                    if let Some(val) = parts.last() {
                        if val.len() >= 16 {
                            return val.trim().to_string();
                        }
                    }
                }
            }
        }
    }
    let comp = std::env::var("COMPUTERNAME").unwrap_or_else(|_| "UNKNOWN_PC".to_string());
    let user = std::env::var("USERNAME").unwrap_or_else(|_| "USER".to_string());
    format!("{}-{}", comp, user)
}

fn compute_hardware_id() -> (String, String) {
    let guid = get_windows_machine_guid();
    let comp = std::env::var("COMPUTERNAME").unwrap_or_else(|_| "Windows_PC".to_string());
    let mut hasher = Sha256::new();
    hasher.update(b"VOLT_HARDWARE_SALT_v1:");
    hasher.update(guid.as_bytes());
    let digest = hasher.finalize();
    let hex_str: String = digest.iter().map(|b| format!("{:02x}", b)).collect();
    let hwid = format!("hw_{}", &hex_str[..12]);
    (hwid, comp)
}

fn send_pct(proxy: &tao::event_loop::EventLoopProxy<UserEvent>, pct: u64, label: &str) {
    let js = format!(
        "window.__voltUpdatePercent && window.__voltUpdatePercent({}, '{}');",
        pct.min(100),
        js_escape(label)
    );
    let _ = proxy.send_event(UserEvent::EvalJs(js));
}

fn perform_self_update(
    url: &str,
    expected_sha: &str,
    expected_size: u64,
    proxy: &tao::event_loop::EventLoopProxy<UserEvent>,
) -> Result<PathBuf, String> {
    if !url.starts_with(VOLT_SERVER_URL) {
        return Err("Недопустимый URL обновления".to_string());
    }
    let cur_exe =
        std::env::current_exe().map_err(|e| format!("Не найден путь к приложению: {}", e))?;
    let new_path = cur_exe.with_extension("exe.new");
    let old_path = cur_exe.with_extension("exe.old");
    let _ = std::fs::remove_file(&new_path);

    let _ = proxy.send_event(UserEvent::EvalJs(
        "window.__voltUpdateProgress && window.__voltUpdateProgress('downloading');".to_string(),
    ));
    send_pct(proxy, 8, "Подключение к серверу...");

    let mut cmd = Command::new("curl.exe");
    cmd.args([
        "-sSLf",
        "--connect-timeout",
        "15",
        "--max-time",
        "120",
        "-o",
    ])
    .arg(&new_path)
    .arg(url);

    #[cfg(windows)]
    cmd.creation_flags(0x08000000);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Не удалось запустить загрузчик: {}", e))?;

    let target_bytes = if expected_size > 150_000 {
        expected_size
    } else {
        750_000
    };

    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break st,
            Ok(None) => {
                let dl = std::fs::metadata(&new_path).map(|m| m.len()).unwrap_or(0);
                let pct = 10 + ((dl.saturating_mul(80)) / target_bytes).min(80);
                let kb = dl / 1024;
                let total_kb = target_bytes / 1024;
                send_pct(proxy, pct, &format!("Загрузка обновления ({} / {} КБ)...", kb, total_kb));
                std::thread::sleep(std::time::Duration::from_millis(80));
            }
            Err(e) => {
                let _ = std::fs::remove_file(&new_path);
                return Err(format!("Ошибка процесса загрузки: {}", e));
            }
        }
    };

    if !status.success() {
        let _ = std::fs::remove_file(&new_path);
        return Err("Не удалось скачать файл обновления с сервера".to_string());
    }

    send_pct(proxy, 93, "Проверка цифровой подписи SHA-256...");

    let bytes = std::fs::read(&new_path)
        .map_err(|e| format!("Ошибка чтения загруженного файла: {}", e))?;
    if bytes.len() < 150_000 || &bytes[0..2] != b"MZ" {
        let _ = std::fs::remove_file(&new_path);
        return Err("Скачанный файл повреждён или не является приложением".to_string());
    }

    if !expected_sha.trim().is_empty() {
        let actual_sha = sha256_file(&new_path)?;
        if actual_sha.to_lowercase() != expected_sha.trim().to_lowercase() {
            let _ = std::fs::remove_file(&new_path);
            return Err("Контрольная сумма SHA-256 не совпала".to_string());
        }
    }

    let _ = proxy.send_event(UserEvent::EvalJs(
        "window.__voltUpdateProgress && window.__voltUpdateProgress('installing');".to_string(),
    ));
    send_pct(proxy, 98, "Установка и перезапуск VOLT...");

    if old_path.exists() {
        let _ = std::fs::remove_file(&old_path);
    }
    std::fs::rename(&cur_exe, &old_path)
        .map_err(|e| format!("Не удалось подготовить замену файла: {}", e))?;

    if let Err(e) = std::fs::rename(&new_path, &cur_exe) {
        let _ = std::fs::rename(&old_path, &cur_exe);
        return Err(format!("Не удалось установить новый файл: {}", e));
    }

    send_pct(proxy, 100, "Готово! Перезапуск...");
    std::thread::sleep(std::time::Duration::from_millis(160));
    Ok(cur_exe)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let extra_args = "--disable-background-timer-throttling --disable-renderer-backgrounding --disable-backgrounding-occluded-windows --disable-features=CalculateNativeWinOcclusion,IntensiveWakeUpThrottling";
    let merged_args = match std::env::var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS") {
        Ok(existing) if !existing.trim().is_empty() => format!("{} {}", existing, extra_args),
        _ => extra_args.to_string(),
    };
    std::env::set_var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", merged_args);

    cleanup_old_exe_background();

    let local_sha = std::env::current_exe()
        .ok()
        .and_then(|p| sha256_file(&p).ok())
        .unwrap_or_default();

    let event_loop = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let proxy = event_loop.create_proxy();

    let small_icon = Icon::from_rgba(ICON_32_BYTES.to_vec(), 32, 32).ok();
    let big_icon = Icon::from_rgba(ICON_64_BYTES.to_vec(), 64, 64).ok();

    let mut builder = WindowBuilder::new()
        .with_title("VOLT")
        .with_inner_size(tao::dpi::LogicalSize::new(1440.0, 900.0))
        .with_min_inner_size(tao::dpi::LogicalSize::new(960.0, 600.0));

    if let Some(icon) = small_icon {
        builder = builder.with_window_icon(Some(icon));
    }
    if let Some(icon) = big_icon {
        builder = builder.with_taskbar_icon(Some(icon));
    }

    let window = builder.build(&event_loop)?;

    let (hwid, dev_name) = compute_hardware_id();
    let init_script = format!(
        "window.__VOLT_DESKTOP__ = true; window.__VOLT_SUPPORTS_RELAUNCH__ = true; window.__VOLT_CLIENT_KIND__ = 'main'; window.__VOLT_EXE_VERSION__ = '{}'; window.__VOLT_EXE_SHA256__ = '{}'; window.__VOLT_HARDWARE_ID__ = '{}'; window.__VOLT_DEVICE_NAME__ = '{}'; if (!window.ipc && window.chrome && window.chrome.webview) {{ window.ipc = {{ postMessage: function(msg) {{ window.chrome.webview.postMessage(msg); }} }}; }}",
        APP_VERSION, local_sha, hwid, js_escape(&dev_name)
    );

    let ipc_proxy = proxy.clone();
    let webview = WebViewBuilder::new()
        .with_url(VOLT_SERVER_URL)
        .with_background_color((0, 0, 0, 255))
        .with_autoplay(true)
        .with_clipboard(true)
        .with_download_started_handler(|_url, path| {
            if let Ok(userprofile) = std::env::var("USERPROFILE") {
                let dl = PathBuf::from(userprofile).join("Downloads");
                if dl.exists() {
                    let fname = path.file_name().map(|n| n.to_os_string()).unwrap_or_default();
                    *path = dl.join(fname);
                }
            }
            true
        })
        .with_permission_handler(|_| PermissionResponse::Allow)
        .with_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 VoltDesktop/1.2")
        .with_initialization_script(&init_script)
        .with_ipc_handler(move |req| {
            let body = req.body().trim().to_string();
            if body == "relaunch" {
                if let Ok(cur_exe) = std::env::current_exe() {
                    let _ = ipc_proxy.send_event(UserEvent::RestartNewExe(cur_exe));
                }
                return;
            }
            if let Some(url) = body.strip_prefix("reload|") {
                if url.starts_with(VOLT_SERVER_URL) {
                    let _ = ipc_proxy.send_event(UserEvent::ReloadFresh(url.to_string()));
                }
                return;
            }
            if let Some(rest) = body.strip_prefix("download_file|") {
                let mut parts = rest.splitn(2, '|');
                let file_url = parts.next().unwrap_or("").to_string();
                let file_name = parts.next().unwrap_or("file.bin").to_string();
                if !file_url.is_empty() {
                    let p_clone = ipc_proxy.clone();
                    std::thread::spawn(move || {
                        if let Ok(userprofile) = std::env::var("USERPROFILE") {
                            let dl_dir = PathBuf::from(userprofile).join("Downloads");
                            if dl_dir.exists() {
                                let dest = dl_dir.join(&file_name);
                                let mut cmd = Command::new("curl.exe");
                                cmd.args(["-sSLf", "--connect-timeout", "15", "-o"])
                                   .arg(&dest)
                                   .arg(&file_url);
                                #[cfg(windows)]
                                cmd.creation_flags(0x08000000);
                                if let Ok(status) = cmd.status() {
                                    if status.success() {
                                        #[cfg(windows)]
                                        {
                                            let mut exp = Command::new("explorer.exe");
                                            exp.arg(format!("/select,{}", dest.to_string_lossy()));
                                            exp.creation_flags(0x08000000);
                                            let _ = exp.spawn();
                                        }
                                        let js = format!("window.showToast && window.showToast('✅ Файл сохранён в Загрузки: {}');", js_escape(&file_name));
                                        let _ = p_clone.send_event(UserEvent::EvalJs(js));
                                    }
                                }
                            }
                        }
                    });
                }
                return;
            }
            if let Some(rest) = body.strip_prefix("self_update|") {
                let mut parts = rest.splitn(3, '|');
                let url = parts.next().unwrap_or("").to_string();
                let sha = parts.next().unwrap_or("").to_string();
                let size_val = parts.next().and_then(|s| s.parse::<u64>().ok()).unwrap_or(0);
                if UPDATE_BUSY.swap(true, Ordering::SeqCst) {
                    return;
                }
                let p_clone = ipc_proxy.clone();
                std::thread::spawn(move || {
                    match perform_self_update(&url, &sha, size_val, &p_clone) {
                        Ok(exe_path) => {
                            let _ = p_clone.send_event(UserEvent::RestartNewExe(exe_path));
                        }
                        Err(err_msg) => {
                            UPDATE_BUSY.store(false, Ordering::SeqCst);
                            let js = format!(
                                "window.__voltUpdateError && window.__voltUpdateError('{}');",
                                js_escape(&err_msg)
                            );
                            let _ = p_clone.send_event(UserEvent::EvalJs(js));
                        }
                    }
                });
            }
        })
        .build(&window)?;

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => {
                let mut cmd = Command::new("curl.exe");
                cmd.args([
                    "-sSL",
                    "--connect-timeout",
                    "1",
                    "--max-time",
                    "1",
                    &format!("{}/api/leave?hwid={}", VOLT_SERVER_URL.trim_end_matches('/'), hwid),
                ]);
                #[cfg(windows)]
                cmd.creation_flags(0x08000000);
                let _ = cmd.spawn();
                *control_flow = ControlFlow::Exit;
            }
            Event::UserEvent(user_evt) => match user_evt {
                UserEvent::EvalJs(js) => {
                    let _ = webview.evaluate_script(&js);
                }
                UserEvent::ReloadFresh(url) => {
                    let _ = webview.load_url(&url);
                }
                UserEvent::RestartNewExe(exe_path) => {
                    let _ = Command::new(&exe_path).spawn();
                    *control_flow = ControlFlow::Exit;
                }
            },
            _ => {}
        }
    });
}
