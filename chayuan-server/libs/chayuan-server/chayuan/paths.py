"""察元AI助手数据目录（``CHAYUAN_ROOT``）解析策略。

设计要点
========

* **绝不再以 cwd 作为默认值**。cwd 依赖调用者当时所在位置，是最大的"为什么
  配置文件跑到奇怪位置"类 bug 来源。
* 参考 Poetry / gh CLI / platformdirs 的做法：尊重 ``$XDG_DATA_HOME``，
  然后按操作系统给出各平台推荐的「用户持久数据目录」：

  ==========  =============================================================
  平台        默认 ``CHAYUAN_ROOT``
  ----------  -------------------------------------------------------------
  macOS       ``~/Library/Application Support/chayuan``
  Windows     ``%APPDATA%\\chayuan``（无 APPDATA 时回退 ``~\\AppData\\Roaming\\chayuan``）
  Linux/其它  ``$XDG_DATA_HOME/chayuan`` → 默认 ``~/.local/share/chayuan``
  ==========  =============================================================

优先级（高→低）
----------------
1. ``$CHAYUAN_ROOT``（显式，任意平台）
2. ``$XDG_DATA_HOME/chayuan``（显式设置了 XDG 的用户，跨平台尊重）
3. OS 默认（见上表）

返回值
------
:func:`resolve_chayuan_root` 返回 ``(path, source)``：``source`` 为
``"env" | "xdg" | "macos" | "windows" | "linux"``，便于 CLI / 面板回显
"当前路径来自哪里"。

本模块**不依赖** :mod:`chayuan.settings`，也不做任何 I/O（``mkdir`` 等
由调用方负责），方便写单测。
"""
from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


APP_NAME = "chayuan"


@dataclass(frozen=True)
class RootInfo:
    """解析结果。

    Attributes:
        path: 已 ``expanduser().resolve()`` 的绝对路径。
        source: 来源，用于 UI 回显。取值：``env`` / ``xdg`` / ``macos``
            / ``windows`` / ``linux``。
        reason: 面向人的简短说明。
    """

    path: Path
    source: str
    reason: str

    @property
    def is_default(self) -> bool:
        return self.source != "env"


def _norm(p: Path) -> Path:
    return Path(os.path.expanduser(str(p))).resolve()


def _default_for_platform(system: Optional[str] = None,
                          home: Optional[Path] = None,
                          env: Optional[dict] = None) -> Tuple[Path, str, str]:
    """基于 OS 计算默认路径。参数全部可注入，方便单测。"""
    system = (system if system is not None else platform.system()) or ""
    env = env if env is not None else os.environ
    home = home if home is not None else Path.home()

    if system == "Darwin":
        return (
            home / "Library" / "Application Support" / APP_NAME,
            "macos",
            "macOS 默认：~/Library/Application Support/chayuan",
        )

    if system == "Windows":
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return (
            base / APP_NAME,
            "windows",
            "Windows 默认：%APPDATA%\\chayuan",
        )

    # Linux / BSD / 其它类 Unix
    xdg = env.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return (Path(xdg) / APP_NAME, "xdg", f"$XDG_DATA_HOME/{APP_NAME}")
    return (
        home / ".local" / "share" / APP_NAME,
        "linux",
        "Linux 默认（XDG）：~/.local/share/chayuan",
    )


def resolve_chayuan_root(env: Optional[dict] = None,
                         system: Optional[str] = None,
                         home: Optional[Path] = None) -> RootInfo:
    """计算当前应使用的 CHAYUAN_ROOT。

    优先级（高→低）：

    1. 最近一次 ``chayuan init`` 向导写入 ``state.json`` 的 ``last_init_root``
       （只要目录还在就无脑用它）——解决「用户 init 完忘了 source activate.sh、
       旧 shell 里 $CHAYUAN_ROOT 还是老值」的常见问题；
    2. ``$CHAYUAN_ROOT``（显式）；
    3. ``$XDG_DATA_HOME/chayuan``；
    4. OS 默认。

    想跳过 1（例如临时想切到别的目录跑测试），设置 ``CHAYUAN_ROOT_IGNORE_STATE=1``
    即可退回到 2-4 的旧行为；或重新跑 ``chayuan init`` 把 state.json 刷成新路径。

    参数全部可注入，默认读真实 ``os.environ`` / ``platform.system()`` /
    ``Path.home()``，方便单元测试覆盖不同 OS 分支。
    """
    env = env if env is not None else os.environ

    # 1) state.json.last_init_root —— 用户最近一次 init 的选择，最高优先级。
    # read_user_state() 本身吞掉 JSON 错误返回 {}，这里再包一层 try 防御下文件系统异常。
    if not (env.get("CHAYUAN_ROOT_IGNORE_STATE") or "").strip():
        try:
            state = read_user_state()
        except Exception:
            state = {}
        last = (state.get("last_init_root") or "").strip() if isinstance(state, dict) else ""
        if last:
            p = _norm(Path(last))
            if p.exists():
                recorded = state.get("last_init_at") or "时间未知"
                return RootInfo(
                    path=p,
                    source="init",
                    reason=f"最近一次 `chayuan init` 选择（{recorded}）",
                )
            # 目录被用户挪走/删了——继续走下面的回退逻辑，避免把服务卡死。

    explicit = (env.get("CHAYUAN_ROOT") or "").strip()
    if explicit:
        return RootInfo(
            path=_norm(Path(explicit)),
            source="env",
            reason="来自环境变量 $CHAYUAN_ROOT",
        )

    xdg = (env.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return RootInfo(
            path=_norm(Path(xdg) / APP_NAME),
            source="xdg",
            reason="来自环境变量 $XDG_DATA_HOME",
        )

    path, source, reason = _default_for_platform(system=system, home=home, env=env)
    return RootInfo(path=_norm(path), source=source, reason=reason)


def ensure_root(info: Optional[RootInfo] = None) -> RootInfo:
    """确保目录存在（``mkdir -p``），返回解析结果。

    仅做目录创建；不拷贝默认 yaml（那是 ``chayuan init`` 的职责）。
    """
    info = info if info is not None else resolve_chayuan_root()
    try:
        info.path.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 尊重只读环境（例如 CI / 容器只读 rootfs）：不抛错，调用方自行处理。
        pass
    return info


def describe(info: RootInfo) -> str:
    """人类可读的一行描述，用于 CLI / loguru 打印。"""
    if info.source == "env":
        return f"CHAYUAN_ROOT={info.path}（{info.reason}）"
    return (
        f"CHAYUAN_ROOT={info.path}（未设置 $CHAYUAN_ROOT，"
        f"使用{info.reason}；如需切换请 `export CHAYUAN_ROOT=...` 或运行 `chayuan init`）"
    )


# ---------------------------------------------------------------------------
# 用户级状态文件（跨 shell、跨 CHAYUAN_ROOT）
#
# 主要诉求：向导 `chayuan init` 结束后，下一次启动时即便用户忘了 `export
# CHAYUAN_ROOT=...`，程序仍然能「知道上一次向导实际落到了哪个目录」，从而：
#   - CLI 在 start 前给出明显 warning；
#   - 配置面板的数据目录页可以高亮「向导选择 vs 当前进程实际使用」的不一致。
#
# 放在**用户级别**的配置目录，不放进 CHAYUAN_ROOT 本身——因为"路径不一致"这个
# 问题本质上就来自「CHAYUAN_ROOT 变了」，状态文件自己也会跟着漂走。
# ---------------------------------------------------------------------------


def _user_state_dir(env: Optional[dict] = None,
                    home: Optional[Path] = None,
                    system: Optional[str] = None) -> Path:
    env = env if env is not None else os.environ
    home = home if home is not None else Path.home()
    system = (system if system is not None else platform.system()) or ""

    if system == "Darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if system == "Windows":
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return base / APP_NAME
    # Linux / 其它：尊重 XDG_CONFIG_HOME（Config），退回 ~/.config
    xdg_cfg = (env.get("XDG_CONFIG_HOME") or "").strip()
    base = Path(xdg_cfg) if xdg_cfg else home / ".config"
    return base / APP_NAME


def user_state_path(name: str = "state.json") -> Path:
    """返回用户级状态文件的路径（不保证存在）。"""
    return _user_state_dir() / name


def read_user_state() -> dict:
    """读 ``state.json``；不存在 / 坏 json → 返回空 dict。永远不抛。"""
    import json
    p = user_state_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_user_state(updates: dict) -> None:
    """把 ``updates`` 浅合并进 ``state.json`` 并原子写回。失败仅打 stderr。"""
    import json
    import tempfile

    p = user_state_path()
    cur = read_user_state()
    cur.update(updates)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        sys.stderr.write(f"[chayuan][paths] 写 user state 失败：{type(e).__name__}: {e}\n")


# ---------------------------------------------------------------------------
# 运行时配置目录(<CHAYUAN_ROOT>/runtime/)
#
# 设计目的(35 题): 让所有运行时 yaml 文件 —— modality wrapper 配置(funasr /
# cosyvoice / voxcpm2 / rapidocr / paddleocr 等)、docker-compose 编排 ——
# 统一存放在用户已配置的 CHAYUAN_ROOT 下,与 *_settings.yaml 同根。
#
# 历史: 1.x 时期这些 yaml 散落到硬编码的 ``~/.chayuan/runtime/``,
# 与 CHAYUAN_ROOT 分裂。本模块在首次启动时把老路径里的 yaml 文件
# 一次性迁移到新位置(已存在的不覆盖),并写 ``.migrated`` marker。
#
# 优先级:
#   1. 环境变量 ``$CHAYUAN_RUNTIME_HOME``(显式覆盖,用于容器/CI)
#   2. ``<CHAYUAN_ROOT>/runtime/``(新统一布局)
# ---------------------------------------------------------------------------

RUNTIME_DIRNAME = "runtime"
COMPOSE_DIRNAME = "compose"
_LEGACY_RUNTIME_DIR = Path.home() / ".chayuan" / "runtime"
_MIGRATION_MARKER = ".migrated_from_dot_chayuan"


def runtime_config_dir(env: Optional[dict] = None) -> Path:
    """统一的 runtime 配置目录: ``<CHAYUAN_ROOT>/runtime/``。

    优先级:
      1. ``$CHAYUAN_RUNTIME_HOME``(显式)
      2. ``<CHAYUAN_ROOT>/runtime/``(默认)

    首次启动会把 ``~/.chayuan/runtime/`` 里残留的 yaml 文件一次性
    迁移到新目录(已存在的不覆盖),保证 1.x → 2.x 升级零数据丢失。
    """
    env = env if env is not None else os.environ
    explicit = (env.get("CHAYUAN_RUNTIME_HOME") or "").strip()
    if explicit:
        p = _norm(Path(explicit))
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return p

    info = resolve_chayuan_root(env=env)
    p = info.path / RUNTIME_DIRNAME
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        return p

    _migrate_legacy_runtime_dir(p)
    return p


def runtime_config_path(name: str) -> Path:
    """指定文件在 runtime 配置目录里的绝对路径。"""
    return runtime_config_dir() / name


def compose_config_dir(env: Optional[dict] = None) -> Path:
    """docker-compose 编排文件的**专属**目录: ``<CHAYUAN_ROOT>/compose/``。

    与 modality wrapper 配置 ``runtime/`` 分开,因为:
      * compose 文件是声明式容器编排(端口 / 镜像 / 网络),
        而 runtime/*.yaml 是 Python 进程参数(model_path / device 等)
      * 用户改 compose 直接影响 docker daemon,改 runtime/*.yaml 影响 wrapper
      * 备份 / 迁移时分类清楚

    优先级:
      1. ``$CHAYUAN_COMPOSE_HOME``(显式整目录覆盖,容器 / CI 用)
      2. ``<CHAYUAN_ROOT>/compose/``(默认)

    单文件覆盖看 :func:`compose_config_path` 的注释。
    """
    env = env if env is not None else os.environ
    explicit = (env.get("CHAYUAN_COMPOSE_HOME") or "").strip()
    if explicit:
        p = _norm(Path(explicit))
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return p

    info = resolve_chayuan_root(env=env)
    p = info.path / COMPOSE_DIRNAME
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def compose_config_path(name: str = "docker-compose.yaml") -> Path:
    """``<CHAYUAN_ROOT>/compose/<name>`` 的绝对路径。"""
    return compose_config_dir() / name


def _migrate_legacy_runtime_dir(new_dir: Path) -> None:
    """把 ``~/.chayuan/runtime/`` 里的 yaml 一次性迁到新目录。

    * 只迁 ``*.yaml`` / ``*.yml``(避免拷贝 cache / log)
    * 已在新位置存在的文件不覆盖(用户改过的优先)
    * 迁完写 ``.migrated_from_dot_chayuan`` marker,下次启动跳过
    * 不删老目录 —— 用户可自行清理
    * 任何 I/O 错误都吞掉,不影响启动
    """
    marker = new_dir / _MIGRATION_MARKER
    if marker.exists():
        return
    legacy = _LEGACY_RUNTIME_DIR
    if not legacy.exists() or _norm(legacy) == _norm(new_dir):
        try:
            marker.write_text("nothing to migrate\n", encoding="utf-8")
        except OSError:
            pass
        return

    import shutil
    copied = []
    try:
        entries = list(legacy.iterdir())
    except OSError:
        return
    for src in entries:
        try:
            if not src.is_file():
                continue
            if src.suffix.lower() not in (".yaml", ".yml"):
                continue
            dst = new_dir / src.name
            if dst.exists():
                continue
            shutil.copy2(src, dst)
            copied.append(src.name)
        except OSError:
            continue
    try:
        if copied:
            marker.write_text(
                f"migrated from {legacy} on first run\n"
                f"files: {', '.join(copied)}\n",
                encoding="utf-8",
            )
        else:
            marker.write_text("checked, nothing to migrate\n", encoding="utf-8")
    except OSError:
        pass


__all__ = [
    "APP_NAME",
    "RootInfo",
    "resolve_chayuan_root",
    "ensure_root",
    "describe",
    "user_state_path",
    "read_user_state",
    "write_user_state",
    "RUNTIME_DIRNAME",
    "runtime_config_dir",
    "runtime_config_path",
    "COMPOSE_DIRNAME",
    "compose_config_dir",
    "compose_config_path",
]
