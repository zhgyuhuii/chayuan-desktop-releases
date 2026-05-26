//! 单机模式 chayuan-server sidecar 启停管理(Phase 3)。
//!
//! 流程:
//!
//! 1. **不在 Tauri ``setup`` 里立刻 spawn** —— 用户首启动向导(``data_dir.rs``)
//!    必须先选定数据目录,我们才知道往子进程注入什么 ``CHAYUAN_ROOT``。
//!    所以 spawn 入口是前端命令 ``chayuan_sidecar_start`` ── FirstRunSetup
//!    确认后由 Shell 调用一次。
//! 2. spawn 时用 ``app.path().resolve("chayuan-server/chayuan-server.exe",
//!    BaseDirectory::Resource)`` 拿可执行路径,``Command::new(path)`` 起子进程
//!    (2026-05-19 切 PyInstaller --onedir 起换走 resources 而不是 externalBin);
//!    参数:``start -a --single-machine``;
//!    env 注入:``CHAYUAN_ROOT`` + 单机 profile 系列(详见 ``chayuan/startup.py``)。
//! 3. 后台线程监听 ``CommandEvent::{Stdout, Stderr, Terminated}`` ──
//!    日志逐行追加到 last_log_line(诊断用);Terminated 时把 state 切到
//!    ``failed`` / ``stopped``。
//! 4. 另一后台线程 polling ``http://127.0.0.1:<port>/healthz`` 直到 200 OK,
//!    把 state 切到 ``ready``。前端调 ``chayuan_sidecar_status`` 取最新。
//! 5. Tauri ``RunEvent::ExitRequested`` / ``WindowEvent::CloseRequested`` 由
//!    ``lib.rs`` 触发 ``chayuan_sidecar_kill`` ── 子进程 SIGTERM(plugin-shell
//!    内部用 ``kill_on_drop`` + 显式 kill)。
//!
//! 开发态(``cfg!(debug_assertions)``)默认**不 spawn**:开发者自己跑
//! ``poetry run chayuan start -a``。前端探测到 dev 时直接走 62581。
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::path::BaseDirectory;
use tauri::{AppHandle, Emitter, Manager, Runtime};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// 子进程状态机。前端据此渲染 banner / 错误覆盖层。
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub enum SidecarState {
    /// 未启动(等 FirstRunSetup 确认后调 ``chayuan_sidecar_start``)
    #[serde(rename = "idle")]
    Idle,
    /// 已 spawn,但 ``/healthz`` 还没返 200
    #[serde(rename = "starting")]
    Starting,
    /// ``/healthz`` 200,业务请求可发
    #[serde(rename = "ready")]
    Ready,
    /// 启动失败 / 健康探测超时 / 进程异常退出
    #[serde(rename = "failed")]
    Failed,
    /// 用户主动停(关窗 / chayuan_sidecar_kill)
    #[serde(rename = "stopped")]
    Stopped,
    /// dev 模式跳过 spawn(开发者自己跑 server)
    #[serde(rename = "disabled")]
    Disabled,
}

#[derive(Debug, Clone, Serialize)]
pub struct SidecarStatus {
    pub state: SidecarState,
    pub port: u16,
    /// 业务侧直接拿这个填 ``configureClient({ baseURL })``
    pub base_url: String,
    /// 重启次数(自动重启 Phase 4 接;v0 不重启)
    pub restarts: u32,
    /// 失败时的诊断信息(stdout / stderr 最后一行 / 退出码 etc.)
    pub error: Option<String>,
    /// 最后一行 log,前端展示在 banner 里(便于用户看到启动进度)
    pub last_log_line: Option<String>,
    /// 当前数据目录(用于"切换数据目录"向导回显)
    pub data_dir: String,
}

impl SidecarStatus {
    fn idle(port: u16) -> Self {
        Self {
            state: SidecarState::Idle,
            port,
            base_url: format!("http://127.0.0.1:{port}"),
            restarts: 0,
            error: None,
            last_log_line: None,
            data_dir: String::new(),
        }
    }
}

/// 由 ``lib.rs`` 注入到 Tauri State 的句柄。
pub struct SidecarManager {
    inner: Mutex<ManagerInner>,
}

struct ManagerInner {
    status: SidecarStatus,
    child: Option<CommandChild>,
}

impl SidecarManager {
    pub fn new(default_port: u16) -> Self {
        Self {
            inner: Mutex::new(ManagerInner {
                status: SidecarStatus::idle(default_port),
                child: None,
            }),
        }
    }

    pub fn snapshot(&self) -> SidecarStatus {
        self.inner.lock().unwrap().status.clone()
    }

    /// 切状态;``error`` = ``None`` 时清掉历史 error(转 ready / idle 用)。
    fn set_state(&self, state: SidecarState, error: Option<String>) {
        let mut g = self.inner.lock().unwrap();
        g.status.state = state;
        g.status.error = error;
    }

    fn set_log_line(&self, line: String) {
        self.inner.lock().unwrap().status.last_log_line = Some(line);
    }

    fn store_child(&self, child: CommandChild) {
        self.inner.lock().unwrap().child = Some(child);
    }

    fn take_child(&self) -> Option<CommandChild> {
        self.inner.lock().unwrap().child.take()
    }

    fn set_data_dir(&self, dir: String) {
        self.inner.lock().unwrap().status.data_dir = dir;
    }

    /// 应用退出前的清理钩子(``lib.rs`` 在 ``RunEvent::ExitRequested`` 调用)。
    ///
    /// ⚠ 只 ``kill`` 后端 chayuan-server 进程**本身**,不递归子进程。
    /// 模型服务(llama-server / whisper-server / infinity 等)是 chayuan-server
    /// 自己 spawn 的子进程,这条路径不会收掉它们 —— 需要杀整棵进程树请用
    /// ``kill_tree_on_exit``。
    pub fn kill_on_exit(&self) {
        if let Some(child) = self.take_child() {
            let _ = child.kill();
        }
    }

    /// 杀掉后端 chayuan-server **整棵进程树**(它本身 + 它 spawn 的所有子孙)。
    ///
    /// 背景:Tauri 只直接拉起 chayuan-server 一个子进程(``externalBin`` 为空,
    /// 没有任何模型 sidecar 由 Tauri 管理)。llama-server / whisper-server /
    /// infinity 等模型服务全部是 chayuan-server(Python 后端)运行期自己
    /// spawn 出来的子孙进程。所以单 ``child.kill()`` 只收掉 Python 父进程,
    /// 模型服务会变成孤儿继续占显存 / 端口。要杀干净必须按进程树整棵收。
    ///
    /// ⚠ 为什么单 ``taskkill /T`` 不够 —— 头号坑:
    ///   后端进程树是多层的:
    ///     ``chayuan-server.exe``(Tauri 拉起,本 pid)
    ///       └─ ``chayuan-server.exe``(startup.py ``mp.set_start_method("spawn")``
    ///          起的 API Server 子进程)
    ///            └─ ``llama-server.exe`` / ``whisper-server.exe``(local_runtime.py
    ///               ``subprocess.Popen`` 起的模型 sidecar)
    ///   ``taskkill /PID <root> /T /F`` 靠**实时 PPID 链**枚举整棵树,但 ``/F``
    ///   强杀中间层时,根进程和 API Server 子进程几乎同时死,llama-server 在被
    ///   枚举到之前父进程就没了 → 变孤儿(Windows 不把孤儿挂到祖父,PPID 失效)
    ///   → ``/T`` 够不到 → 残留后台。这是用户报"llama-server 杀不掉"的根因。
    ///
    /// 实现(进程树 + 映像名双保险):
    ///   * Windows —— 先 ``taskkill /PID <pid> /T /F`` 收进程树;再追加按映像名
    ///     ``taskkill /F /IM llama-server.exe /IM whisper-server.exe`` 兜底,
    ///     直接收掉所有同名 sidecar,不依赖 PPID 链。这两个二进制是本应用独占
    ///     拉起的(集成版自带,单机版场景用户机上不会有别的同名进程),按映像名
    ///     杀是稳妥的。
    ///     注意:image-embedding 的 infinity sidecar 是 ``chayuan-server.exe -m
    ///     ...`` 跑的(映像名跟后端自己一样),**不能**按映像名杀,但它是 API
    ///     Server 的直接子进程,``/T`` 已覆盖,无需额外处理。
    ///   * unix —— 先对 pid 本身 ``kill -KILL``;再 ``pkill -f`` 按名字兜底收掉
    ///     llama-server / whisper-server(spawn 时没建独立进程组,无法 killpg)。
    ///
    /// 返回 ``true`` 表示拿到过 pid 并发出过 kill;``false`` 表示当时没有
    /// 在管理的子进程(已停 / 未启动 / dev 模式没 spawn)。
    pub fn kill_tree_on_exit(&self) -> bool {
        // ``child`` 在 windows / unix 分支里都会被 ``child.kill()`` 消费掉;
        // 仅在既非 windows 也非 unix 的(理论)平台上不会用到 —— 加 allow 抑制。
        #[cfg_attr(not(any(windows, unix)), allow(unused_variables))]
        let Some(child) = self.take_child() else {
            return false;
        };
        // tauri-plugin-shell 2.x:CommandChild::pid() 返子进程 OS pid。
        let pid = child.pid();

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // CREATE_NO_WINDOW(0x0800_0000):不弹 taskkill 的控制台黑窗。
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            // 1) 进程树:/T = 连子进程树,/F = 强制。先同步收整棵树
            //    (.status() 阻塞等 taskkill 跑完,确保在 child.kill() 之前完成)。
            let _ = std::process::Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .status();
            // 2) 映像名兜底:进程树枚举可能因多层 /F 强杀竞态够不到深层 sidecar,
            //    直接按映像名收掉残留的 llama-server / whisper-server。
            //    本应用独占拉起这两个二进制,单机版场景按名字杀是安全的。
            let _ = std::process::Command::new("taskkill")
                .args([
                    "/F",
                    "/IM", "llama-server.exe",
                    "/IM", "whisper-server.exe",
                ])
                .creation_flags(CREATE_NO_WINDOW)
                .status();
            // 3) 兜底:即使上面都失败,也再调一次 plugin-shell 的 kill 收父进程。
            let _ = child.kill();
        }

        #[cfg(unix)]
        {
            // 1) spawn 时没设独立进程组,无法 killpg 整组;直接 SIGKILL 父 pid。
            unsafe {
                libc_kill(pid as i32, 9);
            }
            // 2) 映像名兜底:pkill -f 按命令行匹配收掉残留的 llama-server /
            //    whisper-server(它们是后端的子孙进程,父进程被杀后会变孤儿)。
            //    -f 匹配完整命令行,覆盖带绝对路径 spawn 的情况。
            let _ = std::process::Command::new("pkill")
                .args(["-9", "-f", "llama-server"])
                .status();
            let _ = std::process::Command::new("pkill")
                .args(["-9", "-f", "whisper-server"])
                .status();
            // 3) plugin-shell 的 kill 兜底一次。
            let _ = child.kill();
        }

        let _ = pid; // 非 windows/unix 平台占位,避免 unused 警告
        true
    }
}

/// 直接声明 ``kill(2)`` —— 仅 unix 用,避免为这一处引入 ``libc`` 整个 crate。
/// signature 对齐 POSIX:``int kill(pid_t pid, int sig)``。
#[cfg(unix)]
extern "C" {
    #[link_name = "kill"]
    fn libc_kill(pid: i32, sig: i32) -> i32;
}

/// 默认端口与 ``Settings.basic_settings.API_SERVER["port"]`` 对齐。
/// 实际端口由 ``SidecarManager::new(port)`` 在 ``lib.rs`` 注入,这里仅作
/// 文档锚点 + 给手动测试用,不会被 import,加 ``#[allow(dead_code)]``。
#[allow(dead_code)]
const DEFAULT_PORT: u16 = 62581;

/// 健康探测超时与轮询间隔。``healthz`` 是无依赖端点(只 return ok),2s 够用。
const HEALTH_TIMEOUT: Duration = Duration::from_secs(2);
const HEALTH_POLL_INTERVAL: Duration = Duration::from_millis(800);
/// 启动总等待:300s。
///
/// 历史值 60s 在以下场景必超时,导致主界面进不去:
///   - PyInstaller --onefile 首次解压 726 MB bundle 到 ``%TEMP%\_MEIxxxxx``
///     ≈100-150s(冷盘 / 装机后第一次启动 / 杀软扫描时更长)
///   - 父进程 ``startup.py`` bootstrap(create_tables / run_migrations /
///     config_center seed / default admin)≈10s
///   - API 子进程 lifespan startup(fire-and-forget 后 <2s)
///   合计 worst-case ~180s,留富裕到 300s。
///
/// 副作用:首次启动用户最长会看 5 分钟的"启动中"。但 60s 误判 Failed 后
/// 整个主界面就进不去,体验更差。后续可以改成 Failed 后继续后台 poll,
/// 一旦 /healthz 返 200 就 self-heal 到 Ready;先用大 deadline 兜住。
const HEALTH_TOTAL_DEADLINE: Duration = Duration::from_secs(300);

fn poll_healthz_once(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/healthz");
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(HEALTH_TIMEOUT)
        .timeout_read(HEALTH_TIMEOUT)
        .build();
    match agent.get(&url).call() {
        Ok(resp) => resp.status() == 200,
        Err(_) => false,
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SidecarStartArgs {
    pub data_dir: String,
    /// 可选:vendor 平台子目录强制覆盖(对齐 chayuan-server 的 CHAYUAN_VENDOR_PLATFORM env)。
    /// 用户在「本地模型」设置页选了 win-x64-noavx / win-x64-avx512 等 CPU 变体时填这个;
    /// 不传 = sidecar 走自动检测(_platform_subdir_candidates)。
    #[serde(default)]
    pub vendor_platform: Option<String>,
}

/// 前端 FirstRunSetup 确认后调用一次。已 ready 时是 no-op。
#[tauri::command]
pub fn chayuan_sidecar_start<R: Runtime>(
    app: AppHandle<R>,
    state: tauri::State<'_, Arc<SidecarManager>>,
    args: SidecarStartArgs,
) -> Result<SidecarStatus, String> {
    // 已 ready / starting 直接返当前快照,不重复 spawn
    {
        let g = state.inner.lock().unwrap();
        if matches!(
            g.status.state,
            SidecarState::Starting | SidecarState::Ready
        ) {
            return Ok(g.status.clone());
        }
    }

    // 开发态:不 spawn(开发者自己跑 chayuan start -a),直接报 disabled。
    // 通过环境变量 ``CHAYUAN_DESKTOP_SPAWN_SIDECAR=1`` 强制 dev 也 spawn(调试 Phase 3 用)。
    let force_spawn = std::env::var("CHAYUAN_DESKTOP_SPAWN_SIDECAR")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false);
    if cfg!(debug_assertions) && !force_spawn {
        state.set_state(SidecarState::Disabled, None);
        state.set_data_dir(args.data_dir.clone());
        return Ok(state.snapshot());
    }

    state.set_data_dir(args.data_dir.clone());
    state.set_state(SidecarState::Starting, None);

    let port = state.snapshot().port;

    // ── 1. 解析 chayuan-server bootloader 路径 ────────────────────
    // 2026-05-19 起 chayuan-server 不再用 Tauri ``externalBin``(externalBin 只
    // 能嵌单 exe,无法跟着 PyInstaller --onedir 的 _internal/ 一起进包)。
    // 改走 ``bundle.resources``,build.py 把 dist/chayuan-server/ 整树拷到
    // src-tauri/chayuan-server/,装机后落在 <install>/resources/chayuan-server/。
    // 用 BaseDirectory::Resource 解析:dev 模式落 src-tauri/chayuan-server/,
    // 生产模式落 <install>/resources/chayuan-server/。两端命名一致。
    #[cfg(target_os = "windows")]
    let exe_rel = "chayuan-server/chayuan-server.exe";
    #[cfg(not(target_os = "windows"))]
    let exe_rel = "chayuan-server/chayuan-server";

    let exe_path = app
        .path()
        .resolve(exe_rel, BaseDirectory::Resource)
        .map_err(|e| format!("resolve {exe_rel}: {e}"))?;
    if !exe_path.is_file() {
        return Err(format!(
            "chayuan-server bootloader 不存在:{} \
             (build.py 是否漏拷 dist/chayuan-server/ 到 resources?)",
            exe_path.display()
        ));
    }

    // tauri-plugin-shell 2.x:Command::new 是 pub(crate),库外不能直接构造;
    // 走 ShellExt::shell().command(<path>) 路径,接受任意可执行路径。
    // shell.command 仅在 JS 端 IPC 调用时受 capabilities 策略约束;
    // Rust 端直接 spawn 不走 IPC,不需要 shell:allow-execute 白名单。
    let mut cmd = app
        .shell()
        .command(exe_path.to_string_lossy().to_string())
        .args(["start", "-a", "--single-machine"])
        // ⚠ CHAYUAN_ROOT_IGNORE_STATE=1 必须设。
        // chayuan/paths.py::resolve_chayuan_root 默认优先读 ~/.chayuan/state.json
        // 里 last_init_root —— 开发机上跑过 ``chayuan init`` 后写过的目录会凌驾
        // 于这里 env 注入的 CHAYUAN_ROOT 之上,导致 sidecar 读老 yaml 把 API
        // 端口 / 凭据全错配(实测:Tauri 注入 data_dir 用默认 62581,但老 yaml
        // 里 API 是个旧默认(此前出现过 7861),Tauri 探 62581 永远 404)。设这个
        // env 跳过 state.json 优先级,真正让 Tauri 选定的 data_dir 生效。
        // 注意:62581 是约定端口,看到 7861 一律视作历史残留要修。
        .env("CHAYUAN_ROOT_IGNORE_STATE", "1")
        .env("CHAYUAN_ROOT", &args.data_dir)
        .env("CHAYUAN_PROFILE", "single-machine")
        .env("CHAYUAN_AUTH", "anonymous")
        .env("CHAYUAN_REDIS", "disabled")
        .env("CHAYUAN_QUEUE", "inproc")
        .env("CHAYUAN_VECTOR_STORE", "sqlite-vec");

    // CPU 变体覆盖:用户在「本地模型」设置页选了 win-x64-noavx / win-x64-avx512 等
    // 自定义平台后,Tauri 把值存进 vendor_platform_override 设置项,这里透传给 sidecar。
    // 让 local_runtime.py:_platform_subdir_candidates() 走 env 路径,跳自动检测。
    // 来源 1:进程 env(开发机 / CI 调试)
    if let Ok(v) = std::env::var("CHAYUAN_VENDOR_PLATFORM") {
        let v = v.trim();
        if !v.is_empty() {
            cmd = cmd.env("CHAYUAN_VENDOR_PLATFORM", v);
        }
    }
    // 来源 2:start args 字段(前端设置页传过来)
    if let Some(v) = args.vendor_platform.as_ref() {
        let v = v.trim();
        if !v.is_empty() {
            cmd = cmd.env("CHAYUAN_VENDOR_PLATFORM", v);
        }
    }

    let (mut rx, child) = cmd
        .spawn()
        .map_err(|e| format!("spawn sidecar: {e}"))?;
    state.store_child(child);

    // ── 2. 后台线程读 stdout/stderr,Terminated 时切状态 ─────────────
    // Arc::clone(&*state) —— 通过 Deref 拿到 &Arc<SidecarManager> 再 clone 出
    // 独立的 Arc。注意:**不能**写 state.inner() —— Tauri 自带的
    // ``State::inner(&self) -> &T`` 名字跟我们之前的 ManagerExt 撞车,
    // method resolution 优先选 inherent,返的是借用,带 state 生命周期,
    // 进 thread::spawn 的 'static 闭包就抛 E0521。
    let app_h = app.clone();
    let mgr: Arc<SidecarManager> = Arc::clone(&*state);
    thread::spawn(move || {
        // Tauri 2 的 CommandEvent receiver 是 tokio mpsc;此处用 blocking_recv
        // 避免在主线程跑 tokio runtime 的复杂度。
        while let Some(ev) = rx.blocking_recv() {
            match ev {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    let s = String::from_utf8_lossy(&line).trim().to_string();
                    if !s.is_empty() {
                        mgr.set_log_line(s.clone());
                        // 同步 emit 给前端,banner 实时滚动启动日志
                        let _ = app_h.emit("cy:sidecar-log", s);
                    }
                }
                CommandEvent::Terminated(payload) => {
                    let cur = mgr.snapshot();
                    // 已 ready 后被杀:正常关闭路径(用户关窗触发 chayuan_sidecar_kill)
                    let next = if cur.state == SidecarState::Ready {
                        SidecarState::Stopped
                    } else {
                        SidecarState::Failed
                    };
                    let err = format!(
                        "sidecar exited code={:?} signal={:?}",
                        payload.code, payload.signal
                    );
                    mgr.set_state(next.clone(), Some(err));
                    let _ = app_h.emit("cy:sidecar-state", next);
                    break;
                }
                _ => {}
            }
        }
    });

    // ── 3. 后台线程 polling /healthz ───────────────────────────────
    let app_h2 = app.clone();
    let mgr2: Arc<SidecarManager> = Arc::clone(&*state);
    thread::spawn(move || {
        let started = std::time::Instant::now();
        loop {
            if started.elapsed() > HEALTH_TOTAL_DEADLINE {
                mgr2.set_state(
                    SidecarState::Failed,
                    Some(format!(
                        "/healthz 在 {}s 内未返 200",
                        HEALTH_TOTAL_DEADLINE.as_secs()
                    )),
                );
                let _ = app_h2.emit("cy:sidecar-state", SidecarState::Failed);
                return;
            }
            // 已被外部切到 stopped/failed → 终止 poll
            let cur_state = mgr2.snapshot().state;
            if matches!(cur_state, SidecarState::Stopped | SidecarState::Failed) {
                return;
            }
            if poll_healthz_once(port) {
                mgr2.set_state(SidecarState::Ready, None);
                let _ = app_h2.emit("cy:sidecar-state", SidecarState::Ready);
                return;
            }
            thread::sleep(HEALTH_POLL_INTERVAL);
        }
    });

    Ok(state.snapshot())
}

#[tauri::command]
pub fn chayuan_sidecar_status(
    state: tauri::State<'_, Arc<SidecarManager>>,
) -> SidecarStatus {
    state.snapshot()
}

/// 主动停(关窗 / 切换数据目录向导)。已停为 no-op。
#[tauri::command]
pub fn chayuan_sidecar_kill(
    state: tauri::State<'_, Arc<SidecarManager>>,
) -> Result<SidecarStatus, String> {
    if let Some(child) = state.take_child() {
        // CommandChild::kill 在 tauri-plugin-shell 内部 SIGKILL;Phase 4 可换成
        // SIGTERM + 3s grace + SIGKILL,这里先简单实现。
        let _ = child.kill();
    }
    state.set_state(SidecarState::Stopped, None);
    Ok(state.snapshot())
}

