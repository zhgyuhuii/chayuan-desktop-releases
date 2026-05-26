"""安装任务全局管理器 —— 关闭弹窗不杀进程的关键。

为什么单建?
==========

老版 `_spawn_install_async` 把 ``_install_state`` 当成模块级 dict,但
`_open_install_dialog` 里 ``ui.timer`` 在 dialog 关闭时会被 NiceGUI 销毁,
日志轮询断开,新弹窗看不到进度。**进程没死,但视图死了**。

设计:
* :class:`InstallTask` 不可变于"已开始"—— task_id 一经创建就稳定
* :class:`InstallTaskManager` 单例;弹窗只是 *视图*,不持有 task 状态
* 状态机: ``pending → running → done | failed | cancelled``
* 关闭弹窗不动 task;再开弹窗 `manager.attach(framework_name)` 拿到当前 task
* 终止: ``manager.cancel(task_id)`` 通过 cancel_event + Popen.terminate

多镜像源
=========

``InstallTask.cmd`` 只是当前正在跑的命令;``recipes`` 才是"还可以试什么"。
重试时换 recipe 重新 spawn,task_id 沿用。

线程安全
=========

* ``_lock`` 保护所有 mutation
* 日志 buffer 限 500 行 ring buffer
* cancel_event 用于子进程协作终止;暴力 kill 在 join timeout 后

新旧路径(42 题 Phase 5 之后)
=============================

本模块是**通用安装/启停任务的执行器**。docker 类 framework 现在有两条路径:

  * **老路径(本模块)**:``_START_RECIPES["vllm"]`` 等 + thread + Popen + 流式日志。
    ``runtime_framework_panel`` 的"启动 ▶"小按钮,以及 install_dialog 的"安装"按钮
    都走这里。优势是流程统一(任意 cmd 都能跑),日志能 attach 重看。
  * **新路径(Phase 5)**:``install_dialog._render_docker_deploy_section`` 的
    "🚀 一键部署"按钮,直接调 ``ContainerLifecycle.up(wait_healthy=True)`` +
    ``auto_register.register_after_healthy()``。优势是健康等待 + 自动注册模型,
    用户体验更顺。但仅对 5 个 docker service 可用。

非 docker framework(funasr / cosyvoice / voxcpm2 / rapidocr / paddleocr / ollama)
**只走老路径**(本模块)— 它们没有 compose yaml,无需 ContainerLifecycle。

两条路径**互不冲突**,docker 类 framework 用户可以选任一种,效果都是启动容器。
后续(可选)可让"启动 ▶"小按钮也走 ContainerLifecycle.up — 但需保证日志兼容。
"""
from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.config_panel.install_task_manager")


@dataclass
class InstallRecipe:
    """一个具体的安装命令配方。"""

    label: str       # UI 显示, 如 "官方 (curl)" / "国内镜像 (DaoCloud)"
    cmd: List[str]   # subprocess.Popen args
    note: str = ""   # 简短说明
    requires: str = ""  # 依赖, 如 "docker" / "winget" / "brew"


@dataclass
class InstallTask:
    task_id: str
    framework: str
    recipe_label: str
    cmd: List[str]
    state: str = "pending"      # pending / running / done / failed / cancelled
    log: List[str] = field(default_factory=list)
    return_code: Optional[int] = None
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "framework": self.framework,
            "recipe_label": self.recipe_label,
            "state": self.state,
            "log": list(self.log),
            "return_code": self.return_code,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def is_terminal(self) -> bool:
        return self.state in ("done", "failed", "cancelled")


class InstallTaskManager:
    """进程内单例。"""

    _LOG_CAP = 500

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # framework_name → 当前活跃 task_id (一个 framework 同时只允许一个安装任务)
        self._active: Dict[str, str] = {}
        self._tasks: Dict[str, InstallTask] = {}
        self._procs: Dict[str, subprocess.Popen] = {}

    # ---- 查询 ----

    def get(self, task_id: str) -> Optional[InstallTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def attach(self, framework: str) -> Optional[InstallTask]:
        """拿到该 framework 当前活跃 task (供新打开弹窗)。"""
        with self._lock:
            tid = self._active.get(framework)
            if not tid:
                return None
            t = self._tasks.get(tid)
            # 已 terminal 的 task 也保留 30 分钟,让用户能看到结果
            return t

    def list_active(self) -> List[InstallTask]:
        with self._lock:
            return [t for t in self._tasks.values() if not t.is_terminal()]

    # ---- 写 ----

    def start(self, *, framework: str, recipe: InstallRecipe) -> InstallTask:
        """启动一个新 task;若该 framework 已有 active task,**复用** task_id。"""
        with self._lock:
            existing_id = self._active.get(framework)
            if existing_id:
                existing = self._tasks.get(existing_id)
                if existing and not existing.is_terminal():
                    logger.info("install task for %s already running: %s", framework, existing_id)
                    return existing
            task_id = uuid.uuid4().hex[:12]
            # 命令拦截:占位 ``docker compose <op> <service>`` 注入真实 -f <path>
            real_cmd = _maybe_inject_compose_file(list(recipe.cmd))
            task = InstallTask(
                task_id=task_id,
                framework=framework,
                recipe_label=recipe.label,
                cmd=real_cmd,
            )
            self._tasks[task_id] = task
            self._active[framework] = task_id

        threading.Thread(
            target=self._run, args=(task,),
            daemon=True, name=f"install-{framework}-{task_id[:6]}",
        ).start()
        return task

    def stop_service(self, *, framework: str) -> Optional[InstallTask]:
        """停止一个**正在运行**的服务(给已运行卡片"⏹"按钮用)。

        镜像 :meth:`start_service`:先查 ``_STOP_RECIPES``,再 ``_derive_stop_recipe``
        兜底;两者都找不到返回 None。
        """
        recipe = _STOP_RECIPES.get(framework) or _derive_stop_recipe(framework)
        if recipe is None:
            return None
        return self.start(framework=f"stop-{framework}", recipe=recipe)

    def start_service(self, *, framework: str) -> Optional[InstallTask]:
        """启动一个**已安装但未运行**的服务。

        与 ``start`` 不同:``start`` 跑 *安装* 命令(curl install.sh / pip install /
        docker run 一次性);``start_service`` 跑 *daemon* 命令(systemctl /
        docker start 已有容器 / nohup 二进制 &)。

        优先级:
        1. 显式 :data:`_START_RECIPES` 表(已知 framework 的最佳启动方式)
        2. 自动派生:按 install_kind 推断
           - kind=docker → ``docker start <container_name>``
           - kind=pip 且有 bin_names → ``nohup <bin> serve``
           - kind=manual + Docker 自身 → ``systemctl start docker``(linux) / 启动 Desktop(mac/win)
        3. 都派生不出 → 返回 None,UI 应提示用户去安装弹窗看
        """
        recipe = _START_RECIPES.get(framework) or _derive_start_recipe(framework)
        if recipe is None:
            return None
        return self.start(framework=f"start-{framework}", recipe=recipe)

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            proc = self._procs.get(task_id)
        if task is None or task.is_terminal():
            return False
        task.cancel_event.set()
        if proc is not None:
            try:
                # 先温柔 terminate, 等 3s 不响应再 kill
                if hasattr(proc, "send_signal"):
                    try:
                        proc.send_signal(signal.SIGTERM)
                    except Exception:  # noqa: BLE001
                        proc.terminate()
                else:
                    proc.terminate()
            except Exception as e:  # noqa: BLE001
                logger.warning("terminate proc failed: %s", e)
        return True

    # ---- worker ----

    def _run(self, task: InstallTask) -> None:
        try:
            with self._lock:
                task.state = "running"
                task.log.append(f"[recipe] {task.recipe_label}")
                task.log.append(f"[cmd] {' '.join(shlex.quote(s) for s in task.cmd)}")

            # 子进程输出统一按 UTF-8 解码,errors='replace' 容错。
            # 不能依赖平台默认编码:中文 Windows 默认 GBK(cp936),pip / wheel
            # 名 / 进度条含非 GBK 字节时 text=True 会抛 UnicodeDecodeError 崩 runner。
            # PYTHONIOENCODING=utf-8 让被调子进程(尤其 chayuan-server.exe -m pip)
            # 也按 UTF-8 输出,双保险。
            child_env = dict(os.environ)
            child_env["PYTHONIOENCODING"] = "utf-8"

            # 启动进程
            try:
                proc = subprocess.Popen(  # noqa: S603
                    task.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                    encoding="utf-8", errors="replace",
                    env=child_env,
                    # Windows 下不开 console 窗口
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW") and os.name == "nt"
                    else 0,
                )
            except FileNotFoundError as e:
                with self._lock:
                    task.state = "failed"
                    task.error = f"命令不存在: {e}"
                    task.finished_at = time.time()
                return
            with self._lock:
                self._procs[task.task_id] = proc

            assert proc.stdout is not None
            for line in proc.stdout:
                if task.cancel_event.is_set():
                    break
                with self._lock:
                    task.log.append(line.rstrip()[:400])
                    if len(task.log) > self._LOG_CAP:
                        del task.log[: len(task.log) - self._LOG_CAP]

            # 等 proc 结束;若 cancel 已发,给 3s 然后 kill
            if task.cancel_event.is_set():
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3.0)
            rc = proc.wait()

            with self._lock:
                task.return_code = rc
                task.finished_at = time.time()
                if task.cancel_event.is_set():
                    task.state = "cancelled"
                    task.log.append(f"[end] cancelled by user (rc={rc})")
                elif rc == 0:
                    task.state = "done"
                    task.log.append("[end] done")
                else:
                    task.state = "failed"
                    task.log.append(f"[end] failed (rc={rc})")
                # 终态消息也参与 ring buffer 上限,避免日志比 LOG_CAP 多 1 行
                if len(task.log) > self._LOG_CAP:
                    del task.log[: len(task.log) - self._LOG_CAP]

        except Exception as e:  # noqa: BLE001
            logger.exception("install task crashed")
            with self._lock:
                task.state = "failed"
                task.error = str(e)
                task.finished_at = time.time()
                task.log.append(f"[end] crash: {e}")
                if len(task.log) > self._LOG_CAP:
                    del task.log[: len(task.log) - self._LOG_CAP]
        finally:
            with self._lock:
                self._procs.pop(task.task_id, None)


def _bg_cmd(args: List[str], log_path: str) -> List[str]:
    """跨平台地"后台启动一个命令并把输出重定向到 log"。

    Linux/macOS: ``bash -lc 'nohup <args> > <log> 2>&1 &'``
    Windows:    Python 内联 helper(``python -c '...'``)启 detached 进程

    历史:
      * 16 题:Windows 改 ``cmd /c start /B "" ... > log`` 替代 bash nohup
      * **46 题(本次)**:用户在 Windows 报 "找不到 \\\\文件 请确定拼写...",
        根因 ``cmd start`` 的命令行解析对反斜杠 / UNC / 重定向 ``>`` 行为不可靠
        (尤其 sys.executable 路径含反斜杠时)。改用 Python 内联 helper:
        ``[sys.executable, "-c", "import subprocess; ..."]`` 直接 spawn
        ``subprocess.Popen(..., creationflags=DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP)``,
        完全绕过 cmd shell,无任何转义 / quoting / 重定向坑。

    Args:
        args: 实际执行的程序及参数,如 ``["infinity_emb", "v2", "--port", "7997"]``
        log_path: 输出 log 的路径(平台已规范化的)
    """
    if os.name == "nt":
        return _bg_cmd_python_inline(args, log_path)

    # Linux / macOS / WSL — bash nohup 已稳定,继续用
    def _q(s: str) -> str:
        if any(c in s for c in (" ", "\t", "&", "|", "<", ">", "(", ")")):
            return f'"{s}"'
        return s
    cmd_str = " ".join(_q(a) for a in args)
    return ["bash", "-lc", f"nohup {cmd_str} > {_q(log_path)} 2>&1 &"]


def _bg_cmd_python_inline(args: List[str], log_path: str) -> List[str]:
    """46 题:跨平台 Python 内联 helper,绕过 shell quoting / 重定向坑。

    生成的命令:::

        python -c '<helper code>'

    helper code 用 ``subprocess.Popen`` 起后台 detached 进程,把 stdout/stderr
    重定向到 log_path,然后立刻退出。install_task_manager 的 outer Popen
    等到 helper 退出(几十毫秒),task 状态就 done。后台 service 持续跑。

    主要在 Windows 用,因为 Linux/macOS 的 bash nohup 已稳;但本函数本身
    是跨平台的(detached flag 在非 Windows 时不传)— 可以在调试 Linux
    问题时用。
    """
    # 用 repr() 让 list[str] 直接嵌入 Python 源,不需要手动 quoting
    code = (
        "import os, sys, subprocess; "
        f"log = open({log_path!r}, 'ab'); "
        f"flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) "
        f"if os.name == 'nt' else 0; "
        f"subprocess.Popen({args!r}, stdout=log, stderr=log, close_fds=True, "
        f"creationflags=flags, "
        f"start_new_session=(os.name != 'nt')); "
        "log.close()"
    )
    return [sys.executable, "-c", code]


def _maybe_inject_compose_file(cmd: List[str]) -> List[str]:
    """如果 cmd 是 ``docker compose <op> ...``,注入 ``-f <compose_yaml>``。

    简单字符串替换 — 占位 cmd 写成 ``docker compose op service`` 没带 -f,
    本函数把真实 yaml 路径插进去。这样 _START_RECIPES 静态定义,运行时按用户
    实际 ``CHAYUAN_DOCKER_COMPOSE_FILE`` 路径 / 默认路径动态拼。
    """
    if len(cmd) < 3 or cmd[0] != "docker" or cmd[1] != "compose":
        return cmd  # 非 compose 命令,原样
    # 已经有 -f 的不动
    if "-f" in cmd[:5]:
        return cmd
    try:
        from chayuan.server.config_panel.compose_manager import ensure_compose_file
        compose_yaml = str(ensure_compose_file())
    except Exception:  # noqa: BLE001
        return cmd  # compose_manager 不可用 — 退化到无 -f(用 cwd 默认)
    # 在 'compose' 后插入 -f <path>
    return [cmd[0], cmd[1], "-f", compose_yaml] + list(cmd[2:])


def _bg_log_path(name: str) -> str:
    """跨平台 log 路径。Windows 用 %TEMP% / %LOCALAPPDATA%,*nix 用 /tmp。"""
    if os.name == "nt":
        # %TEMP% 通常是 C:\Users\<user>\AppData\Local\Temp
        return os.path.join(os.environ.get("TEMP") or os.environ.get("TMP")
                            or "C:\\Windows\\Temp", f"chayuan_{name}.log")
    return f"/tmp/chayuan_{name}.log"


# 各 framework 的启动 (daemon) 命令 — 用于"已安装·未启动" → 点 ▶ 的场景。
# 实现思路: 优先 docker start (容器已存在但停了),其次本地二进制后台跑。
#
# 关键:**所有 nohup/bash 启动方式都通过 _bg_cmd 包装跨平台**,Windows 上自动转成
# cmd /c "start /B ..." 而不是 bash -lc(Windows 上 bash 不存在)。
#
# 这张表是"最佳路径";没命中时 _derive_start_recipe 按 spec 自动派生兜底,
# 保证 14 个框架都能一键启。
_START_RECIPES: Dict[str, InstallRecipe] = {
    "ollama": InstallRecipe(
        label="ollama serve (后台)",
        cmd=_bg_cmd(["ollama", "serve"], _bg_log_path("ollama")),
        note=f"后台运行;日志 {_bg_log_path('ollama')}",
        requires="ollama",
    ),
    # docker 类 framework 走 compose 统一管理 — 端口 / 容器名 / 镜像都从
    # <CHAYUAN_ROOT>/compose/docker-compose.yaml 读;首次启动自动生成默认 yaml
    # 这里 cmd 占位,真正命令在 _build_compose_recipe() 动态生成(运行时读 yaml)
    "vllm": InstallRecipe(
        label="docker compose up vllm",
        cmd=["docker", "compose", "up", "-d", "vllm"],
        note="走 compose 配置;端口/镜像从 yaml 读 — 运行时会 -f 注入真实路径",
        requires="docker",
    ),
    # Infinity: pip 包 infinity-emb 装上后用 ``infinity_emb v2`` 起 daemon;
    # docker 启动方案在 _START_RECIPES_EXTRA(由弹窗 chip 选择)
    # Infinity:走 compose 优先(端口/镜像从 yaml 读);pip 方式作为备选(EXTRA)
    "infinity": InstallRecipe(
        label="docker compose up infinity",
        cmd=["docker", "compose", "up", "-d", "infinity"],
        note="走 compose 配置;端口/镜像从 yaml 读",
        requires="docker",
    ),
    # ComfyUI:走 compose 优先;源码方式作为备选(EXTRA)
    "comfyui": InstallRecipe(
        label="docker compose up comfyui",
        cmd=["docker", "compose", "up", "-d", "comfyui"],
        note="走 compose 配置;源码方式见弹窗里的'源码启动'选项",
        requires="docker",
    ),
    "llamacpp": InstallRecipe(
        label="docker compose up llamacpp",
        cmd=["docker", "compose", "up", "-d", "llamacpp"],
        note="走 compose 配置",
        requires="docker",
    ),
    "docker": InstallRecipe(
        # Linux: systemctl start docker;Win/Mac 需手动开 Docker Desktop
        # 这里给 *nix 命令;Win 上 _recipe_runnable 会判 systemctl 不存在 → 拒跑
        label="systemctl start docker (Linux only)",
        cmd=(["bash", "-lc", "sudo systemctl start docker"]
             if os.name != "nt"
             else ["cmd", "/c", "echo Docker Desktop must be started manually on Windows && exit 1"]),
        note="Linux only;Mac/Win 需手动启动 Docker Desktop",
        requires="docker",
    ),
    # FunASR / CosyVoice / RapidOCR / PaddleOCR 都是 pip 包但**没 CLI script**;
    # bin_names=("funasr",) 实际不是真二进制 → nohup funasr 会 ENOENT。
    # 启动方式改成 python -m 进入 chayuan 内置 HTTP wrapper module。
    # 注意:相应的 wrapper module 需要由 chayuan 提供(后续做);暂时此 recipe
    # 在 wrapper 缺失时会失败,但**不会再"暂无启动配方"** — 用户能看到真错。
    # 4 个 modality wrapper 用 sys.executable -m <module> 跨平台;
    # 加 sys.executable 显式路径而非裸 "python",避免 conda 环境间 PATH 错位
    "funasr": InstallRecipe(
        label="python -m chayuan.server.modality.funasr_server",
        cmd=_bg_cmd(
            [sys.executable, "-m", "chayuan.server.modality.funasr_server",
             "--host", "127.0.0.1", "--port", "18180"],
            _bg_log_path("funasr"),
        ),
        note=f"启 chayuan 内置 FunASR HTTP 包装;日志 {_bg_log_path('funasr')}",
        requires="",  # sys.executable 是 abs 路径,which 检查不必要
    ),
    "cosyvoice": InstallRecipe(
        label="python -m chayuan.server.modality.cosyvoice_server",
        cmd=_bg_cmd(
            [sys.executable, "-m", "chayuan.server.modality.cosyvoice_server",
             "--host", "127.0.0.1", "--port", "18280"],
            _bg_log_path("cosyvoice"),
        ),
        requires="",
    ),
    "voxcpm2": InstallRecipe(
        label="python -m chayuan.server.modality.voxcpm2_server",
        cmd=_bg_cmd(
            [sys.executable, "-m", "chayuan.server.modality.voxcpm2_server",
             "--host", "127.0.0.1", "--port", "18580"],
            _bg_log_path("voxcpm2"),
        ),
        note=f"启 chayuan 内置 VoxCPM 2 HTTP 包装;日志 {_bg_log_path('voxcpm2')}",
        requires="",
    ),
    "rapidocr": InstallRecipe(
        label="python -m chayuan.server.modality.rapidocr_server",
        cmd=_bg_cmd(
            [sys.executable, "-m", "chayuan.server.modality.rapidocr_server",
             "--host", "127.0.0.1", "--port", "18380"],
            _bg_log_path("rapidocr"),
        ),
        requires="",
    ),
    "paddleocr": InstallRecipe(
        label="python -m chayuan.server.modality.paddleocr_server",
        cmd=_bg_cmd(
            [sys.executable, "-m", "chayuan.server.modality.paddleocr_server",
             "--host", "127.0.0.1", "--port", "18480"],
            _bg_log_path("paddleocr"),
        ),
        requires="",
    ),
    # whisper.cpp / piper 是 subprocess 工具,**不是 daemon**;
    # 这两个不该有"启动"按钮;但为了避免"暂无启动配方"提示,给一条无害命令。
    # 真实使用时由 modality service 子进程调用。
    "whispercpp": InstallRecipe(
        label="(subprocess 工具, 无需 daemon)",
        cmd=(["bash", "-lc", "echo whisper.cpp 由 modality 子进程按需调用"]
             if os.name != "nt"
             else ["cmd", "/c", "echo whisper.cpp 由 modality 子进程按需调用"]),
        note="ASR 调用时即起,无独立 daemon",
        requires="bash",
    ),
    "piper": InstallRecipe(
        label="(subprocess 工具, 无需 daemon)",
        cmd=(["bash", "-lc", "echo piper 由 modality 子进程按需调用"]
             if os.name != "nt"
             else ["cmd", "/c", "echo piper 由 modality 子进程按需调用"]),
        note="TTS 调用时即起,无独立 daemon",
        requires="",
    ),
}


# 同 framework 的多种启动方式(每条会作为一个独立 chip 让用户选)。
# 默认 _START_RECIPES[name] 是首选;_START_RECIPES_EXTRA[name] 是其它备选。
# 典型场景:同一服务可能是 docker / pip / 源码 三种装法,启动命令完全不同。
_START_RECIPES_EXTRA: Dict[str, List[InstallRecipe]] = {
    # ------------------------------------------------------------------
    # ComfyUI: 源码 git clone 后 ``python main.py``
    #   * 默认假设 clone 在 ~/comfyui 或 ./comfyui;允许用 CHAYUAN_COMFYUI_DIR 覆盖
    #   * --port 18188 与 _FrameworkSpec(comfyui).default_url 对齐
    # ------------------------------------------------------------------
    "comfyui": [
        # 源码启动 — 跨平台:用 sys.executable + os.path 找 ComfyUI 目录
        # *nix shell 的 ${VAR:-default} 语法在 Windows cmd 不可用,改用 Python launcher
        InstallRecipe(
            label="源码启动 (python main.py)",
            cmd=[
                sys.executable, "-c",
                # 一行 Python 完成:找目录 → cd → Popen(后台)
                "import os, sys, subprocess, pathlib; "
                "d = os.environ.get('CHAYUAN_COMFYUI_DIR') "
                "  or os.path.expanduser('~/comfyui'); "
                "d = d if pathlib.Path(d).is_dir() "
                "  else os.path.join(os.getcwd(), 'comfyui'); "
                "p = pathlib.Path(d); "
                "(print(f'[error] 找不到 ComfyUI 源码: {d}; "
                "set CHAYUAN_COMFYUI_DIR'), sys.exit(2)) if not p.is_dir() else None; "
                f"log = open(r'{_bg_log_path('comfyui')}', 'ab'); "
                "kw = {'stdout': log, 'stderr': subprocess.STDOUT, 'cwd': str(p)}; "
                "kw['creationflags'] = 0x208 if os.name == 'nt' else 0; "
                "kw['start_new_session'] = (os.name != 'nt'); "
                "subprocess.Popen([sys.executable, 'main.py', "
                "                   '--listen', '127.0.0.1', '--port', '18188'], **kw); "
                "print('ComfyUI started in background')",
            ],
            note=(f"从 ~/comfyui 或 ./comfyui 启动;可设 CHAYUAN_COMFYUI_DIR 指定目录;"
                  f"日志 {_bg_log_path('comfyui')}"),
            requires="",  # sys.executable 是绝对路径,不需 PATH 查
        ),
    ],
    # ------------------------------------------------------------------
    # Infinity: 用户也可能用 docker 跑(若 pip 装失败)
    # ------------------------------------------------------------------
    "infinity": [
        InstallRecipe(
            label="docker start infinity (容器方式)",
            cmd=["docker", "start", "infinity"],
            note="启动已存在的 infinity 容器",
            requires="docker",
        ),
    ],
}


def get_start_recipes(framework: str) -> List[InstallRecipe]:
    """所有可选的启动方式(主 + 备选)。无任何 recipe 返空 list。"""
    out: List[InstallRecipe] = []
    primary = _START_RECIPES.get(framework)
    if primary:
        out.append(primary)
    out.extend(_START_RECIPES_EXTRA.get(framework, []))
    return out


def _kill_cmd(pattern: str) -> List[str]:
    """跨平台地按进程名/命令行模式 kill 进程。

    *nix: pkill -f '<pattern>'
    Windows: taskkill /F /IM <name>.exe(用 pattern 第一个 token 当 IM 名)
             或者用 wmic + findstr 模糊匹配
    """
    if os.name == "nt":
        # Windows 没有 pkill;用 PowerShell Get-Process | Where 过滤
        # pattern 通常是 "ollama serve" / "infinity_emb" / "comfyui|ComfyUI"
        # 第一个空格前的 token 当进程名(去 .exe 后缀匹配)
        first = pattern.split()[0].split("|")[0]
        return ["powershell", "-NoProfile", "-Command",
                f"Get-Process -EA 0 | Where-Object {{ $_.ProcessName -like '*{first}*' }} | "
                f"Stop-Process -Force -EA 0"]
    return ["bash", "-lc", f"pkill -f '{pattern}' || true"]


# 各 framework 的停止命令(给"已运行 → ⏹"用)
_STOP_RECIPES: Dict[str, InstallRecipe] = {
    "ollama": InstallRecipe(
        label="kill ollama serve",
        cmd=_kill_cmd("ollama serve"),
        requires="",
    ),
    # docker 类 framework 走 compose stop(端口/容器名一致用 yaml 真源)
    "vllm": InstallRecipe(
        label="docker compose stop vllm",
        cmd=["docker", "compose", "stop", "vllm"],
        requires="docker",
    ),
    "infinity": InstallRecipe(
        label="docker compose stop infinity (+ kill pip 进程兜底)",
        cmd=["docker", "compose", "stop", "infinity"],
        note="docker 路径走 compose;若是 pip 启动用 pkill infinity_emb",
        requires="docker",
    ),
    "comfyui": InstallRecipe(
        label="docker compose stop comfyui (+ kill 源码进程兜底)",
        cmd=["docker", "compose", "stop", "comfyui"],
        note="docker 路径走 compose;源码启动用 pkill comfyui",
        requires="docker",
    ),
    "llamacpp": InstallRecipe(
        label="docker compose stop llamacpp",
        cmd=["docker", "compose", "stop", "llamacpp"],
        requires="docker",
    ),
    "docker": InstallRecipe(
        label="systemctl stop docker (Linux)",
        cmd=(["bash", "-lc", "sudo systemctl stop docker"]
             if os.name != "nt"
             else ["cmd", "/c", "echo Docker Desktop must be stopped manually on Windows && exit 1"]),
        note="Linux only;Mac/Win 需手动停 Docker Desktop",
        requires="docker",
    ),
}


def _derive_start_recipe(framework: str) -> Optional[InstallRecipe]:
    """没显式 _START_RECIPES 时按框架 spec 自动派生启动命令。

    策略:
    * 框架的 install_kind == "docker" → docker start <framework>
    * 框架有 bin_names → nohup <bin> &
    * 否则 None
    """
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            _FRAMEWORKS_BY_NAME,
        )
    except Exception:  # noqa: BLE001
        return None
    spec = _FRAMEWORKS_BY_NAME.get(framework)
    if spec is None:
        return None
    # 1) docker 安装 → docker start <framework>
    if spec.install_kind == "docker":
        return InstallRecipe(
            label=f"docker start {framework}",
            cmd=["docker", "start", framework],
            note=f"启动名为 {framework} 的容器(若不存在请先安装)",
            requires="docker",
        )
    # 2) 有 bin_names → nohup <bin> &
    bins = list(spec.bin_names or ())
    if bins:
        bin0 = bins[0]
        return InstallRecipe(
            label=f"{bin0} (后台 nohup)",
            cmd=["bash", "-lc", f"nohup {bin0} > /tmp/{framework}.log 2>&1 &"],
            note=f"日志在 /tmp/{framework}.log",
            requires=bin0,
        )
    return None


def _derive_stop_recipe(framework: str) -> Optional[InstallRecipe]:
    """同 ``_derive_start_recipe``,反过来。"""
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            _FRAMEWORKS_BY_NAME,
        )
    except Exception:  # noqa: BLE001
        return None
    spec = _FRAMEWORKS_BY_NAME.get(framework)
    if spec is None:
        return None
    if spec.install_kind == "docker":
        return InstallRecipe(
            label=f"docker stop {framework}",
            cmd=["docker", "stop", framework],
            requires="docker",
        )
    bins = list(spec.bin_names or ())
    if bins:
        bin0 = bins[0]
        return InstallRecipe(
            label=f"kill {bin0}",
            cmd=_kill_cmd(bin0),
            requires="",  # _kill_cmd 已封装平台差异
        )
    return None


_SINGLETON: Optional[InstallTaskManager] = None
_SINGLETON_LOCK = threading.Lock()


def get_install_manager() -> InstallTaskManager:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = InstallTaskManager()
        return _SINGLETON


__all__ = [
    "InstallRecipe",
    "InstallTask",
    "InstallTaskManager",
    "get_install_manager",
]
