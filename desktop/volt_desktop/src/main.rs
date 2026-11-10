#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, RwLock};
use tao::{
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoopBuilder},
    platform::windows::{WindowBuilderExtWindows, WindowExtWindows},
    window::{Icon, WindowBuilder},
};
use wry::{PermissionResponse, WebViewBuilder};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const COMPILED_SERVER_URL: Option<&str> = option_env!("VOLT_SERVER_URL");
const ICON_32_BYTES: &[u8] = include_bytes!("../icon_32.rgba");
const ICON_64_BYTES: &[u8] = include_bytes!("../icon_64.rgba");
const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

static UPDATE_BUSY: AtomicBool = AtomicBool::new(false);

#[derive(Debug)]
enum UserEvent {
    EvalJs(String),
    ReloadFresh(String),
    RestartNewExe(PathBuf),
    ToggleOverlay,
    SyncOverlay(String),
}

#[cfg(windows)]
#[repr(C)]
struct WinMsg {
    hwnd: isize,
    message: u32,
    _pad: u32,
    w_param: usize,
    l_param: isize,
    time: u32,
    pt_x: i32,
    pt_y: i32,
    _l_private: u32,
}

#[cfg(windows)]
extern "system" {
    fn RegisterHotKey(hWnd: isize, id: i32, fsModifiers: u32, vk: u32) -> i32;
    fn UnregisterHotKey(hWnd: isize, id: i32) -> i32;
    fn GetMessageW(lpMsg: *mut WinMsg, hWnd: isize, wMsgFilterMin: u32, wMsgFilterMax: u32) -> i32;
    fn SetWindowLongPtrW(hWnd: isize, nIndex: i32, dwNewLong: isize) -> isize;
    fn GetWindowLongPtrW(hWnd: isize, nIndex: i32) -> isize;
}

const OVERLAY_HTML: &str = r###"<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      width: 100%;
      height: 100%;
      background: transparent !important;
      overflow: hidden;
      user-select: none;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    #overlay-root {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 6px;
      padding: 8px;
    }
    .hud-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(10, 10, 14, 0.86);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 999px;
      padding: 3px 10px;
      color: #fff;
      font-size: 10px;
      font-weight: 700;
      box-shadow: 0 6px 18px rgba(0, 0, 0, 0.5);
    }
    .hud-badge .bolt { color: #EBFB10; font-size: 11px; }
    .hud-badge .hotkey { color: #858484; font-size: 8.5px; font-family: Consolas, monospace; }

    .users-container {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 5px;
      width: 100%;
    }

    .user-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: rgba(12, 12, 16, 0.82);
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 999px;
      padding: 3px 12px 3px 4px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.55);
      transition: all 0.18s cubic-bezier(0.16, 1, 0.3, 1);
      opacity: 0.38;
      max-width: 240px;
    }

    .user-pill.speaking {
      opacity: 1.0 !important;
      border-color: rgba(235, 251, 16, 0.65) !important;
      background: rgba(18, 19, 11, 0.90) !important;
      box-shadow: 0 0 16px rgba(235, 251, 16, 0.35), 0 8px 24px rgba(0,0,0,0.6) !important;
      transform: translateX(3px);
    }

    .avatar-circle {
      width: 26px;
      height: 26px;
      border-radius: 50%;
      background: #141518;
      border: 1.5px solid rgba(255, 255, 255, 0.18);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #FFFFFF;
      font-size: 11px;
      font-weight: 700;
      flex-shrink: 0;
      transition: all 0.18s ease;
    }

    .user-pill.speaking .avatar-circle {
      border-color: #EBFB10 !important;
      color: #EBFB10 !important;
      box-shadow: 0 0 12px rgba(235, 251, 16, 0.8) !important;
      background: #1c1d0b !important;
    }

    .user-label {
      font-size: 11px;
      font-weight: 600;
      color: #BABABA;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 140px;
    }

    .user-pill.speaking .user-label {
      color: #FFFFFF !important;
      font-weight: 700 !important;
    }

    .eq-bars {
      display: none;
      align-items: flex-end;
      gap: 1.5px;
      height: 9px;
      margin-left: 2px;
    }

    .user-pill.speaking .eq-bars {
      display: inline-flex;
    }

    .bar {
      width: 2px;
      background: #EBFB10;
      border-radius: 1px;
      animation: eqBounce 0.32s infinite alternate ease-in-out;
    }
    .bar:nth-child(1) { height: 5px; animation-delay: 0.04s; }
    .bar:nth-child(2) { height: 9px; animation-delay: 0.12s; }
    .bar:nth-child(3) { height: 6px; animation-delay: 0.20s; }

    @keyframes eqBounce {
      0% { height: 3px; }
      100% { height: 9px; }
    }

    .mute-badge {
      font-size: 9px;
      color: #ED4245;
      margin-left: -2px;
    }
  </style>
</head>
<body>
  <div id="overlay-root">
    <div class="hud-badge" id="hudBadge">
      <span class="bolt">⚡</span>
      <span id="hudChannel">VOLT</span>
      <span class="hotkey">Shift + ~</span>
    </div>
    <div class="users-container" id="usersContainer"></div>
  </div>
  <script>
    window.__voltOverlaySync = function(payload) {
      if (!payload) return;
      const chan = document.getElementById('hudChannel');
      const container = document.getElementById('usersContainer');
      if (chan && payload.room) chan.textContent = payload.room.replace('⚡', '').trim();
      if (!container) return;

      const users = payload.users || [];
      if (users.length === 0) {
        container.innerHTML = '';
        return;
      }

      container.innerHTML = users.map(u => {
        const initial = (u.name || '?').charAt(0).toUpperCase();
        const speakingClass = u.speaking ? 'speaking' : '';
        const botIcon = u.is_bot ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#EBFB10" stroke-width="2.5"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>' : initial;
        return `
          <div class="user-pill ${speakingClass}">
            <div class="avatar-circle">${botIcon}</div>
            <span class="user-label">${u.name}</span>
            <div class="eq-bars">
              <span class="bar"></span><span class="bar"></span><span class="bar"></span>
            </div>
            ${u.muted ? '<span class="mute-badge">🔇</span>' : ''}
          </div>
        `;
      }).join('');
    };
  </script>
</body>
</html>"###;

fn normalize_server_url(raw: &str) -> Option<String> {
    let trimmed = raw.trim().trim_end_matches('/');
    if trimmed.starts_with("https://") || trimmed.starts_with("http://") {
        let after_scheme = trimmed
            .strip_prefix("https://")
            .or_else(|| trimmed.strip_prefix("http://"))
            .unwrap_or("");
        if !after_scheme.is_empty() && !after_scheme.contains(char::is_whitespace) {
            return Some(trimmed.to_string());
        }
    }
    None
}

fn saved_config_path() -> Option<PathBuf> {
    std::env::var_os("LOCALAPPDATA")
        .map(|d| PathBuf::from(d).join("VOLT").join("server_url.txt"))
}

fn resolve_initial_server_url() -> Option<String> {
    if let Some(url) = COMPILED_SERVER_URL {
        if let Some(clean) = normalize_server_url(url) {
            return Some(clean);
        }
    }
    if let Ok(exe_path) = std::env::current_exe() {
        let portable_cfg = exe_path.with_file_name("volt_server.txt");
        if let Ok(txt) = std::fs::read_to_string(portable_cfg) {
            if let Some(clean) = normalize_server_url(&txt) {
                return Some(clean);
            }
        }
    }
    if let Some(app_cfg) = saved_config_path() {
        if let Ok(txt) = std::fs::read_to_string(app_cfg) {
            if let Some(clean) = normalize_server_url(&txt) {
                return Some(clean);
            }
        }
    }
    None
}

fn persist_server_url(url: &str) -> Result<(), String> {
    if let Some(cfg_path) = saved_config_path() {
        if let Some(parent) = cfg_path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("Не удалось создать папку настроек: {}", e))?;
        }
        std::fs::write(&cfg_path, url)
            .map_err(|e| format!("Не удалось сохранить адрес сервера: {}", e))?;
    }
    Ok(())
}

fn setup_screen_html() -> &'static str {
    r###"<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>VOLT — Подключение к серверу</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body, html {
      width: 100%; height: 100%;
      background: #000000; color: #FFFFFF;
      font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
      display: flex; align-items: center; justify-content: center;
      overflow: hidden; user-select: none;
    }
    .orb {
      position: fixed; border-radius: 50%; filter: blur(130px); pointer-events: none;
    }
    .orb-1 { width: 420px; height: 420px; top: -120px; left: -100px; background: rgba(235, 251, 16, 0.12); }
    .orb-2 { width: 380px; height: 380px; bottom: -120px; right: -80px; background: rgba(255, 255, 255, 0.05); }
    .card {
      position: relative; z-index: 2; width: 460px; max-width: 92vw;
      background: linear-gradient(145deg, rgba(16,16,18,0.82), rgba(8,8,10,0.74));
      backdrop-filter: blur(26px); -webkit-backdrop-filter: blur(26px);
      border: 1px solid rgba(255,255,255,0.12); border-radius: 24px;
      padding: 32px; box-shadow: 0 24px 60px rgba(0,0,0,0.8);
    }
    .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
    .badge { font-size: 11px; font-weight: 700; color: #EBFB10; letter-spacing: 0.08em; }
    h1 { font-size: 20px; font-weight: 700; margin-bottom: 8px; }
    p { font-size: 13px; color: #9D9D9D; line-height: 1.5; margin-bottom: 20px; }
    label { display: block; font-size: 11px; font-weight: 600; color: #BABABA; margin-bottom: 8px; letter-spacing: 0.04em; }
    input {
      width: 100%; height: 44px; border-radius: 12px;
      background: rgba(0,0,0,0.65); border: 1px solid rgba(255,255,255,0.14);
      color: #FFFFFF; padding: 0 14px; font-size: 13.5px; outline: none;
      margin-bottom: 14px; transition: border-color 0.15s;
    }
    input:focus { border-color: #EBFB10; }
    .err { color: #ED4245; font-size: 12px; margin-bottom: 12px; min-height: 16px; }
    button {
      width: 100%; height: 44px; border-radius: 12px; border: none;
      background: #EBFB10; color: #000000; font-size: 13px; font-weight: 700;
      cursor: pointer; transition: background 0.15s;
    }
    button:hover { background: #d8e80e; }
    .hint { font-size: 11px; color: #707078; margin-top: 14px; line-height: 1.45; }
    code { color: #BABABA; font-family: Consolas, monospace; }
  </style>
</head>
<body>
  <div class="orb orb-1"></div>
  <div class="orb orb-2"></div>
  <form class="card" id="setup-form">
    <div class="brand">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="#EBFB10"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
      <span class="badge">VOLT DESKTOP</span>
    </div>
    <h1>Подключение к серверу</h1>
    <p>Укажите URL вашего self-hosted сервера VOLT, чтобы подключиться к голосовому каналу и встроенному плееру.</p>
    <label for="srv-url">АДРЕС СЕРВЕРА</label>
    <input id="srv-url" type="url" placeholder="https://voice.example.com" autofocus required />
    <div class="err" id="err-box"></div>
    <button type="submit">ПОДКЛЮЧИТЬСЯ</button>
    <div class="hint">Адрес сохраняется локально. Можно также положить файл <code>volt_server.txt</code> рядом с программой.</div>
  </form>
  <script>
    window.__voltSetupError = (msg) => {
      document.getElementById('err-box').textContent = msg || 'Некорректный URL';
    };
    document.getElementById('setup-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const val = document.getElementById('srv-url').value.trim();
      if (!/^https?:\/\/\S+$/i.test(val)) {
        window.__voltSetupError('Введите корректный URL (начинается с https:// или http://)');
        return;
      }
      document.getElementById('err-box').textContent = '';
      window.ipc.postMessage('set_server|' + val);
    });
  </script>
</body>
</html>"###
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
    expected_base: &str,
    expected_sha: &str,
    expected_size: u64,
    proxy: &tao::event_loop::EventLoopProxy<UserEvent>,
) -> Result<PathBuf, String> {
    if expected_base.is_empty() || !url.starts_with(expected_base) {
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

    let initial_url = resolve_initial_server_url();
    let active_server = Arc::new(RwLock::new(initial_url.clone().unwrap_or_default()));

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

    // ==========================================
    // 2. Gaming Overlay Window (Always-on-Top & Click-through)
    // ==========================================
    let overlay_builder = WindowBuilder::new()
        .with_title("VOLT Overlay")
        .with_inner_size(tao::dpi::LogicalSize::new(260.0, 340.0))
        .with_position(tao::dpi::LogicalPosition::new(24.0, 24.0))
        .with_decorations(false)
        .with_always_on_top(true)
        .with_transparent(true)
        .with_resizable(false)
        .with_visible(false);

    let overlay_window = overlay_builder.build(&event_loop)?;

    #[cfg(windows)]
    unsafe {
        let hwnd = overlay_window.hwnd() as isize;
        let cur = GetWindowLongPtrW(hwnd, -20); // GWL_EXSTYLE
        // WS_EX_TRANSPARENT (0x20) + WS_EX_LAYERED (0x80000) + WS_EX_TOOLWINDOW (0x80)
        SetWindowLongPtrW(hwnd, -20, cur | 0x00000020 | 0x00080000 | 0x00000080);
    }

    let overlay_webview = WebViewBuilder::new()
        .with_html(OVERLAY_HTML)
        .with_background_color((0, 0, 0, 0))
        .with_autoplay(true)
        .build(&overlay_window)?;

    // ==========================================
    // 3. Global Hotkey Thread: Shift + ` (Tilde)
    // ==========================================
    let hotkey_proxy = proxy.clone();
    std::thread::spawn(move || {
        #[cfg(windows)]
        unsafe {
            // Register Shift + ` (VK_OEM_3 = 0xC0) thread-level hotkey
            // MOD_SHIFT = 0x0004, MOD_NOREPEAT = 0x4000
            let res = RegisterHotKey(0, 1001, 0x0004 | 0x4000, 0xC0);
            if res != 0 {
                let mut msg: WinMsg = std::mem::zeroed();
                while GetMessageW(&mut msg, 0, 0, 0) > 0 {
                    if msg.message == 0x0312 && msg.w_param == 1001 { // WM_HOTKEY
                        let _ = hotkey_proxy.send_event(UserEvent::ToggleOverlay);
                    }
                }
                UnregisterHotKey(0, 1001);
            }
        }
    });

    let (hwid, dev_name) = compute_hardware_id();
    let init_script = format!(
        "window.__VOLT_DESKTOP__ = true; window.__VOLT_SUPPORTS_RELAUNCH__ = true; window.__VOLT_CLIENT_KIND__ = 'main'; window.__VOLT_EXE_VERSION__ = '{}'; window.__VOLT_EXE_SHA256__ = '{}'; window.__VOLT_HARDWARE_ID__ = '{}'; window.__VOLT_DEVICE_NAME__ = '{}'; if (!window.ipc && window.chrome && window.chrome.webview) {{ window.ipc = {{ postMessage: function(msg) {{ window.chrome.webview.postMessage(msg); }} }}; }}",
        APP_VERSION, local_sha, hwid, js_escape(&dev_name)
    );

    let ipc_proxy = proxy.clone();
    let ipc_server = Arc::clone(&active_server);
    let mut wb = WebViewBuilder::new()
        .with_background_color((0, 0, 0, 255))
        .with_autoplay(true)
        .with_clipboard(true)
        .with_permission_handler(|_| PermissionResponse::Allow)
        .with_user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 VoltDesktop/1.3")
        .with_initialization_script(&init_script)
        .with_ipc_handler(move |req| {
            let body = req.body().trim().to_string();
            if body == "relaunch" {
                if let Ok(cur_exe) = std::env::current_exe() {
                    let _ = ipc_proxy.send_event(UserEvent::RestartNewExe(cur_exe));
                }
                return;
            }
            if body == "toggle_overlay" {
                let _ = ipc_proxy.send_event(UserEvent::ToggleOverlay);
                return;
            }
            if let Some(json) = body.strip_prefix("overlay_sync|") {
                let _ = ipc_proxy.send_event(UserEvent::SyncOverlay(json.to_string()));
                return;
            }
            if let Some(raw_url) = body.strip_prefix("set_server|") {
                if let Some(clean_url) = normalize_server_url(raw_url) {
                    if let Err(err_msg) = persist_server_url(&clean_url) {
                        let js = format!("window.__voltSetupError && window.__voltSetupError('{}');", js_escape(&err_msg));
                        let _ = ipc_proxy.send_event(UserEvent::EvalJs(js));
                        return;
                    }
                    if let Ok(mut guard) = ipc_server.write() {
                        *guard = clean_url.clone();
                    }
                    let _ = ipc_proxy.send_event(UserEvent::ReloadFresh(clean_url));
                } else {
                    let _ = ipc_proxy.send_event(UserEvent::EvalJs(
                        "window.__voltSetupError && window.__voltSetupError('Укажите URL с http:// или https://');".to_string(),
                    ));
                }
                return;
            }
            let current_base = ipc_server.read().map(|g| g.clone()).unwrap_or_default();
            if let Some(url) = body.strip_prefix("reload|") {
                if !current_base.is_empty() && url.starts_with(&current_base) {
                    let _ = ipc_proxy.send_event(UserEvent::ReloadFresh(url.to_string()));
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
                    match perform_self_update(&url, &current_base, &sha, size_val, &p_clone) {
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
        });

    wb = match initial_url {
        Some(url) => wb.with_url(&url),
        None => wb.with_html(setup_screen_html()),
    };

    let webview = wb.build(&window)?;

    let leave_server = Arc::clone(&active_server);
    let mut overlay_is_visible = false;

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        match event {
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => {
                let base_url = leave_server.read().map(|g| g.clone()).unwrap_or_default();
                if !base_url.is_empty() {
                    let mut cmd = Command::new("curl.exe");
                    cmd.args([
                        "-sSL",
                        "--connect-timeout",
                        "1",
                        "--max-time",
                        "1",
                        &format!("{}/api/leave?hwid={}", base_url.trim_end_matches('/'), hwid),
                    ]);
                    #[cfg(windows)]
                    cmd.creation_flags(0x08000000);
                    let _ = cmd.spawn();
                }
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
                UserEvent::ToggleOverlay => {
                    overlay_is_visible = !overlay_is_visible;
                    overlay_window.set_visible(overlay_is_visible);
                    if overlay_is_visible {
                        overlay_window.set_always_on_top(true);
                    }
                    let toast = if overlay_is_visible {
                        "window.showToast && window.showToast('🎮 Оверлей поверх игр: ВКЛ (Shift + ~)');"
                    } else {
                        "window.showToast && window.showToast('🎮 Оверлей поверх игр: ВЫКЛ (Shift + ~)');"
                    };
                    let _ = webview.evaluate_script(toast);
                }
                UserEvent::SyncOverlay(json) => {
                    let script = format!("window.__voltOverlaySync && window.__voltOverlaySync({});", json);
                    let _ = overlay_webview.evaluate_script(&script);
                }
            },
            _ => {}
        }
    });
}

#[cfg(test)]
mod tests {
    use super::normalize_server_url;

    #[test]
    fn test_normalize_server_url() {
        assert_eq!(
            normalize_server_url("  https://voice.example.com/  "),
            Some("https://voice.example.com".to_string())
        );
        assert_eq!(
            normalize_server_url("http://127.0.0.1:3000/"),
            Some("http://127.0.0.1:3000".to_string())
        );
        assert_eq!(normalize_server_url("voice.example.com"), None);
        assert_eq!(normalize_server_url("https://"), None);
        assert_eq!(normalize_server_url("javascript:alert(1)"), None);
    }
}
