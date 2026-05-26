// 截图编码用,仅非 Linux 引入 —— Linux 不编译 xcap，两个抓屏函数走桩实现。
#[cfg(not(target_os = "linux"))]
use std::io::Cursor;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use sha2::{Digest, Sha256};
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, RunEvent, WindowEvent,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

/// 截图选区流程的跨窗口共享状态。
///
/// 流程:主窗调 ``chayuan_capture_for_overlay`` → Rust 抓屏写到 ``buffer`` →
/// 主窗自己创建 overlay 窗口 → overlay 窗读 ``buffer`` 渲图 + 让用户拖框 →
/// 用户确认/取消 → overlay 调 ``chayuan_submit_screenshot_result`` 写 ``result``
/// + emit 事件 + 自关 → 主窗收事件 → 调 ``chayuan_take_screenshot_result`` 拿
/// 最终裁剪 PNG(或 None=用户取消)。
///
/// 用 Tauri State 而非 ``Lazy<Mutex>``: 测试时可隔离;清理在 RunEvent::Exit
/// 自动 drop。bytes 走 Rust 全程不经 JSON,前后端零序列化开销。
#[derive(Default)]
struct ScreenshotState {
    /// 主窗抓屏后写,overlay 窗读一次(take)后清空
    buffer: Mutex<Option<Vec<u8>>>,
    /// overlay 窗交付的最终结果:
    ///   Some(Some(bytes)) → 用户确认了选区
    ///   Some(None)        → 用户取消
    ///   None              → overlay 还没交付(主窗早调时的初始态)
    result: Mutex<Option<Option<Vec<u8>>>>,
}

/// 主显示器尺寸 — 创建 overlay 窗口时定位 + 设大小用
#[derive(serde::Serialize)]
struct MonitorGeom {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

/// 托盘「新对话」的待处理标记。
///
/// 为什么需要它:托盘菜单点「新对话」时,Rust 会 `show()` 主窗 + `emit("cy:new-chat")`。
/// 但 `emit` 是一锤子买卖 —— 如果此刻前端的 listener 还没注册好(冷启动 webview
/// 还在 mount、`Chrome` 懒加载 chunk 没到、`useDesktopIntegrations` 里
/// `getCurrentWindow().listen` 这个 async 注册没完成),事件就丢了,表现为
/// 「窗口开了但没新建对话」。
///
/// 解法:点菜单时除了 emit,还把这个 flag 置 true;前端 `useDesktopIntegrations`
/// 在 listener 注册就绪后,主动 `invoke('chayuan_take_pending_new_chat')` 把 flag
/// `swap` 取走消费一次。两条路任一命中都触发新建对话,前端用短时间窗去重,
/// 保证不会开两个 tab。
#[derive(Default)]
struct TrayPendingState {
    /// 托盘点了「新对话」但前端可能还没收到 —— 前端就绪后 take 一次。
    new_chat: AtomicBool,
}

mod data_dir;
mod sidecar;

/// 派生稳定的 vault 密码：machine-uid + 应用盐。前端调 `chayuan_vault_password` 取。
#[tauri::command]
fn chayuan_vault_password() -> Result<String, String> {
    let mid = machine_uid::get().unwrap_or_else(|_| "anon-machine".to_string());
    let salt = "chayuan.vault.v1";
    let mut hasher = Sha256::new();
    hasher.update(mid.as_bytes());
    hasher.update(salt.as_bytes());
    Ok(hex::encode(hasher.finalize()))
}

/// 占位：tray 菜单按 id 重设；事件通过 'tray-click' emit 给前端。
#[tauri::command]
fn chayuan_tray_set_menu(_items: Vec<serde_json::Value>) -> Result<(), String> {
    // 当前默认 tray 在 setup 里建好；动态变更菜单项需更多 plumbing，
    // 后续按需扩展。返回 Ok 让前端稳定。
    Ok(())
}

#[tauri::command]
fn chayuan_tray_set_tooltip(text: String, app: tauri::AppHandle) -> Result<(), String> {
    if let Some(tray) = app.tray_by_id("main") {
        tray.set_tooltip(Some(text)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// 在系统文件管理器里打开一个本地路径(Windows 资源管理器 / macOS 访达 /
/// Linux 文件管理器)。
///
/// 为什么不走 plugin-shell.open:那条命令把参数当 URL,按
/// `^((mailto:\w+)|(tel:\w+)|(https?://\w+)).+` 正则校验,文件夹路径
/// (`C:\Users\...` 或 `file://...`)过不了 → "failed regex validation"。
/// 打开本地路径必须按平台分别调 explorer / open / xdg-open。
#[tauri::command]
fn chayuan_open_path(path: String) -> Result<(), String> {
    if !std::path::Path::new(&path).exists() {
        return Err(format!("路径不存在:{path}"));
    }
    #[cfg(target_os = "windows")]
    let program = "explorer";
    #[cfg(target_os = "macos")]
    let program = "open";
    #[cfg(all(unix, not(target_os = "macos")))]
    let program = "xdg-open";
    // fire-and-forget:explorer.exe 即便成功也常返回非 0 退出码,不 wait/不查
    // exit status,spawn 成功即认为已唤起。
    std::process::Command::new(program)
        .arg(&path)
        .spawn()
        .map_err(|e| format!("打开 {path} 失败:{e}"))?;
    Ok(())
}

/// 截图（全屏第一块显示器）→ PNG 字节
///
/// xcap 在 Linux 上拖 pipewire/libspa(见 Cargo.toml 注释),libspa 没有能匹配
/// 用户系统 SPA 头的版本，故 Linux 不引入 xcap。本命令在 Linux 用桩实现:命令
/// 仍注册(invoke_handler 不缺命令、前端 invoke 不会因「命令不存在」崩),调用
/// 时返回 Err 说明 Linux 暂不支持截图。
#[cfg(not(target_os = "linux"))]
#[tauri::command]
fn chayuan_screenshot() -> Result<Vec<u8>, String> {
    let monitors = xcap::Monitor::all().map_err(|e| format!("xcap: {e}"))?;
    let primary = monitors.into_iter().next().ok_or_else(|| "no monitor".to_string())?;
    let img = primary.capture_image().map_err(|e| format!("capture: {e}"))?;
    let mut buf: Vec<u8> = Vec::new();
    let dyn_img = image::DynamicImage::ImageRgba8(img);
    dyn_img
        .write_to(&mut Cursor::new(&mut buf), image::ImageFormat::Png)
        .map_err(|e| format!("png: {e}"))?;
    Ok(buf)
}

/// Linux 桩:截图功能在 Linux 暂不支持（见上方说明 / Cargo.toml）。
#[cfg(target_os = "linux")]
#[tauri::command]
fn chayuan_screenshot() -> Result<Vec<u8>, String> {
    Err("截图功能在 Linux 暂不支持".to_string())
}

/// 截图 → 写到共享 state,返回显示器几何信息给 caller 用来创建 overlay 窗口。
///
/// 主窗调用时机:用户点剪刀 → 抓屏入 state → JS 端用 ``WebviewWindow`` 在主显示
/// 器位置创建无边框透明 overlay → overlay 调 ``chayuan_take_screenshot_buffer``
/// 读回 PNG 字节 → 拖框选区。
///
/// 同时清空 result slot(防止上次 cancelled 残留污染本次结果)。
///
/// Linux 同样走桩(见 ``chayuan_screenshot`` 说明):xcap 不在 Linux 编译。
#[cfg(not(target_os = "linux"))]
#[tauri::command]
fn chayuan_capture_for_overlay(
    state: tauri::State<'_, ScreenshotState>,
) -> Result<MonitorGeom, String> {
    let monitors = xcap::Monitor::all().map_err(|e| format!("xcap: {e}"))?;
    let primary = monitors.into_iter().next().ok_or_else(|| "no monitor".to_string())?;
    // xcap 0.6+ 把 x/y/width/height 改成 Result<T, XCapError>(底层 OS API 可能
    // 失败,比如 Wayland 无权限);用 ? 解包,失败映射成 String
    let geom = MonitorGeom {
        x: primary.x().map_err(|e| format!("monitor.x: {e}"))?,
        y: primary.y().map_err(|e| format!("monitor.y: {e}"))?,
        width: primary.width().map_err(|e| format!("monitor.width: {e}"))?,
        height: primary.height().map_err(|e| format!("monitor.height: {e}"))?,
    };
    let img = primary.capture_image().map_err(|e| format!("capture: {e}"))?;
    let mut buf: Vec<u8> = Vec::new();
    let dyn_img = image::DynamicImage::ImageRgba8(img);
    dyn_img
        .write_to(&mut Cursor::new(&mut buf), image::ImageFormat::Png)
        .map_err(|e| format!("png: {e}"))?;
    *state.buffer.lock().map_err(|e| format!("buffer lock: {e}"))? = Some(buf);
    *state.result.lock().map_err(|e| format!("result lock: {e}"))? = None;
    Ok(geom)
}

/// Linux 桩:截图功能在 Linux 暂不支持（见 ``chayuan_screenshot`` / Cargo.toml）。
/// 保留 ``state`` 参数与签名不变，仅清空残留结果后返回 Err。
#[cfg(target_os = "linux")]
#[tauri::command]
fn chayuan_capture_for_overlay(
    state: tauri::State<'_, ScreenshotState>,
) -> Result<MonitorGeom, String> {
    let _ = &state;
    Err("截图功能在 Linux 暂不支持".to_string())
}

/// overlay 窗口拉取主窗刚抓的 PNG;take 一次后 state 清空(避免悬挂大块内存)
#[tauri::command]
fn chayuan_take_screenshot_buffer(
    state: tauri::State<'_, ScreenshotState>,
) -> Result<Vec<u8>, String> {
    state.buffer.lock().map_err(|e| format!("buffer lock: {e}"))?
        .take()
        .ok_or_else(|| "screenshot buffer empty — capture_for_overlay 没先调?".to_string())
}

/// overlay 窗口提交最终结果:
///   bytes=Some(裁剪 PNG) → 用户确认
///   bytes=None           → 用户取消
/// 写入 state 后立刻 emit ``chayuan://screenshot/done`` 到主窗,主窗 listen
/// 到后用 ``chayuan_take_screenshot_result`` 取走。
#[tauri::command]
async fn chayuan_submit_screenshot_result(
    bytes: Option<Vec<u8>>,
    app: tauri::AppHandle,
    state: tauri::State<'_, ScreenshotState>,
) -> Result<(), String> {
    *state.result.lock().map_err(|e| format!("result lock: {e}"))? = Some(bytes);
    // emit 到所有窗口,主窗自己 listen 即可;事件没有 payload,bytes 在 state
    let _ = app.emit("chayuan://screenshot/done", ());
    // 自关 overlay — 用户 confirm/cancel 后窗口立刻消失,体验跟 QQ 一致
    if let Some(overlay) = app.get_webview_window("screenshot-overlay") {
        let _ = overlay.close();
    }
    Ok(())
}

/// 主窗在收到 done 事件后拉走结果。
/// 返 None:用户取消 / 还没提交(异常路径,主窗 listen 之后才会调)
#[tauri::command]
fn chayuan_take_screenshot_result(
    state: tauri::State<'_, ScreenshotState>,
) -> Result<Option<Vec<u8>>, String> {
    Ok(state
        .result
        .lock()
        .map_err(|e| format!("result lock: {e}"))?
        .take()
        .flatten())
}

/// 前端取走并清除「托盘新对话」待处理标记。
///
/// `useDesktopIntegrations` 在 `cy:new-chat` listener 注册完成后调一次:
/// 返回 `true` 说明在 listener 就绪前用户点过托盘「新对话」、`emit` 很可能丢了,
/// 前端据此补触发一次新建对话。`swap` 取走后 flag 归位,保证只消费一次。
#[tauri::command]
fn chayuan_take_pending_new_chat(state: tauri::State<'_, TrayPendingState>) -> bool {
    state.new_chat.swap(false, Ordering::SeqCst)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // ── Linux:关掉 WebKitGTK 的 DMABUF 渲染器 ───────────────────────────
    // WebKitGTK 2.42+ 默认用 DMABUF 做 GPU 合成层。在没有正常 GPU 加速的环境
    // (虚拟机 / 国产 Linux 杂牌显卡 / 远程桌面)里 DMABUF 合成会失败,弹层 /
    // 下拉 / 对话框 / tab 等「独立合成层」渲染成黑块或品红块(实测 bug/ 截图)。
    // 关掉 DMABUF 渲染器即回退到可靠路径,实测黑块全消。
    // 必须在 webview / GTK 初始化前设置 —— 放 run() 第一行。
    #[cfg(target_os = "linux")]
    std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");

    // window_state plugin:只持久化 SIZE / POSITION / MAXIMIZED / FULLSCREEN,
    // 启动时按窗口 label 自动 restore。
    //
    // 排除 VISIBLE / DECORATIONS:主窗口 conf.json 固定 visible:true +
    // decorations:true,不让 plugin 用历史 state 覆盖(否则上次若意外存了
    // visible=false,下次启动主窗口会不显示)。
    let window_state_plugin = tauri_plugin_window_state::Builder::default()
        .with_state_flags(
            tauri_plugin_window_state::StateFlags::SIZE
                | tauri_plugin_window_state::StateFlags::POSITION
                | tauri_plugin_window_state::StateFlags::MAXIMIZED
                | tauri_plugin_window_state::StateFlags::FULLSCREEN,
        )
        .build();

    tauri::Builder::default()
        // 单实例:必须是第一个注册的插件(官方要求 single-instance 先于其它
        // .plugin())。第二个实例启动时插件检测到已有实例 → 在首个实例里跑
        // 下面的回调 → 第二个实例自动退出。回调里把已运行的主窗唤到前台,
        // 复用托盘 "show" 分支同样的 unminimize/show/set_focus 三件套
        // (窗口可能被最小化,或右上角 X 隐藏到了托盘)。
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(window_state_plugin)
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_sql::Builder::default().build())
        // Stronghold 用 ChaCha20-Poly1305 加密 vault,key 必须是稳定的 32 byte。
        // 旧 closure 直接 password.as_bytes().to_vec(),长度不固定时 libsodium 抛
        // "illegal non-contiguous size",前端 secure store 全废。
        // 这里用 argon2id 做 KDF:salt 写死 + 单次轮数(冷启动一次,够用),
        // 输出固定 32 byte。失败软回落到 SHA-256(同样 32 byte,弱但保证不崩)。
        .plugin(tauri_plugin_stronghold::Builder::new(|password| {
            use argon2::{Algorithm, Argon2, Params, Version};

            let salt: &[u8] = b"chayuan.stronghold.v1";
            let mut key = [0u8; 32];

            let params = Params::new(19_456, 2, 1, Some(32))
                .unwrap_or_else(|_| Params::default());
            let argon2 = Argon2::new(Algorithm::Argon2id, Version::V0x13, params);

            if argon2.hash_password_into(password.as_bytes(), salt, &mut key).is_err() {
                // KDF 失败的话用 SHA-256 兜底,至少 key 长度合规
                let mut hasher = Sha256::new();
                hasher.update(password.as_bytes());
                hasher.update(salt);
                let digest = hasher.finalize();
                key.copy_from_slice(&digest);
            }
            key.to_vec()
        }).build())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Arc::new(sidecar::SidecarManager::new(62581)))
        .manage(ScreenshotState::default())
        .manage(TrayPendingState::default())
        .invoke_handler(tauri::generate_handler![
            chayuan_vault_password,
            chayuan_tray_set_menu,
            chayuan_tray_set_tooltip,
            chayuan_screenshot,
            chayuan_capture_for_overlay,
            chayuan_take_screenshot_buffer,
            chayuan_submit_screenshot_result,
            chayuan_take_screenshot_result,
            chayuan_take_pending_new_chat,
            chayuan_open_path,
            data_dir::chayuan_data_dir_state,
            data_dir::chayuan_data_dir_set,
            data_dir::chayuan_data_dir_default,
            data_dir::chayuan_data_dir_check_existing,
            data_dir::chayuan_data_dir_reset,
            sidecar::chayuan_sidecar_start,
            sidecar::chayuan_sidecar_status,
            sidecar::chayuan_sidecar_kill
        ])
        .setup(|app| {
            // splash 改为内联在 index.html 的 #chayuan-splash(单窗口方案),
            // 主窗口 conf.json 直接 visible:true,首帧就是 HTML 解析出的 splash
            // 动画。不再需要"隐藏 main / 显示独立 splash 窗口"的强制顺序。

            // ── 系统托盘 ────────────────────────────────────────────
            // 菜单(从上到下):
            //   打开察元 AI
            //   ─── 分隔 ───
            //   重启
            //   关于
            //   ─── 分隔 ───
            //   退出
            let show     = MenuItem::with_id(app, "show",     "打开察元 AI", true, None::<&str>)?;
            let sep1     = PredefinedMenuItem::separator(app)?;
            let restart  = MenuItem::with_id(app, "restart",  "重启",         true, None::<&str>)?;
            let about    = MenuItem::with_id(app, "about",    "关于",         true, None::<&str>)?;
            let sep2     = PredefinedMenuItem::separator(app)?;
            // 右上角 X 是关到托盘不退程序;真退出由"退出"菜单触发,后台 sidecar
            // 也会一起结束(由 RunEvent::ExitRequested 的 on_window_event 处理)。
            //
            // quit = 退出桌面应用;依赖 RunEvent::ExitRequested 里 kill_on_exit
            //        收掉后端 chayuan-server 进程本身。
            let quit     = MenuItem::with_id(app, "quit",     "退出", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[&show, &sep1, &restart, &about, &sep2, &quit],
            )?;

            // 托盘图标:用 tauri build 注入的 default_window_icon (即 tauri.conf.json
            // bundle.icon 链里第一张),保证跟标题栏 / installer 图标视觉一致。
            // 拿不到 → setup 失败 (理论不会 — bundle.icon 已配 icons/icon.ico,
            // tauri 编译期把 ico 嵌进 binary 当 default_window_icon)。
            let tray_icon = app
                .default_window_icon()
                .cloned()
                .ok_or_else(|| {
                    "default_window_icon 不存在 — tauri.conf.json bundle.icon 链丢失?"
                        .to_string()
                })?;
            let _tray = TrayIconBuilder::with_id("main")
                .icon(tray_icon)
                .tooltip("察元 AI")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.unminimize();
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "restart" => {
                        // AppHandle::restart 是 divergent(返 !),tauri 内部把
                        // 当前进程替换成新 instance,后面代码不会跑。
                        app.restart();
                    }
                    "about" => {
                        // 非阻塞 message dialog;callback 忽略点击结果。
                        let pkg_ver = app.package_info().version.to_string();
                        app.dialog()
                            .message(format!(
                                "察元 AI · Chayuan AI 桌面单机版\n\
                                 版本:{pkg_ver}\n\
                                 \n\
                                 面向办公场景的离线 AI 助手 — 把 WPS/Office 文档\n\
                                 处理、个人/企业知识库、结构化数据、外部向量库、\n\
                                 多模态资料和本地/服务端模型能力统一起来。\n\
                                 \n\
                                 © Chayuan AI · AGPL-3.0"
                            ))
                            .title("关于 察元 AI")
                            .kind(MessageDialogKind::Info)
                            .show(|_| {});
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    // 用户要求:双击图标 → 打开主窗。
                    // 单击不做切换显示/隐藏 — 避免和双击竞争误触(Windows 系统会先派发
                    // Click,250ms 内再派发 DoubleClick);右键菜单 Tauri 内置支持,
                    // 右键即出 menu 不需要 hook。
                    if let TrayIconEvent::DoubleClick { .. } = event {
                        if let Some(w) = tray.app_handle().get_webview_window("main") {
                            let _ = w.unminimize();
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                })
                .build(app)?;

            // ── 拦截 main 关闭:右上角 X → 隐藏到托盘,不退程序 ─────
            // 真退出由托盘菜单"退出"触发(app.exit(0)),或者外部信号 / 任务管理器
            // 强杀。RunEvent::ExitRequested 仍会被走到回收 sidecar。
            if let Some(main) = app.get_webview_window("main") {
                let main_for_close = main.clone();
                main.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = main_for_close.hide();
                    }
                });
            }

            #[cfg(debug_assertions)]
            {
                let window = app.get_webview_window("main").unwrap();
                window.open_devtools();
            }

            // 单窗口内联 splash 方案下不再需要"30s 强制 show main"兜底 —
            // 主窗口始终 visible,splash 是 DOM 节点(#chayuan-splash),
            // 由前端 splash.ts 的 8s 安全兜底负责移除,Rust 端无需介入。

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // 应用退出前回收 sidecar 子进程,避免留僵尸 Python 进程占数据目录锁。
            if let RunEvent::ExitRequested { .. } = event {
                let mgr = app_handle.state::<Arc<sidecar::SidecarManager>>();
                mgr.kill_on_exit();
            }
        });
}
