"""模型设置页 · 顶部两行 NiceGUI 组件。

* :func:`render_runtime_framework_row` —— 11 张本项目支持的"模型框架"卡片
  （Ollama / vLLM / Infinity / ComfyUI / FunASR / Piper / CosyVoice /
   RapidOCR / PaddleOCR / llama.cpp / whisper.cpp）。

  每张卡显示：
    - 健康状态徽章（**4 档**）：
        running   —— HTTP 探针通，服务正在运行
        installed —— 本机能 ``which`` 到二进制，但 HTTP 不通
        configured —— 有 URL 但 HTTP 不通且没二进制
        missing   —— 啥都没有
    - 它能服务的 capability 标签（"对话 / 文本嵌入 / ..."）
    - 已经在它上面注册的本地模型数量
    - URL
    - "怎么装"按钮（→ 一键安装 Ollama / pip / Docker 命令）

* :func:`render_capability_defaults_row` —— 9 类 capability
  （chat / embedding / clip / rerank / t2i / t2v / tts / asr / ocr）
  的默认模型选择器。**写盘到 ``model_settings.yaml``**（不是只在内存）。

设计动机
--------

用户 2026-05-02 反馈：
  - "模型框架内没有卡片，无法点击"  → 之前用了 ``ui.grid(columns=3)`` +
    Quasar ``col-12``，部分 NiceGUI 版本下两套 grid 系统打架，子元素
    塌成 0 高度；现在改成纯 ``ui.row(wrap=True)`` + 显式宽度。
  - "本机安装的 ollama 也没有探测出来"  → 之前只 ping URL；如果服务
    没在跑就探不到，但 ``which ollama`` 是有的。现在加二进制兜底。
  - "9 大类型默认模型设置无法设置"  → 之前 ``set_default`` 只写内存；
    现在统一写 ``model_settings.yaml`` 的 ``DEFAULT_<CAP>_MODEL`` 字段，
    重启 / 重载页都保得住。
  - "下面的 默认模型 也应该去掉"  → 由 ``model_config.py`` 处理，删掉
    旧版 LLM / Embedding 双 select。
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("chayuan.config_panel.runtime_framework_panel")


# ---------------------------------------------------------------------------
# 词汇表 —— 与 chayuan_gateway.routers.admin._DEFAULTS_CAPABILITIES 完全一致
# ---------------------------------------------------------------------------

CAPABILITY_LABELS: List[Tuple[str, str]] = [
    ("chat", "对话"),
    ("embedding", "文本嵌入"),
    ("clip", "图像嵌入"),
    ("rerank", "重排"),
    ("t2i", "文生图"),
    ("t2v", "文生视频"),
    ("tts", "语音合成"),
    ("asr", "语音识别"),
    ("ocr", "图像识别文字"),
]
_CAP_TO_ZH: Dict[str, str] = dict(CAPABILITY_LABELS)


# ---------------------------------------------------------------------------
# 内置框架目录 —— 不依赖 chayuan_runtime registry 是否可用，**始终给 11 张卡**
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FrameworkSpec:
    name: str
    label: str
    capabilities: Tuple[str, ...]   # 内部 capability 词
    default_url: str                # 健康探针的"约定地址"
    health_path: str                # 拼到 default_url 后探活的路径
    install_kind: str               # one-click / pip / docker / manual
    install_pkg: Optional[str]      # pip 包名 / curl 脚本 etc.
    bin_names: Tuple[str, ...]      # PATH 上找这些 cli 即视为"已安装"
    needs_gpu: bool = False
    is_subprocess: bool = False     # piper / whisper.cpp 这类无 HTTP


# 11 个内置 adapter（与 chayuan-runtime/registry.py::_BUILTIN 一一对应）
_FRAMEWORK_CATALOG: Tuple[_FrameworkSpec, ...] = (
    _FrameworkSpec(
        name="ollama", label="Ollama",
        capabilities=("chat", "embedding"),
        default_url="http://127.0.0.1:11434", health_path="/api/tags",
        install_kind="one-click", install_pkg=None,
        bin_names=("ollama",),
    ),
    _FrameworkSpec(
        name="vllm", label="vLLM",
        capabilities=("chat",),
        default_url="http://127.0.0.1:18000", health_path="/v1/models",
        install_kind="docker", install_pkg=None,
        bin_names=("vllm",),
        needs_gpu=True,
    ),
    _FrameworkSpec(
        name="llamacpp", label="llama.cpp",
        capabilities=("chat",),
        default_url="http://127.0.0.1:18080", health_path="/health",
        install_kind="docker", install_pkg=None,
        bin_names=("llama-server", "llama.cpp"),
    ),
    _FrameworkSpec(
        name="infinity", label="Infinity (嵌入 / 重排)",
        capabilities=("embedding", "clip", "rerank"),
        default_url="http://127.0.0.1:7997", health_path="/health",
        install_kind="pip", install_pkg="infinity-emb[all]",
        bin_names=("infinity_emb",),
    ),
    _FrameworkSpec(
        name="comfyui", label="ComfyUI",
        capabilities=("t2i", "t2v"),
        default_url="http://127.0.0.1:18188", health_path="/system_stats",
        install_kind="docker", install_pkg=None,
        bin_names=("comfyui",),
        needs_gpu=True,
    ),
    _FrameworkSpec(
        name="funasr", label="FunASR",
        capabilities=("asr",),
        default_url="http://127.0.0.1:18180", health_path="/health",
        install_kind="pip", install_pkg="funasr",
        bin_names=("funasr",),
    ),
    _FrameworkSpec(
        name="cosyvoice", label="CosyVoice",
        capabilities=("tts",),
        default_url="http://127.0.0.1:18280", health_path="/v1/models",
        install_kind="pip", install_pkg="cosyvoice",
        bin_names=("cosyvoice",),
    ),
    _FrameworkSpec(
        # VoxCPM 2 — OpenBMB 开源轻量 TTS;0.5B 参数,CPU 实时合成中文
        # https://github.com/OpenBMB/VoxCPM
        # 端口 18580 — 避开 funasr(18180)/cosyvoice(18280)/rapidocr(18380)/paddleocr(18480)
        name="voxcpm2", label="VoxCPM 2 (TTS)",
        capabilities=("tts",),
        default_url="http://127.0.0.1:18580", health_path="/v1/models",
        install_kind="pip", install_pkg="voxcpm",
        bin_names=("voxcpm",),
    ),
    _FrameworkSpec(
        name="piper", label="Piper TTS",
        capabilities=("tts",),
        default_url="", health_path="",
        install_kind="pip", install_pkg="piper-tts",
        bin_names=("piper",),
        is_subprocess=True,
    ),
    _FrameworkSpec(
        name="rapidocr", label="RapidOCR",
        capabilities=("ocr",),
        default_url="http://127.0.0.1:18380", health_path="/health",
        install_kind="pip", install_pkg="rapidocr-onnxruntime",
        bin_names=("rapidocr",),
    ),
    _FrameworkSpec(
        name="paddleocr", label="PaddleOCR",
        capabilities=("ocr",),
        default_url="http://127.0.0.1:18480", health_path="/version",
        install_kind="pip", install_pkg="paddleocr paddlepaddle",
        bin_names=("paddleocr",),
    ),
    _FrameworkSpec(
        name="whispercpp", label="whisper.cpp",
        capabilities=("asr",),
        default_url="", health_path="",
        install_kind="pip", install_pkg="whisper-cpp-python",
        bin_names=("whisper-cli", "main"),  # llama.cpp/whisper.cpp 主可执行
        is_subprocess=True,
    ),
    # ---- 基础服务(非推理框架,但是其它 docker 服务的依赖) ----
    _FrameworkSpec(
        name="docker", label="Docker",
        capabilities=("base",),                 # base 表示基础设施
        default_url="", health_path="",
        install_kind="manual",                  # 系统级依赖,不走 chayuan 一键
        install_pkg=None,
        bin_names=("docker",),
        is_subprocess=True,                     # 不开 HTTP 探针,只看二进制
    ),
    _FrameworkSpec(
        name="docker-compose", label="Docker Compose",
        capabilities=("base",),
        default_url="", health_path="",
        install_kind="manual",
        install_pkg=None,
        bin_names=("docker-compose", "docker"),  # 新版 docker 自带 compose 子命令
        is_subprocess=True,
    ),
)
_FRAMEWORKS_BY_NAME: Dict[str, _FrameworkSpec] = {f.name: f for f in _FRAMEWORK_CATALOG}


# ============================================================================
# 55 题:**动态扫描 compose 目录** — 用户加 yaml 即时显示卡片
# ============================================================================

def _parse_compose_service(yaml_path: Any, svc_name: str) -> Tuple[str, str]:
    """从 yaml 解析单个 service 的 (default_url, health_path)。失败返空串。"""
    try:
        import yaml as _yaml
        doc = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        sdef = (doc.get("services") or {}).get(svc_name) or {}
        # 端口:支持 "host:container" / dict {published: ...} 两种写法
        host_port: Optional[int] = None
        for p in (sdef.get("ports") or []):
            if isinstance(p, str) and ":" in p:
                try:
                    host_port = int(p.split(":", 1)[0].strip())
                    break
                except ValueError:
                    continue
            elif isinstance(p, dict):
                pp = p.get("published")
                if pp:
                    try:
                        host_port = int(pp)
                        break
                    except (TypeError, ValueError):
                        continue
        default_url = f"http://127.0.0.1:{host_port}" if host_port else ""
        # healthcheck.test 是 list,例如 ["CMD-SHELL", "curl -fsS .../health || exit 1"]
        health_path = ""
        hc = sdef.get("healthcheck") or {}
        test = hc.get("test") if isinstance(hc, dict) else None
        if isinstance(test, list) and len(test) >= 2:
            txt = " ".join(str(t) for t in test[1:])
            for cand in ("/healthcheck", "/healthz", "/health",
                         "/v1/models", "/api/tags", "/system_stats", "/version"):
                if cand in txt:
                    health_path = cand
                    break
        return default_url, health_path
    except Exception as e:  # noqa: BLE001
        logger.debug("[compose-parse] %s::%s failed: %r", yaml_path.name, svc_name, e)
        return "", ""


def _get_dynamic_compose_specs() -> List[_FrameworkSpec]:
    """83 题:**唯一**服务列表来源 — 扫 ``<CHAYUAN_ROOT>/compose/*.yaml``。

    每个 yaml 文件 = 一类服务(``vllm.yaml`` / ``infinity.yaml`` / ``ollama.yaml``
    / 用户自加),每个内部 service 生成一张卡片。

    数据合并优先级:
      * 静态 ``_FRAMEWORK_CATALOG`` 命中 svc_name → 用其 ``label`` / ``capabilities``
        / ``needs_gpu``(让 UI 友好,中文 label + 准确能力标签)
      * yaml 解析的 ``default_url`` / ``health_path`` → 永远以 yaml 为准
        (用户改了端口要立刻反映)
      * 没命中静态 catalog → 全部从 yaml 推断,label 取 svc_name.title()

    所有动态 spec 的 ``install_kind="docker"``,启停走 ``docker compose -f`` 命令
    (``container_lifecycle`` 已支持 per-yaml)。**不再有 pip / one-click / manual
    路径**。

    用户操作:
      * 加 ``<CHAYUAN_ROOT>/compose/qdrant.yaml`` → 重启 chayuan → 新卡片出现
      * 删除某 yaml → 重启 → 卡片消失
    """
    out: List[_FrameworkSpec] = []
    try:
        from chayuan.server.config_panel.compose_manager import (
            list_compose_service_files,
        )
        files = list_compose_service_files()
    except Exception as e:  # noqa: BLE001
        logger.debug("[dynamic-spec] scan failed: %r", e)
        return out

    for csf in files:
        # 排除整合大文件 docker-compose.yaml(用户原话:不再加载)
        if csf.file_path.name in ("docker-compose.yaml", "docker-compose.yml"):
            continue
        for svc_name in csf.services:
            default_url, health_path = _parse_compose_service(csf.file_path, svc_name)

            # 静态 catalog 命中:借 label / capabilities / needs_gpu 做 UI 友好
            static_meta = _FRAMEWORKS_BY_NAME.get(svc_name)
            if static_meta is not None:
                out.append(_FrameworkSpec(
                    name=svc_name,
                    label=static_meta.label,
                    capabilities=static_meta.capabilities,
                    default_url=default_url or static_meta.default_url,
                    health_path=health_path or static_meta.health_path,
                    install_kind="docker",       # 强制 docker(不再走 static install_kind)
                    install_pkg=None,             # 没有 pip 包
                    bin_names=(),                 # 不再走二进制探测
                    needs_gpu=static_meta.needs_gpu,
                    is_subprocess=False,
                ))
            else:
                # 完全自动推断 — 标 capabilities=("custom",) 给 UI 显示用户自定义
                out.append(_FrameworkSpec(
                    name=svc_name,
                    label=svc_name.title(),
                    capabilities=("custom",),
                    default_url=default_url,
                    health_path=health_path,
                    install_kind="docker",
                    install_pkg=None,
                    bin_names=(),
                    needs_gpu=False,
                    is_subprocess=False,
                ))
    return out


def _get_effective_catalog() -> List[_FrameworkSpec]:
    """83 题:运行时与服务卡片列表 = 完全由 ``<CHAYUAN_ROOT>/compose/*.yaml``
    决定。没 yaml 没卡片。静态 _FRAMEWORK_CATALOG 仅用作 lookup 元数据。
    """
    return _get_dynamic_compose_specs()


def get_framework_spec_by_name(name: str) -> Optional[_FrameworkSpec]:
    """55 题:替代 ``_FRAMEWORKS_BY_NAME.get(name)`` 的公开 API,
    支持静态 catalog + 动态 compose service 全集查询。

    用户在 ``<CHAYUAN_ROOT>/compose/`` 加 ``qdrant.yaml`` 后,本函数能查到
    ``qdrant`` 的虚拟 spec(install_kind=docker, default_url 自动从 yaml 解析)。
    """
    spec = _FRAMEWORKS_BY_NAME.get(name)
    if spec is not None:
        return spec
    # 退回扫描动态服务
    for d in _get_dynamic_compose_specs():
        if d.name == name:
            return d
    return None


# ---------------------------------------------------------------------------
# 探测：HTTP / 二进制 / 进程模式
# ---------------------------------------------------------------------------

@dataclass
class RuntimeHealth:
    name: str
    spec: _FrameworkSpec
    state: str          # "running" | "installed" | "configured" | "missing"
    url: str
    bin_path: Optional[str] = None
    models_served: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


def _detect_local_os() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "win"
    return "linux"


def _http_ping(url: str, *, timeout: float = 0.6) -> bool:
    """快速 HTTP 探针；任何异常视为不通。"""
    if not url:
        return False
    try:
        import httpx  # type: ignore
        r = httpx.get(url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def _which_any(bin_names: Tuple[str, ...]) -> Optional[str]:
    """命中任一 cli 即返绝对路径;否则 None。"""
    for n in bin_names:
        p = shutil.which(n)
        if p:
            return p
    return None


# daemon 探针:某些 framework 没有 HTTP 端点(docker / docker-compose),
# 但有 daemon。装了 != 跑了,需要执行验证命令判活。
#
# value = list of (cmd, timeout_s);任一返 0 即认 running(多重 fallback)
# 用 list 而非单个 cmd 是因为不同 docker 版本的 info 子命令返回行为不一,
# 需要兜底:docker version 比 docker info 轻;docker ps 仅检查 daemon socket。
_DAEMON_PROBES: Dict[str, List[Tuple[List[str], float]]] = {
    "docker": [
        # 1. docker version --format —— Server.Version 段需要 daemon 在跑
        (["docker", "version", "--format", "{{.Server.Version}}"], 4.0),
        # 2. docker info 兜底
        (["docker", "info", "--format", "{{.ServerVersion}}"], 4.0),
        # 3. 最轻探针:列容器数(就算 0 也能验证 daemon socket 可达)
        (["docker", "ps", "-q"], 4.0),
    ],
    "docker-compose": [
        (["docker", "compose", "version"], 4.0),
        (["docker-compose", "version"], 4.0),
    ],
}


def _daemon_running(name: str) -> bool:
    """框架 daemon 是否在跑(给 is_subprocess=True 的 framework 用)。

    多重 fallback:任一 cmd 返 0 即视为 running。
    Windows 下 ``docker.exe`` 在 PATH 上有,subprocess 能找到;但需要
    用户已启动 Docker Desktop 否则任何探针都失败 → return False。
    """
    probes = _DAEMON_PROBES.get(name)
    if not probes:
        return False
    for cmd, timeout in probes:
        try:
            r = subprocess.run(  # noqa: S603
                cmd, capture_output=True, text=True, timeout=timeout, check=False,
            )
            if r.returncode == 0:
                logger.debug("[daemon_probe] %s OK via %s", name, " ".join(cmd))
                return True
            logger.debug("[daemon_probe] %s rc=%d via %s; stderr=%s",
                         name, r.returncode, " ".join(cmd), (r.stderr or "")[:200])
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug("[daemon_probe] %s exception via %s: %s",
                         name, " ".join(cmd), e)
            continue
        except Exception as e:  # noqa: BLE001
            logger.debug("[daemon_probe] %s unexpected via %s: %s",
                         name, " ".join(cmd), e)
            continue
    return False


def _read_runtime_endpoint(name: str) -> str:
    """从 ``runtime.json`` 读出 ``runtime.<name>`` 实际配置的 URL；不存在返空串。"""
    try:
        from chayuan.server.runtime import get_runtime_info
        info = get_runtime_info()
        ep = info.endpoints.get(name)
        if ep and getattr(ep, "url", ""):
            return str(ep.url)
    except Exception as e:  # noqa: BLE001
        logger.debug("[runtime_framework] read runtime.json failed: %r", e)
    return ""


def _endpoint_from_chayuan_runtime_yaml(name: str) -> str:
    """从 ``<CHAYUAN_ROOT>/runtime/<name>.yaml`` 抽 endpoint URL — 探测最高优先级。

    用户在 install_dialog 的"配置文件"区填的 host/port/endpoint 是这里的真源。
    一旦填了,就不再去猜默认端口或 docker 默认容器名,直接按用户写的 ping。

    支持的字段(按优先级):
        endpoint:       "http://..."
        url:            "http://..."
        base_url:       "http://..."
        host + port:    拼成 "http://<host>:<port>"
    """
    try:
        from chayuan.server.modality._runtime_server_base import runtime_config_path
        path = runtime_config_path(name)
        if not path.exists():
            return ""
        import yaml
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("[runtime_framework] yaml(%s) read failed: %r", name, e)
        return ""
    if not isinstance(cfg, dict):
        return ""
    for k in ("endpoint", "url", "base_url"):
        v = cfg.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    host = cfg.get("host") or cfg.get("bind_host") or ""
    port = cfg.get("port") or cfg.get("bind_port") or ""
    if host and port:
        return f"http://{host}:{port}"
    return ""


def _parse_docker_port_mapping(ports_str: str) -> str:
    """从 ``docker ps`` 的 Ports 列抽出对外暴露的端口,拼成 http URL。

    典型 Ports 列内容:
        "0.0.0.0:38080->80/tcp, :::38080->80/tcp"
        "127.0.0.1:5432->5432/tcp"
        ""    (容器只暴露内部端口)
    返回 "http://127.0.0.1:<port>" 或空串。
    """
    if not ports_str:
        return ""
    # 匹配第一个 ``host:port->container_port`` 或 ``::port->...``
    m = re.search(r"(?:[\d.]+|::):?(\d+)->\d+/tcp", ports_str)
    if not m:
        return ""
    return f"http://127.0.0.1:{m.group(1)}"


def _docker_container_endpoint(
    framework: str,
    *,
    possible_names: Tuple[str, ...] = (),
) -> Tuple[str, str]:
    """对 install_kind=='docker' 的 framework,跑 docker 探测实际容器。

    返回 ``(container_state, http_url)``:
        container_state ∈ {"running", "exited", "created", "paused", "missing"}
        http_url        从端口映射拼出;无映射返空串

    83 题精确化:**优先用** ``docker compose -f <yaml> ps``(指定该服务的
    独立 yaml),严格只查那个 compose project 内的容器。这样:
    * 用户改 yaml 里 ``container_name`` 不会丢探测(以 yaml + service 为准)
    * 多个 compose project 不会互相干扰(每个 yaml 独立 project)

    docker compose 不可用 → fallback 到原来的 ``docker ps`` 全局子串匹配。
    """
    # ---- 优先:针对该服务的 docker compose -f <yaml> ps 精确查询 ----
    compose_state, compose_url = _docker_compose_ps(framework)
    if compose_state:
        return (compose_state, compose_url)

    # ---- Fallback:docker ps 全局子串匹配(老逻辑兜底)----
    candidates = [framework.lower()] + [c.lower() for c in possible_names]
    try:
        out = subprocess.run(  # noqa: S603
            ["docker", "ps", "-a", "--no-trunc",
             "--format", "{{.Names}}\t{{.State}}\t{{.Ports}}"],
            capture_output=True, text=True, timeout=4.0, check=False,
        )
        if out.returncode != 0:
            return ("missing", "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ("missing", "")
    except Exception as e:  # noqa: BLE001
        logger.debug("[docker_probe] %s unexpected: %r", framework, e)
        return ("missing", "")

    # 在所有容器中找第一个名字含候选关键字 + 状态最有用的那个
    state_rank = {"running": 0, "paused": 1, "created": 2, "exited": 3}
    matches: List[Tuple[int, str, str]] = []  # (rank, state, url)
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        cname = parts[0].lower()
        cstate = parts[1].lower()
        cports = parts[2] if len(parts) >= 3 else ""
        if not any(c in cname for c in candidates):
            continue
        url = _parse_docker_port_mapping(cports)
        matches.append((state_rank.get(cstate, 9), cstate, url))

    if not matches:
        return ("missing", "")
    matches.sort(key=lambda t: t[0])
    _, best_state, best_url = matches[0]
    return (best_state, best_url)


def _docker_compose_ps(service: str) -> Tuple[str, str]:
    """用 ``docker compose -f <yaml> ps`` 精确查询该服务的容器状态。

    返回 ``(state, http_url)``;yaml 不存在或 docker compose 不可用 → ``("", "")``。
    state ∈ {"running", "exited", "created", "paused"}。
    """
    try:
        from chayuan.server.config_panel.compose_manager import (
            get_compose_file_for_service,
        )
        yaml_path = get_compose_file_for_service(service)
    except Exception as e:  # noqa: BLE001
        logger.debug("[compose_ps] %s lookup yaml failed: %r", service, e)
        return ("", "")

    if yaml_path is None or not yaml_path.exists():
        return ("", "")

    try:
        # docker compose ps --format json 输出 ndjson(每行一个容器)
        out = subprocess.run(  # noqa: S603
            ["docker", "compose", "-f", str(yaml_path), "ps",
             "-a", "--format", "json"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
        if out.returncode != 0:
            return ("", "")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ("", "")
    except Exception as e:  # noqa: BLE001
        logger.debug("[compose_ps] %s subprocess failed: %r", service, e)
        return ("", "")

    # 解析 ndjson;不同 docker compose 版本输出可能是单 array 或多行 ndjson
    import json as _json
    text = (out.stdout or "").strip()
    if not text:
        return ("", "")
    rows: List[Dict[str, Any]] = []
    try:
        if text.startswith("["):
            rows = _json.loads(text)
        else:
            for line in text.splitlines():
                line = line.strip()
                if line:
                    rows.append(_json.loads(line))
    except Exception as e:  # noqa: BLE001
        logger.debug("[compose_ps] %s parse json failed: %r", service, e)
        return ("", "")

    # 在该 yaml 内找匹配 service 名的行(yaml 里的 service key 匹配 row.Service)
    state_rank = {"running": 0, "paused": 1, "created": 2, "exited": 3, "dead": 4}
    matches: List[Tuple[int, str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # docker compose ps json 字段:Service / State / Publishers
        rsvc = str(row.get("Service") or "").lower()
        rstate = str(row.get("State") or "").lower()
        if service.lower() != rsvc:
            continue
        # 端口:从 Publishers 数组拼,优先 IPv4 mapping
        url = ""
        for pub in (row.get("Publishers") or []):
            if not isinstance(pub, dict):
                continue
            published = pub.get("PublishedPort")
            if published:
                url = f"http://127.0.0.1:{int(published)}"
                break
        matches.append((state_rank.get(rstate, 9), rstate, url))

    if not matches:
        return ("", "")
    matches.sort(key=lambda t: t[0])
    _, best_state, best_url = matches[0]
    return (best_state, best_url)


def _adapter_url_from_registry(name: str) -> str:
    """从 ``chayuan_runtime`` registry 拿 adapter 的 base_url，如果可用。"""
    try:
        from chayuan_runtime import get_registry  # type: ignore
        a = get_registry().by_name(name)
        if a is None:
            return ""
        # 优先 health_url（拼上 path），fallback base_url
        return a.health_url() or a.base_url or ""
    except Exception as e:  # noqa: BLE001
        logger.debug("[runtime_framework] registry.by_name(%s) failed: %r", name, e)
        return ""


def _models_served_by_runtime() -> Dict[str, int]:
    """``runtime → 已注册并启用的本地模型数``。所有探测失败时返空 dict。"""
    served: Dict[str, int] = {}
    try:
        from chayuan.server.ai_platform.repo_bridge import LocalIndexRepository
        repo = LocalIndexRepository()
        for m in repo.list():
            r = (getattr(m, "runtime", "") or "").lower()
            if r:
                served[r] = served.get(r, 0) + 1
    except Exception as e:  # noqa: BLE001
        logger.debug("[runtime_framework] enum repo.list failed: %r", e)
    return served


def probe_framework(spec: _FrameworkSpec, *, served_count: int = 0) -> RuntimeHealth:
    """对单个框架做完整探测,**多源叠加**,返回最佳判定。

    探测优先级(从高到低):
      1. ``<CHAYUAN_ROOT>/runtime/<name>.yaml`` — 用户在配置编辑器手填的(最权威)
      2. ``runtime.json`` — 系统级运行时配置
      3. ``docker ps + docker inspect`` — 对 install_kind=="docker" 的服务,
         即使用户没填配置,也通过容器实际状态发现 — 解决"容器在跑但
         chayuan 报 missing"的典型问题
      4. ``chayuan_runtime`` registry — adapter 注册的 base_url
      5. ``spec.default_url`` — 出厂默认

    四档状态:
      * ``running``    HTTP ping 成功 OR docker 容器 state=running
      * ``installed``  HTTP 不通但 ``which`` 找到 cli OR docker 容器存在但 stopped
      * ``configured`` 有 URL 但都不通且没二进制 / 容器
      * ``missing``    啥都没有
    """
    # —— 0. 92-2 题:外置 endpoint 配置(最高优先级)————————————————————
    # 用户在 UI 上配的"外置已部署"地址,绕过 docker 探测。
    # ComfyUI / Infinity 跑在别的机器或 k8s 上时,只要 HTTP 探活通就 ready。
    external_url = ""
    try:
        from chayuan.server.config_panel.external_runtimes import (
            get_external_url,
        )
        external_url = get_external_url(spec.name, spec.health_path or "")
    except Exception as e:  # noqa: BLE001
        logger.debug("[probe] external_runtimes lookup failed: %r", e)

    if external_url and _http_ping(external_url):
        # 外置 URL 通就直接 running,绕过 docker / 二进制检查
        return RuntimeHealth(
            name=spec.name, spec=spec, state="running",
            url=external_url, bin_path=None, models_served=0,
        )

    # 外置 URL 不通 → 仍优先用它做后续判定(用户希望显示自己配的地址而非 docker 探到的)
    url = external_url
    config_source = "external_runtimes" if external_url else ""

    # —— 0.5 93-2 题:本地 pip Infinity 识别 ————————————————————————————
    # 用户 ``pip install infinity-emb[all]`` 后,二进制在 PATH。如果 docker 没
    # 在跑,而本地 pip 已装且默认端口 ping 通(用户自己起了进程),就认为这是
    # 一个有效的"本地 Infinity",显示 state=running + 标 bin_path。
    if spec.name == "infinity":
        try:
            from chayuan.server.config_panel.local_infinity_pip import (
                get_local_infinity_status,
            )
            local = get_local_infinity_status(probe_port=True)
            if local.running:
                return RuntimeHealth(
                    name=spec.name, spec=spec, state="running",
                    url=local.base_url + (spec.health_path or ""),
                    bin_path=local.binary_path,
                    models_served=0,
                )
            # 装了但端口不通 → 让后续 docker 路径继续判;但记下 bin_path
            # 让卡片 detail 能显示"本地 pip 已装,可手动启动 infinity_emb"
            if local.installed:
                config_source = "local_pip"
                # url 暂不覆盖,保持 external 路径优先
        except Exception as e:  # noqa: BLE001
            logger.debug("[probe] local_infinity_pip lookup failed: %r", e)

    # —— 1. URL 多源解析 —————————————————————————————————————————————
    # 用户的 yaml 配置优先级次之 — 已经手填实际地址了,后续不再瞎猜
    if not url:
        url = _endpoint_from_chayuan_runtime_yaml(spec.name)
        config_source = "chayuan_yaml" if url else config_source
    # 如果 yaml 里只填了 endpoint 但没拼 health_path,这里补上
    if url and spec.health_path and "://" in url and not url.rstrip("/").endswith(spec.health_path):
        url = url.rstrip("/") + spec.health_path

    # —— 1.5 docker-compose.yaml 是 docker 类 framework 的端口真源 ————————————
    # 即使用户改了 ports: "19000:8000" 也能立即 parse 到 — 不再硬编码
    if not url and spec.install_kind == "docker":
        try:
            from chayuan.server.config_panel.compose_manager import (
                get_service_host_port, is_managed,
            )
            if is_managed(spec.name):
                host_port = get_service_host_port(spec.name)
                if host_port:
                    url = f"http://127.0.0.1:{host_port}{spec.health_path or ''}"
                    config_source = "docker_compose_yaml"
        except Exception as e:  # noqa: BLE001
            logger.debug("[probe] compose_manager lookup failed: %r", e)

    if not url:
        url = _read_runtime_endpoint(spec.name)
        if url:
            config_source = "runtime_json"

    # —— 2. docker 服务:跑 docker ps 实地探测 ———————————————————————————
    # 关键:docker 探测**总是**执行(只要 install_kind=='docker'),
    # 不管 url 解析到没有 — 因为 docker 容器状态本身就是权威
    docker_state = ""
    docker_url = ""
    if spec.install_kind == "docker":
        docker_state, docker_url = _docker_container_endpoint(spec.name)
        if not url and docker_url:
            url = docker_url + (spec.health_path or "")
            config_source = "docker_inspect"

    if not url:
        url = _adapter_url_from_registry(spec.name)
        if url:
            config_source = "registry"

    if not url and spec.default_url and spec.health_path:
        url = spec.default_url.rstrip("/") + spec.health_path
        config_source = config_source or "default_url"
    elif not url and spec.default_url:
        url = spec.default_url
        config_source = config_source or "default_url"

    # 96-1:不再探 pip 二进制是否在 PATH。用户原话"不再探测 pip 是否安装了 全部
    # 配置 按照配置端口探活"。bin_path 仅作为"额外信息"显示(如本地 pip 装了
    # 给个 hint),不参与 state 判定。
    bin_path = _which_any(spec.bin_names) if spec.bin_names else None
    if spec.name == "infinity" and not bin_path:
        try:
            from chayuan.server.config_panel.local_infinity_pip import (
                _check_binary,
            )
            bin_path = _check_binary()
        except Exception:  # noqa: BLE001
            pass

    # —— 3. state 判定(96-1 后:**端口为王**)—————————————————————————
    if spec.is_subprocess:
        # piper / whispercpp 这类无 HTTP server 的子进程,沿用 daemon 探针。
        # 96-1 用户的描述主要针对"运行时与服务"里有 HTTP 的服务,这部分不动。
        if bin_path and _daemon_running(spec.name):
            state = "running"
        elif bin_path:
            state = "installed"
        else:
            state = "missing"
    else:
        # 96-1:HTTP 服务 — 端口探活是**唯一**判 running 的条件
        http_ok = bool(url and _http_ping(url))
        if http_ok:
            state = "running"
        elif spec.install_kind == "docker" and docker_state == "running":
            # docker 容器在跑(健康检查路径可能不对)算 running
            state = "running"
        elif spec.install_kind == "docker" and docker_state in (
            "exited", "created", "paused"
        ):
            state = "installed"  # 容器存在但没跑 → 用户可点 ▶ 启动
        elif url:
            # 96-1:有配置(URL 来自外置 endpoint / yaml / default_url)但 HTTP
            # 不通 → state="installed"。"已配置但未运行",卡片显黄色让用户启动。
            # 不再依赖 bin_path 是否存在。
            state = "installed"
        else:
            state = "missing"

    if config_source:
        logger.debug(
            "[probe] %s state=%s url=%s source=%s docker_state=%s",
            spec.name, state, url, config_source, docker_state,
        )

    return RuntimeHealth(
        name=spec.name, spec=spec, state=state,
        url=url or "",
        bin_path=bin_path, models_served=served_count,
    )


# 进程级探测缓存 — 避免每次进入"模型配置"页面都阻塞 0.6-2s。
# (timestamp_seconds, healths)。TTL 内直接返回缓存,过期或 force=True 才重新探。
_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_CACHE: Optional[tuple] = None  # (epoch_ts, list[RuntimeHealth])
_PROBE_CACHE_TTL = 10.0  # 秒;实测 NiceGUI 反复进同一页时,10s 内完全不用再探


def probe_all_frameworks(*, force: bool = False) -> List[RuntimeHealth]:
    """全量框架探测 — **静态 catalog + 动态 compose 目录扫描**(55 题)。

    `force=True` 跳过缓存,例如点"刷新"按钮。
    """
    import time as _time

    global _PROBE_CACHE  # noqa: PLW0603 - 单例缓存,本函数双向读写

    if not force:
        with _PROBE_CACHE_LOCK:
            cache = _PROBE_CACHE
        if cache is not None and (_time.time() - cache[0]) < _PROBE_CACHE_TTL:
            return list(cache[1])

    served = _models_served_by_runtime()
    # 55 题:动态合并静态 catalog + 用户在 compose/ 加的自定义 yaml
    catalog = _get_effective_catalog()
    out: List[RuntimeHealth] = [None] * len(catalog)  # type: ignore[list-item]
    threads: List[threading.Thread] = []

    def _one(idx: int, spec: _FrameworkSpec) -> None:
        try:
            out[idx] = probe_framework(spec, served_count=served.get(spec.name, 0))
        except Exception as e:  # noqa: BLE001
            logger.debug("[runtime_framework] probe %s failed: %r", spec.name, e)
            out[idx] = RuntimeHealth(
                name=spec.name, spec=spec, state="missing",
                url="", bin_path=None, models_served=served.get(spec.name, 0),
            )

    for i, s in enumerate(catalog):
        t = threading.Thread(target=_one, args=(i, s), daemon=True)
        t.start()
        threads.append(t)

    # 56 题修复:**总 deadline join**(原来串行 t.join(timeout=2.0) × 15 thread
    # 最坏情况 30 秒 — 这是"运行时与服务"页一直显示加载中的真凶)。
    # 改成 deadline 共享:总上限 3 秒,任一 thread 没在 deadline 内回都填 missing。
    _deadline = _time.time() + 3.0
    for t in threads:
        remaining = max(0.0, _deadline - _time.time())
        if remaining <= 0:
            break  # deadline 已过,后续 thread 全填 missing
        t.join(timeout=remaining)

    # 兜底:如果某个线程没结束(超时 / deadline 过),补一个 missing 占位
    for i, s in enumerate(catalog):
        if out[i] is None:
            out[i] = RuntimeHealth(
                name=s.name, spec=s, state="missing", url="",
                bin_path=None, models_served=served.get(s.name, 0),
            )

    # 排序：running > installed > configured > missing；同档按能力数倒序、name 字典序
    order = {"running": 0, "installed": 1, "configured": 2, "missing": 3}
    out.sort(key=lambda h: (order.get(h.state, 9), -len(h.spec.capabilities), h.name))

    # 写缓存 — 后续 TTL 内同进程直接返回 list 副本
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE = (_time.time(), list(out))
    return out


def invalidate_probe_cache() -> None:
    """显式失效缓存(start/stop service 后立即调用,确保下次探活拿到新状态)。"""
    global _PROBE_CACHE
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE = None


# ---------------------------------------------------------------------------
# 一键安装（subprocess） —— Ollama / pip
# ---------------------------------------------------------------------------

_install_lock = threading.Lock()
_install_state: Dict[str, Dict[str, Any]] = {}


def _build_install_cmd(name: str) -> Optional[List[str]]:
    spec = _FRAMEWORKS_BY_NAME.get(name)
    if spec is None:
        return None
    host_os = _detect_local_os()
    if name == "ollama":
        if host_os == "win":
            return ["winget", "install", "-e", "--id", "Ollama.Ollama"]
        return ["bash", "-lc", "curl -fsSL https://ollama.com/install.sh | sh"]
    if spec.install_kind == "pip" and spec.install_pkg:
        return [sys.executable, "-m", "pip", "install", *shlex.split(spec.install_pkg)]
    return None


def _spawn_install_async(name: str) -> Tuple[str, Optional[str]]:
    cmd = _build_install_cmd(name)
    if cmd is None:
        spec = _FRAMEWORKS_BY_NAME.get(name)
        kind = spec.install_kind if spec else "unknown"
        return "", f"runtime '{name}' 暂不支持自动安装（kind={kind}）；请使用安装指南里的 docker / 手动命令"
    task_id = f"install-{name}-{int(time.time() * 1000)}"
    with _install_lock:
        _install_state[task_id] = {
            "task_id": task_id, "name": name, "state": "running",
            "log": [], "started": time.time(),
        }

    def _bg() -> None:
        try:
            proc = subprocess.Popen(  # noqa: S603
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                with _install_lock:
                    log = _install_state[task_id]["log"]
                    log.append(line.rstrip()[:400])
                    if len(log) > 200:
                        del log[: len(log) - 200]
            rc = proc.wait()
            with _install_lock:
                _install_state[task_id]["state"] = "done" if rc == 0 else "failed"
                _install_state[task_id]["return_code"] = rc
        except Exception as e:  # noqa: BLE001
            with _install_lock:
                _install_state[task_id]["state"] = "failed"
                _install_state[task_id]["error"] = str(e)

    threading.Thread(target=_bg, daemon=True).start()
    return task_id, None


# ---------------------------------------------------------------------------
# UI · 顶部第一行：模型框架卡片
# ---------------------------------------------------------------------------

# 4 档健康颜色 + 文字（直接给前端展示）
_HEALTH_TONE: Dict[str, Dict[str, str]] = {
    "running":    {"dot": "#10b981", "bg": "#ecfdf5", "border": "#a7f3d0", "text": "运行中"},
    "installed":  {"dot": "#3b82f6", "bg": "#eff6ff", "border": "#bfdbfe", "text": "已安装·未启动"},
    # configured 状态已合并到 missing — 留映射避免历史代码 KeyError
    "configured": {"dot": "#9ca3af", "bg": "#f9fafb", "border": "#e5e7eb", "text": "未安装"},
    "missing":    {"dot": "#9ca3af", "bg": "#f9fafb", "border": "#e5e7eb", "text": "未安装"},
}


def render_runtime_framework_row(
    ui: Any,
    *,
    _prefetched_healths: Optional[List["RuntimeHealth"]] = None,
) -> Callable[[], None]:
    """渲染顶部"模型框架"一行;返回 ``refresh()`` 让外部刷新。

    Args:
        _prefetched_healths: 42 题 P0.A — 调用方已经在 thread 池里 probe 过的
            结果,首次渲染直接用,跳过本函数内的同步 ``probe_all_frameworks`` 调用
            (后者在 cache 空时会 join 15 thread 最多 2 秒,卡 NiceGUI asyncio loop)。
            点"刷新"按钮 / 后续 start/stop 时仍走原同步路径。
    """
    container = ui.column().classes("w-full q-mb-sm").style("gap: 6px;")
    # 把首次 prefetched 结果挂到闭包里,只在 _render 第一次被调时使用。
    _initial_state = {"healths": _prefetched_healths}

    def _render(*, force: bool = False) -> None:
        # 62 题:client 已死 → silent return + self-remove,避免触发 NiceGUI
        # "Client has been deleted but is still being used" 警告破坏后续点击
        from chayuan.server.config_panel._safe_ui import is_client_alive
        if not is_client_alive(container):
            try:
                _FRAMEWORK_CARDS_REFRESH.remove(_render)
            except ValueError:
                pass
            return
        # 首次渲染:若有 prefetched(由 wrapper 在 thread 并发拿到)直接用,
        # 跳过同步 probe_all_frameworks。force=True 时不复用,绕过缓存重 probe。
        if not force and _initial_state["healths"] is not None:
            healths = _initial_state["healths"]
            _initial_state["healths"] = None  # 只用一次,后续刷新走正常路径
        else:
            healths = probe_all_frameworks(force=force)
        running_n = sum(1 for h in healths if h.state == "running")
        installed_n = sum(1 for h in healths if h.state == "installed")

        container.clear()
        with container:
            with ui.card().classes("w-full").props("flat bordered").style(
                "background: #fafbfc; padding: 10px 12px;"
            ):
                with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                    ui.icon("hub", size="18px").classes("text-grey-7")
                    ui.label("运行时与服务").classes("text-subtitle2")
                    # 83 题:卡片数 = 动态扫 <CHAYUAN_ROOT>/compose/*.yaml 的服务数
                    # 没 yaml 没卡;启停全走 docker compose -f 命令
                    ui.label(
                        f"共 {len(healths)} 个 · 来源 <CHAYUAN_ROOT>/compose/*.yaml · "
                        f"运行中 {running_n} · 已安装未跑 {installed_n}"
                    ).classes("text-caption text-grey-6")
                    ui.space()
                    ui.button(
                        "刷新", icon="refresh",
                        on_click=lambda: _render(force=True),  # 跳过缓存
                    ).props("dense flat color=primary size=sm")

                # 关键改动：用 ui.row(wrap=True) + 显式 width 避免 ui.grid + col-12
                # 在某些 NiceGUI 版本下塌成 0 高度的问题
                if not healths:
                    # 83 题:0 yaml = 0 卡片;给清晰提示而不是空白
                    with ui.column().classes("w-full q-mt-sm q-pa-md items-center").style(
                        "gap: 6px; border: 1px dashed #d1d5db; border-radius: 6px;"
                    ):
                        ui.icon("inbox", size="32px").classes("text-grey-5")
                        ui.label("暂无服务 yaml").classes(
                            "text-subtitle2 text-grey-7"
                        )
                        ui.label(
                            "把 docker-compose 单服务 yaml 放到 "
                            "<CHAYUAN_ROOT>/compose/(如 vllm.yaml / ollama.yaml),"
                            "重启后此处会自动出现卡片。模板见 "
                            "libs/chayuan-server/server-compose/"
                        ).classes("text-caption text-grey-6 text-center").style(
                            "max-width: 480px; line-height: 1.5;"
                        )
                else:
                    with ui.row().classes("w-full q-mt-sm").style(
                        "gap: 8px; flex-wrap: wrap;"
                    ):
                        for h in healths:
                            _render_card(h)

    def _render_card(h: RuntimeHealth) -> None:
        tone = _HEALTH_TONE.get(h.state, _HEALTH_TONE["missing"])
        # 4 列布局:每张卡 calc((100% - 24px) / 4) 让 gap=8px (3 个间隙) 准确;
        # min-width 220px 小屏自动降级到 2-3 列
        with ui.card().props("flat bordered").style(
            f"flex: 0 0 calc((100% - 24px) / 4); min-width: 220px; "
            f"padding: 10px 12px; cursor: pointer; "
            f"border-color: {tone['border']}; background: {tone['bg']};"
        ).on("click", lambda _e=None, hh=h: _open_install_dialog(hh)):
            # 顶行:色点 · label · 启动按钮(已安装未启动) · 状态文字
            with ui.row().classes("items-center no-wrap w-full").style("gap: 6px;"):
                ui.html(
                    f'<span style="display:inline-block;width:10px;height:10px;'
                    f'border-radius:50%;background:{tone["dot"]};"></span>'
                )
                ui.label(h.spec.label).style(
                    "font-weight: 600; font-size: 13px; flex: 1 1 auto; "
                    "white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
                )
                # 已安装·未启动 → ▶ 启动;运行中 → ⏹ 停止
                if h.state == "installed":
                    def _start_clicked(_e: Any = None, name: str = h.spec.name) -> None:
                        from chayuan.server.config_panel.install_task_manager import (
                            get_install_manager,
                        )
                        task = get_install_manager().start_service(framework=name)
                        if task is None:
                            ui.notify(
                                f"{name} 无法自动启动(spec 缺 bin_names / install_kind 不支持);"
                                f"请用安装弹窗手动启",
                                type="warning",
                            )
                            return
                        ui.notify(f"已启动 {h.spec.label}(后台运行)", type="info")
                        invalidate_probe_cache()
                        # 启动后:框架卡片自身重渲染 + 默认模型选择器拉新候选
                        ui.timer(2.0, lambda: _render(force=True), once=True)
                        ui.timer(2.5, trigger_capability_defaults_refresh, once=True)
                    btn = ui.button(icon="play_arrow").props(
                        "dense round size=xs unelevated color=positive"
                    ).style("flex: 0 0 auto;").tooltip("启动服务")
                    btn.on("click.stop", _start_clicked)
                elif h.state == "running":
                    # 已运行 → 显示 ⏹ 停止 按钮
                    def _stop_clicked(_e: Any = None, name: str = h.spec.name) -> None:
                        from chayuan.server.config_panel.install_task_manager import (
                            get_install_manager,
                        )
                        task = get_install_manager().stop_service(framework=name)
                        if task is None:
                            ui.notify(f"{name} 暂无停止配方", type="warning")
                            return
                        ui.notify(f"已停止 {h.spec.label}", type="info")
                        invalidate_probe_cache()
                        ui.timer(2.0, lambda: _render(force=True), once=True)
                        ui.timer(2.5, trigger_capability_defaults_refresh, once=True)
                    btn = ui.button(icon="stop").props(
                        "dense round size=xs outline color=negative"
                    ).style("flex: 0 0 auto;").tooltip("停止服务")
                    btn.on("click.stop", _stop_clicked)
                ui.label(tone["text"]).classes("text-caption").style(
                    f"color: {tone['dot']}; flex: 0 0 auto; font-weight: 500;"
                )
            # capability 标签
            with ui.row().classes("items-center q-mt-xs").style(
                "gap: 4px; flex-wrap: wrap;"
            ):
                for cap in h.spec.capabilities:
                    ui.label(_CAP_TO_ZH.get(cap, cap)).classes("text-caption").style(
                        "background: white; border: 1px solid #e5e7eb; "
                        "border-radius: 4px; padding: 0 4px; font-size: 10px;"
                    )
                if h.spec.needs_gpu:
                    ui.label("需 GPU").classes("text-caption").style(
                        "background: #f3e8ff; color: #6b21a8; "
                        "border-radius: 4px; padding: 0 4px; font-size: 10px;"
                    )
                if h.models_served:
                    ui.label(f"{h.models_served} 模型").classes("text-caption").style(
                        "background: #e0f2fe; color: #075985; "
                        "border-radius: 4px; padding: 0 4px; font-size: 10px;"
                    )
            # URL / bin path + 外置 endpoint 配置入口(92-3)
            detail = h.url or (
                f"binary: {h.bin_path}" if h.bin_path else "未配置 URL"
            )
            with ui.row().classes("items-center w-full no-wrap").style(
                "gap: 4px; margin-top: 4px;"
            ):
                ui.label(detail).classes("text-caption text-grey-7").style(
                    "flex: 1 1 auto; font-family: ui-monospace, monospace; "
                    "font-size: 10px; white-space: nowrap; overflow: hidden; "
                    "text-overflow: ellipsis;"
                )
                # 92-3:link 按钮 → 弹外置 endpoint 配置对话框,
                # 让用户填外置 URL(自己机器 / k8s 部署的 ComfyUI / Infinity)
                ext_btn = ui.button(icon="link").props(
                    "dense round size=xs flat color=primary"
                ).style("flex: 0 0 auto;").tooltip(
                    "配置外置 endpoint(已部署在别处时,填 URL 即可)"
                )
                ext_btn.on(
                    "click.stop",
                    lambda _e=None, hh=h: _open_external_endpoint_dialog(hh),
                )

    def _open_external_endpoint_dialog(h: RuntimeHealth) -> None:
        """92-3:外置 endpoint 配置对话框。

        允许用户填一个 URL,chayuan 探活通就算 ready,绕过本地 docker。
        """
        from chayuan.server.config_panel.external_runtimes import (
            delete_external_runtime, get_external_runtime, probe_external,
            set_external_url,
        )

        spec = h.spec
        existing = get_external_runtime(spec.name) or {}
        with ui.dialog() as dlg, ui.card().style(
            "min-width: min(560px, 92vw); max-width: 96vw; padding: 16px;"
        ):
            # 标题
            with ui.row().classes("items-center w-full no-wrap").style("gap: 8px;"):
                ui.icon("link", size="20px").classes("text-primary")
                ui.label(f"配置外置 endpoint — {spec.label}").classes(
                    "text-h6"
                ).style("flex: 1;")
                ui.button(icon="close", on_click=dlg.close).props(
                    "flat round dense"
                )

            ui.label(
                "如果你的服务已经跑在别的机器(GPU 服务器 / k8s / portainer 等),"
                "填它的访问地址。chayuan 探活通过即视为 ready,不需要本地起 docker。"
            ).classes("text-caption text-grey-7 q-mb-md")

            # URL 输入
            url_input = ui.input(
                label="URL(必填)",
                placeholder=f"如 {spec.default_url or 'http://10.0.0.5:7997'}",
                value=str(existing.get("url") or ""),
            ).props("outlined dense").classes("w-full")

            # health_path 输入(可选,默认 spec.health_path)
            hp_input = ui.input(
                label=f"健康检查路径(可选,默认 {spec.health_path or '无'})",
                value=str(existing.get("health_path") or ""),
            ).props("outlined dense").classes("w-full")

            # 启用 switch
            enabled_sw = ui.switch(
                "启用此外置 endpoint(关闭则走本地 docker)",
                value=bool(existing) and bool(existing.get("enabled", True)),
            ).props("dense color=primary")

            test_lbl = ui.label("").classes("text-caption q-mt-sm").style(
                "min-height: 18px; font-family: ui-monospace, monospace;"
            )

            def _do_test() -> None:
                # NiceGUI ``set_text()`` 返 None,不能链式 ``.style()`` —
                # 拆成两步,先设文本再单独设样式
                def _set(msg: str, color: str) -> None:
                    test_lbl.set_text(msg)
                    test_lbl.style(f"color:{color};")

                from chayuan.server.config_panel.external_runtimes import (
                    normalize_url,
                )
                # 94-1:URL 自动补 http:// — 用户填 127.0.0.1:7997 也能用
                u = normalize_url(url_input.value or "")
                if u and u != (url_input.value or "").strip():
                    url_input.set_value(u)  # 把补全后的回写,用户能看到
                hp = (hp_input.value or "").strip() or (spec.health_path or "")
                if u and hp and not hp.startswith("/"):
                    hp = "/" + hp
                full = (u.rstrip("/") + hp) if u else ""
                if not full:
                    _set("⚠ 请先填 URL", "#ef4444")
                    return
                _set("探活中…", "#6b7280")
                ok, detail = probe_external(full, timeout=2.5)
                if ok:
                    _set(f"✓ 通(HTTP {detail})", "#22c55e")
                else:
                    _set(f"✗ 不通: {detail}"[:160], "#ef4444")

            def _do_save() -> None:
                from chayuan.server.config_panel.external_runtimes import (
                    get_external_runtime,
                )
                from chayuan.server.config_panel import yaml_store

                # 用户原话:"现在配置后发现没保存链接和端口"
                # — 加诊断,保存后立即读回验证,失败给具体原因和路径
                ok, msg = set_external_url(
                    spec.name,
                    url=(url_input.value or "").strip(),
                    health_path=(hp_input.value or "").strip(),
                    enabled=bool(enabled_sw.value),
                )
                if not ok:
                    ui.notify(f"{spec.label} 保存失败: {msg}",
                              type="negative", multi_line=True, timeout=8000)
                    return

                # 读回验证 — 防止 yaml 写盘静默失败 / 路径不对
                try:
                    yaml_p = yaml_store.yaml_path("external_runtimes.yaml")
                except Exception:
                    yaml_p = "<unknown>"
                try:
                    readback = get_external_runtime(spec.name) or {}
                except Exception as e:  # noqa: BLE001
                    ui.notify(
                        f"{spec.label} 已写盘但读回失败: {e}",
                        type="negative", multi_line=True, timeout=10000,
                    )
                    return

                if not readback or not readback.get("url"):
                    # enabled=False + 空 url 是合法状态;否则视为异常
                    if bool(enabled_sw.value):
                        ui.notify(
                            f"{spec.label} 写盘后读回为空!检查 yaml 路径权限:"
                            f" {yaml_p}",
                            type="negative", multi_line=True, timeout=10000,
                        )
                        return

                ui.notify(
                    f"{spec.label} 已保存: {readback.get('url') or '(已禁用)'} "
                    f"→ {yaml_p}",
                    type="positive", multi_line=True, timeout=5000,
                )
                invalidate_probe_cache()
                dlg.close()
                ui.timer(0.5, lambda: _render(force=True), once=True)

            def _do_delete() -> None:
                ok, msg = delete_external_runtime(spec.name)
                ui.notify(
                    f"{spec.label} {msg}",
                    type="positive" if ok else "negative",
                )
                if ok:
                    invalidate_probe_cache()
                    dlg.close()
                    ui.timer(0.5, lambda: _render(force=True), once=True)

            with ui.row().classes("w-full justify-end q-mt-md").style("gap: 8px;"):
                ui.button("测试连接", icon="health_and_safety",
                          on_click=_do_test).props("flat color=primary")
                if existing:
                    ui.button("删除配置", icon="delete_outline",
                              on_click=_do_delete).props("flat color=negative")
                ui.button("保存", icon="save",
                          on_click=_do_save).props("unelevated color=primary")

        dlg.open()


    def _open_install_dialog(h: RuntimeHealth) -> None:
        # 委托到新 install_dialog 模块:固定大小 / 关闭不杀任务 / 多镜像源 chip
        # 关键修复: 老版关闭弹窗会让 ui.timer 失效,新版用 install_task_manager
        # 单例持有 task,弹窗只是视图,关闭重开都能拿到当前进度
        #
        # 43 题 P0.E + 47 题:任何 exception 都不能冒泡到 NiceGUI(否则触发
        # page reload + WS connection lost)。catch 后:
        #   1) 详细 traceback 打 server log
        #   2) UI 上**直接 mount 一个错误 dialog** 而不是 ui.notify(后者很容易被
        #      其他 toast 覆盖,用户看不见就会以为"点了没反应")
        try:
            from chayuan.server.config_panel.install_dialog import open_install_dialog as _odlg
            _odlg(ui, h, on_after_install=_render)
        except Exception as e:  # noqa: BLE001
            import traceback as _tb
            tb_text = _tb.format_exc()
            logger.exception(
                "[install_dialog] open failed for %s: %s", h.spec.name, e,
            )
            # 渲染一个错误对话框 — 比 toast 更醒目,且能复制 tb 给开发组
            try:
                with ui.dialog().props("persistent=false") as err_dlg:
                    with ui.card().style(
                        "min-width: 480px; max-width: 720px; padding: 16px;"
                    ):
                        ui.label(
                            f"打开 [{h.spec.label}] 安装弹窗失败"
                        ).classes("text-h6 text-negative")
                        ui.label(
                            f"{type(e).__name__}: {e}"
                        ).classes("text-body2 q-mt-sm")
                        ui.label("详细堆栈(请发给开发组):").classes(
                            "text-caption text-grey-7 q-mt-md",
                        )
                        ui.code(tb_text[-1500:]).classes("w-full").style(
                            "max-height: 240px; overflow-y: auto; "
                            "font-size: 11px;",
                        )
                        with ui.row().classes("w-full justify-end q-mt-md"):
                            ui.button(
                                "复制堆栈", icon="content_copy",
                                on_click=lambda: ui.run_javascript(
                                    f"navigator.clipboard.writeText({tb_text!r})",
                                ),
                            ).props("flat dense")
                            ui.button(
                                "关闭", on_click=err_dlg.close,
                            ).props("unelevated color=primary dense")
                err_dlg.open()
            except Exception:  # noqa: BLE001
                # 兜底兜底 — 错误 dialog 都构造不出来时退回 toast
                try:
                    ui.notify(
                        f"打开 [{h.spec.label}] 安装弹窗失败:{type(e).__name__}: {e}",
                        type="negative", timeout=8000,
                    )
                except Exception:  # noqa: BLE001
                    pass

    _render()
    # 注册到全局,让其它面板(默认模型选择器、安装弹窗 on_after_install)级联刷新
    _FRAMEWORK_CARDS_REFRESH.append(_render)
    return _render


def _install_recipes(spec: _FrameworkSpec) -> List[Tuple[str, str]]:
    """回退命令清单——按 host_os 排序；docker 总在最后做兜底。"""
    host_os = _detect_local_os()
    out: List[Tuple[str, str]] = []
    if spec.name == "ollama":
        if host_os == "linux":
            out.append(("Linux · curl",
                        "curl -fsSL https://ollama.com/install.sh | sh"))
            out.append(("Linux · apt (ollama PPA)",
                        "# https://ollama.com/download/linux"))
        elif host_os == "mac":
            out.append(("macOS · brew", "brew install --cask ollama"))
        elif host_os == "win":
            out.append(("Windows · winget",
                        "winget install -e --id Ollama.Ollama"))
        out.append(("Docker（任意系统）",
                    "docker run -d -p 11434:11434 --name ollama ollama/ollama"))
        return out
    if spec.install_kind == "pip" and spec.install_pkg:
        out.append((f"pip · {host_os}",
                    f"{sys.executable} -m pip install {spec.install_pkg}"))
        return out
    if spec.name == "vllm":
        out.append(("Docker（推荐）",
                    "docker run --runtime nvidia --gpus all -p 18000:8000 vllm/vllm-openai"))
        out.append(("pip（需 CUDA）", "pip install vllm"))
        return out
    if spec.name == "comfyui":
        out.append(("Docker", "docker run -p 18188:8188 yanwk/comfyui-boot"))
        out.append(("从源码", "git clone https://github.com/comfyanonymous/ComfyUI.git"))
        return out
    if spec.name == "llamacpp":
        out.append(("Docker", "docker run -p 18080:8080 ghcr.io/ggerganov/llama.cpp:server"))
        return out
    return out


# ---------------------------------------------------------------------------
# UI · 顶部第二行：9 类 capability 默认模型（持久化到 model_settings.yaml）
# ---------------------------------------------------------------------------

_FILE = "model_settings.yaml"

# capability 与 model_settings.yaml 字段名的映射
_CAP_TO_YAML_KEY: Dict[str, str] = {
    "chat":      "DEFAULT_LLM_MODEL",
    "embedding": "DEFAULT_EMBEDDING_MODEL",
    "clip":      "DEFAULT_IMAGE_EMBEDDING_MODEL",
    "rerank":    "DEFAULT_RERANK_MODEL",
    "t2i":       "DEFAULT_TEXT2IMAGE_MODEL",
    "t2v":       "DEFAULT_TEXT2VIDEO_MODEL",
    "tts":       "DEFAULT_TEXT2SPEECH_MODEL",
    "asr":       "DEFAULT_SPEECH2TEXT_MODEL",
    "ocr":       "DEFAULT_OCR_MODEL",
}

# capability 与 ``MODEL_PLATFORMS[*].<group_key>`` 字段名的映射
# group_key 用来汇总"所有平台已启用的某类模型"作为下拉候选
_CAP_TO_GROUP_KEY: Dict[str, str] = {
    "chat":      "llm_models",
    "embedding": "embed_models",
    "clip":      "image2text_models",       # 图像嵌入暂复用 image2text 通道
    "rerank":    "rerank_models",
    "t2i":       "text2image_models",
    "t2v":       "text2image_models",       # t2v 在 yaml 里还没单独通道，暂复用 t2i
    "tts":       "text2speech_models",
    "asr":       "speech2text_models",
    "ocr":       "image2text_models",
}


def _load_capability_defaults() -> Dict[str, str]:
    """从 ``model_settings.yaml`` 读 9 个 ``DEFAULT_*_MODEL``。"""
    from chayuan.server.config_panel import yaml_store
    out = {cap: "" for cap, _ in CAPABILITY_LABELS}
    try:
        load = yaml_store.load_yaml(_FILE)
        doc = load.doc if isinstance(load.doc, dict) else {}
        for cap, yaml_key in _CAP_TO_YAML_KEY.items():
            v = doc.get(yaml_key)
            if isinstance(v, str) and v:
                out[cap] = v
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_defaults] load yaml failed: %r", e)
    return out


def _save_capability_default(cap: str, model_id: str) -> Tuple[bool, str]:
    """**单条**写入；保留其它 yaml 字段。返回 ``(ok, message)``。

    89-5:cap == ``"clip"`` 时额外写 ``capability_defaults.clip`` nested 字段
    (含 platform_name),让 :func:`embedder.resolve_default` 能直接知道
    应该走 Infinity 还是 in-process。

    nested 字段形如:
    ::

        capability_defaults:
          clip:
            model_id: jinaai/jina-clip-v1
            platform_name: infinity-local

    顶层 ``DEFAULT_IMAGE_EMBEDDING_MODEL`` 仍然继续写,保持向后兼容。
    """
    yaml_key = _CAP_TO_YAML_KEY.get(cap)
    if not yaml_key:
        return False, f"未知 capability：{cap}"
    try:
        from chayuan.server.config_panel import yaml_store
        updates: Dict[str, Any] = {yaml_key: model_id}
        # 89-5:clip 额外写 capability_defaults nested 字段
        if cap == "clip":
            platform_name = _lookup_platform_for_model(model_id, "image2text")
            updates["capability_defaults"] = {
                "clip": {
                    "model_id": model_id,
                    "platform_name": platform_name or "",
                }
            }
        # 用 save_updates：保留注释 + 原子写 + .bak 备份 + config center 同步
        path, bak, changes = yaml_store.save_updates(_FILE, updates)
        # 同步到 LocalIndexRepository 内存（让本进程其它请求立即生效）
        try:
            from chayuan.server.ai_platform.repo_bridge import LocalIndexRepository
            repo = LocalIndexRepository()
            repo.set_default(cap, model_id)
        except Exception:
            pass
        # 89-5:清空 image embedder client 缓存,下次 get_client 重建
        if cap == "clip":
            try:
                from chayuan.server.image_source.embedder import (
                    _invalidate_client_cache,
                )
                _invalidate_client_cache(None)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        logger.exception("[capability_defaults] save yaml failed")
        return False, f"写盘失败：{type(e).__name__}: {e}"
    return True, "已保存"


def _add_infinity_inventory(
    out: Dict[str, Dict[str, List[Tuple[str, str]]]]
) -> None:
    """94-2:把 Infinity ``/v1/models`` 真实加载的模型按 capability 填到 out。

    探测顺序:
      A. external_runtimes.yaml 里 ``infinity`` 的 url(用户外置部署)
      B. 本地 pip Infinity 默认端口(``CHAYUAN_LOCAL_INFINITY_PORT`` 或 7997)

    分组 label:
      * 外置 → ``"外置 · Infinity (<host>)"``
      * 本地 → ``"本地 · Infinity"``

    全部失败 / Infinity 不可达 → 静默跳过,不破坏 out 结构。
    """
    from chayuan.server.config_panel.external_runtimes import (
        get_external_runtime,
    )
    from chayuan.server.config_panel.infinity_inventory import (
        get_infinity_models_by_capability,
    )
    from chayuan.server.config_panel.local_infinity_pip import (
        _local_infinity_url, is_local_infinity_running,
    )

    candidates: List[Tuple[str, str]] = []  # [(base_url, group_label), ...]

    # A. 外置 endpoint
    try:
        ext = get_external_runtime("infinity")
        if ext and ext.get("url"):
            base = str(ext["url"]).rstrip("/")
            # group_label 截短 host 显示,避免太长
            host = base.split("://", 1)[-1].split("/", 1)[0]
            candidates.append((base, f"外置 · Infinity ({host})"))
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_grouped] external infinity lookup failed: %r", e)

    # B. 本地 pip Infinity
    try:
        if is_local_infinity_running():
            base = _local_infinity_url()
            # 避免和外置 candidate 撞 url 重复列
            if not any(b == base for b, _ in candidates):
                candidates.append((base, "本地 · Infinity"))
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_grouped] local pip infinity lookup failed: %r", e)

    if not candidates:
        return

    for base_url, group_label in candidates:
        try:
            by_cap = get_infinity_models_by_capability(base_url)
        except Exception as e:  # noqa: BLE001
            logger.debug("[capability_grouped] inventory failed for %s: %r",
                         base_url, e)
            continue

        for cap, models in by_cap.items():
            if cap not in out or not models:
                continue
            existing = {x[0] for x in out[cap].get(group_label, [])}
            for m in models:
                if m.model_id in existing:
                    continue
                display = _friendlify_model_id(m.model_id)
                out[cap].setdefault(group_label, []).append(
                    (m.model_id, display)
                )


def _lookup_platform_for_model(model_id: str, model_type: str) -> Optional[str]:
    """89-5:根据 model_id 反查它所属的 platform_name(无副作用,失败返 None)。

    93-3:扩展 — 命中 hf-cache + 本地 pip Infinity running → 返 ``infinity-local-pip``,
    让 embedder 走 InfinityHttpClient 到 127.0.0.1:7997。
    """
    try:
        from chayuan.server.utils import get_config_models
        models = get_config_models(model_type=model_type)
        info = models.get(model_id) or {}
        plat = info.get("platform_name")
        if plat:
            return str(plat).strip() or None
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_defaults] lookup platform failed: %r", e)

    # 94-2:Infinity 真实 inventory 反查 — 比 hf-cache 关键词更准
    try:
        from chayuan.server.config_panel.external_runtimes import (
            get_external_runtime,
        )
        from chayuan.server.config_panel.infinity_inventory import (
            fetch_infinity_models,
        )
        # A. 外置 endpoint
        ext = get_external_runtime("infinity")
        if ext and ext.get("url"):
            base = str(ext["url"]).rstrip("/")
            for m in fetch_infinity_models(base):
                if m.model_id == model_id:
                    return "infinity-external"
        # B. 本地 pip
        from chayuan.server.config_panel.local_infinity_pip import (
            _local_infinity_url, is_local_infinity_running,
        )
        if is_local_infinity_running():
            base = _local_infinity_url()
            for m in fetch_infinity_models(base):
                if m.model_id == model_id:
                    return "infinity-local-pip"
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_defaults] inventory lookup failed: %r", e)

    return None


def _resolve_clip_runtime_badge() -> Tuple[str, str, str]:
    """89-8:返回 ``(state, color, tooltip)``。state ∈ ``{"online", "degraded", "missing"}``。

    映射规则(基于 :func:`embedder.get_client` + :func:`resolve_default`):
      * 当前 client.kind == "infinity" + healthy=True   → online  绿
      * 当前 client.kind == "inproc" 但 platform 是 infinity → degraded 黄
      * 当前 client.kind == "inproc" 且 platform=None   → online(本地纯 in-proc 也算"运行中")
      * 任何路径都失败 / 没默认                          → missing  红
    """
    try:
        from chayuan.server.image_source.embedder import (
            get_client, is_infinity_platform, resolve_default,
        )
        model_id, platform = resolve_default()
        try:
            cli = get_client()
            healthy = bool(cli.healthcheck())
            if cli.kind == "infinity" and healthy:
                base = getattr(cli, "base_url", "") or ""
                return ("online", "#22c55e", f"●在线  运行于 Infinity {base}")
            if cli.kind == "inproc" and healthy:
                # 92 题:用户选了云厂商但 fallback 到本地 SigLIP → 算"已配置"蓝色
                if platform and not is_infinity_platform(platform):
                    return ("configured", "#2563eb",
                            f"●已配置  ④ tab 选了 {model_id} @ {platform};"
                            "云厂商图像模型本期走本地 SigLIP 兜底入索引")
                if is_infinity_platform(platform):
                    return ("degraded", "#f59e0b",
                            "●降级  Infinity 不可达,已切到本地 in-process 模式")
                return ("online", "#22c55e", "●在线  本地 in-process 模式")
            return ("degraded", "#f59e0b",
                    f"●降级  {cli.kind} healthcheck 未通过")
        except Exception as e:  # noqa: BLE001
            # 92 题:用户选了云厂商模型 → 仍算 "configured",蓝色不报红
            if model_id and platform and not is_infinity_platform(platform):
                return ("configured", "#2563eb",
                        f"●已配置  ④ tab 选了 {model_id} @ {platform};"
                        "本地嵌入兜底未就绪,可在模型广场装一个本地 CLIP")
            return ("missing", "#ef4444", f"●未配置  {type(e).__name__}: {e}"[:120])
    except Exception as e:  # noqa: BLE001
        return ("missing", "#ef4444", f"●未配置  {type(e).__name__}")


def _render_clip_runtime_badge(ui: Any) -> None:
    """89-8:渲染 ④ tab clip 行右侧的运行位置 chip。"""
    try:
        state, color, tip = _resolve_clip_runtime_badge()
    except Exception as e:  # noqa: BLE001
        logger.debug("[clip-badge] render failed: %r", e)
        return
    label_map = {
        "online": "●在线", "configured": "●已配置",
        "degraded": "●降级", "missing": "●未配置",
    }
    txt = label_map.get(state, "●未知")
    ui.label(txt).classes("text-caption").style(
        f"color: {color}; font-weight: 600; font-size: 10px;"
        " padding: 0 4px;"
    ).tooltip(tip)


def _capability_candidates() -> Dict[str, List[Tuple[str, str]]]:
    """**flat 版**(向后兼容);新代码推荐 :func:`_capability_grouped`。

    返回 ``{cap: [(model_id, source_label), ...]}``,与老版同形以不破现有调用。
    display_name 在 grouped 版有,这里只取 source_label。
    """
    grouped = _capability_grouped()
    out: Dict[str, List[Tuple[str, str]]] = {cap: [] for cap, _ in CAPABILITY_LABELS}
    for cap, groups in grouped.items():
        for group_label, items in groups.items():
            for mid, _display in items:
                out[cap].append((mid, group_label))
    return out


def _capability_grouped() -> Dict[str, Dict[str, List[Tuple[str, str]]]]:
    """**分组版**:每个 capability → ``{group_label: [(model_id, display_name), ...]}``。

    返回值每条候选都是 ``(model_id, display_name)`` 元组:
    * model_id   → 后端真实保存的标识(如 ``paddleocr-zh-fast-9c2d``)
    * display_name → UI 显示的人话名(如 ``PaddleOCR 中文 (快速版)``)

    用户痛点 "下拉模型不能是一串乱码" 由此修:OCR / 视觉这种 hash 后缀的
    model_id 经 ``_friendlify_model_id`` 美化后展示。

    78 题架构修复 — 与 ``/v1/models`` API 共用同一数据源
    -----------------------------------------------------
    历史实现:直接读 yaml + ``seen[cap]`` **跨平台去重**(后到的 platform 同名
    模型被吞)。结果:
      * deepseek 排前 → deepseek 分组有 deepseek-v4-flash;baidu-qianfan 分组缺
      * deepseek 排后 → 完全反过来
      * 跟 ``/v1/models`` API(平铺,不去重)行为不一致 → 用户在 chayuan-client
        看得到 deepseek,但 ④ 默认模型 tab 看不到

    现在改用 ``get_config_platforms()``(与 ``openai_routes.list_models`` 同源),
    **每个 platform 保留自己的模型**,跨平台同名各自独立。chayuan-client 与
    ④ tab 行为完全一致。
    """
    out: Dict[str, Dict[str, List[Tuple[str, str]]]] = {cap: {} for cap, _ in CAPABILITY_LABELS}

    # --- 来源 1:云 / 本地厂商(走 get_config_platforms,与 /v1/models 同源) ---
    try:
        from chayuan.server.utils import get_config_platforms
        platforms = get_config_platforms() or {}
        for pname, pinfo in platforms.items():
            # api_key 非空才出现(防漏配的厂商显示空模型清单)
            api_key_raw = pinfo.get("api_key")
            api_key = (api_key_raw or "").strip() if isinstance(api_key_raw, str) else ""
            if not api_key:
                continue
            ptype = str(pinfo.get("platform_type") or "").lower()
            is_cloud = ptype not in _LOCAL_PLATFORM_TYPES
            group_label = f"云 · {pname}" if is_cloud else f"本地 · {ptype or pname}"
            blacklist = set(pinfo.get("disabled_models") or [])

            for cap, _label in CAPABILITY_LABELS:
                gk = _CAP_TO_GROUP_KEY.get(cap)
                if not gk:
                    continue
                seen_in_group: set = set()
                for m in (pinfo.get(gk) or []):
                    if not isinstance(m, str) or not m:
                        continue
                    if m in blacklist:
                        continue
                    # 仅在**同一 group(同一 platform)内**去重,跨平台同名不影响
                    if m in seen_in_group:
                        continue
                    seen_in_group.add(m)
                    out[cap].setdefault(group_label, []).append(
                        (m, _friendlify_model_id(m))
                    )
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_grouped] platforms read failed: %r", e)

    # --- 来源 2:本地已下载模型(LocalIndexRepository) ---
    try:
        from chayuan.server.ai_platform.repo_bridge import LocalIndexRepository
        repo = LocalIndexRepository()
        for m in repo.list(enabled=True):
            cap = getattr(m, "category", None)
            if cap not in out:
                continue
            mid = getattr(m, "public_id", None) or getattr(m, "id", None)
            rt = getattr(m, "runtime", None) or "local"
            if not mid:
                continue
            display = (
                getattr(m, "display_name", None)
                or getattr(m, "name", None)
                or _friendlify_model_id(str(mid))
            )
            group_label = f"本地 · {rt}"
            # 同 group 内去重(local repo 可能有同 id 残留)
            existing_ids = {x[0] for x in out[cap].get(group_label, [])}
            if str(mid) in existing_ids:
                continue
            out[cap].setdefault(group_label, []).append((str(mid), str(display)))
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_grouped] repo enum failed: %r", e)

    # 93-3 / 94-2:Infinity running 时,**直接调它的 /v1/models** 拿真实
    # 加载的模型清单,按 capability 归类填到 out[clip/embedding/rerank]。
    #
    # 探测顺序:
    #   A. 用户配的外置 endpoint(external_runtimes.yaml)
    #   B. 本地 pip 默认端口(127.0.0.1:7997)
    # 任一通就用它。两者都不通 → 跳过(④ tab 该 capability 仍可能有云厂商候选)。
    _add_infinity_inventory(out)

    # 每组内按 display_name 排序
    for cap, groups in out.items():
        for label in groups:
            groups[label].sort(key=lambda t: t[1].lower())
    return out


def _provider_visual_from_label(group_label: str) -> Dict[str, str]:
    """从分组 label("云 · qwen" / "本地 · ollama")查厂商视觉信息(logo/icon/color)。

    返回:``{logo, color, icon, is_local, pid, display_name, prefix}``。
    pid 不在 PROVIDER_CATALOG 时,display_name 回退到 pid。

    82 题:加 display_name 字段(从 ProviderMeta.display_name 查),供
    expansion header 显示"深度求索 DeepSeek"而非裸 pid "deepseek"。

    用 lazy import 规避 model_config <-> runtime_framework_panel 的循环依赖。
    """
    pid = ""
    is_local = group_label.startswith("本地")
    parts = group_label.split("·")
    prefix = parts[0].strip() if parts else ""
    if len(parts) >= 2:
        pid = parts[1].strip().lower()

    out: Dict[str, str] = {
        "logo": "", "color": "#94a3b8" if is_local else "#6366f1",
        "icon": "memory" if is_local else "cloud",
        "is_local": "1" if is_local else "0",
        "pid": pid,
        "display_name": pid,    # fallback pid;查到 PROVIDER_CATALOG 后会被覆盖
        "prefix": prefix,        # "云" / "本地"
    }
    if not pid:
        return out
    try:
        from chayuan.server.config_panel.model_config import PROVIDER_CATALOG
        for meta in PROVIDER_CATALOG:
            if meta.pid.lower() == pid:
                if meta.logo:
                    out["logo"] = meta.logo
                if meta.color:
                    out["color"] = meta.color
                if meta.icon:
                    out["icon"] = meta.icon
                if meta.display_name:
                    out["display_name"] = meta.display_name
                break
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_defaults] provider_visual lookup failed: %r", e)
    return out


def _render_provider_badge(
    visual: Dict[str, str], group_label: str, models: List[Tuple[str, str]],
) -> None:
    """expansion 头部:logo (or 首字母色块) + 厂商名 + 模型数。"""
    # NiceGUI ui import 不在此模块顶层 — 通过参数传 ui 太繁琐,这里假定调用方
    # 已 with 在 expansion 上下文中,直接用全局 ``ui`` 名字
    import nicegui.ui as ui  # type: ignore[import-untyped]

    pid = (visual.get("pid") or "").strip()
    color = visual.get("color") or "#94a3b8"
    logo_file = visual.get("logo") or ""

    if logo_file:
        # 用 /static/model_logos/<file>(由 config_panel/app.py 挂载)
        ui.html(
            f'<img src="/static/model_logos/{logo_file}" '
            f'style="width:18px;height:18px;border-radius:4px;'
            f'background:#fff;border:1px solid #e5e7eb;flex:0 0 auto;" />'
        )
    else:
        first = (pid[:1] or "?").upper()
        ui.html(
            f'<div style="width:18px;height:18px;border-radius:4px;'
            f'background:{color};color:#fff;display:flex;'
            f'align-items:center;justify-content:center;'
            f'font-size:10px;font-weight:700;flex:0 0 auto;">{first}</div>'
        )
    # 82 题:expansion header 显示 ``{prefix} · {display_name}``,如
    # "云 · 深度求索 DeepSeek" / "本地 · ollama" — 更友好,不再是裸 pid
    display_name = (visual.get("display_name") or "").strip() or pid
    prefix = (visual.get("prefix") or "").strip()
    header_text = f"{prefix} · {display_name}" if prefix else display_name
    ui.label(header_text).style(
        "font-size: 12px; font-weight: 500; color: #1f2937; flex: 1;"
    )
    ui.label(f"({len(models)})").classes("text-caption text-grey-6").style(
        "flex: 0 0 auto;"
    )


# 哪些 platform_type 视为"本地推理框架"
# (云 vs 本地 的判定基于此)
_LOCAL_PLATFORM_TYPES = {
    "ollama", "vllm", "infinity", "comfyui", "llamacpp", "llama-cpp",
    "whispercpp", "whisper-cpp", "funasr", "piper", "cosyvoice",
    "rapidocr", "paddleocr", "lmstudio", "xinference", "gpustack",
    "local", "localai",
}


# 已知模型 ID → 友好名 的覆盖表 (常见 OCR/视觉/嵌入模型);
# 命中即用,不命中走 _friendlify_model_id 自动转换
_MODEL_DISPLAY_OVERRIDES: Dict[str, str] = {
    # OCR
    "rapidocr-onnx-zh": "RapidOCR · 中文 (ONNX)",
    "rapidocr-onnx-en": "RapidOCR · 英文 (ONNX)",
    "paddleocr-zh-fast": "PaddleOCR · 中文 (快速)",
    "paddleocr-zh": "PaddleOCR · 中文",
    "paddleocr-en": "PaddleOCR · 英文",
    # 图像嵌入
    "google/siglip2-base-patch16-224": "SigLIP2 base (224)",
    "google/siglip2-large-patch16-384": "SigLIP2 large (384)",
    "openai/clip-vit-base-patch32": "CLIP ViT-B/32",
    "openai/clip-vit-large-patch14": "CLIP ViT-L/14",
    # 文本嵌入
    "BAAI/bge-m3": "BGE-M3 (多语言)",
    "BAAI/bge-large-zh-v1.5": "BGE-large 中文 v1.5",
    "BAAI/bge-small-zh-v1.5": "BGE-small 中文 v1.5",
    # 重排
    "BAAI/bge-reranker-v2-m3": "BGE Reranker v2 (m3)",
    "BAAI/bge-reranker-large": "BGE Reranker large",
}


def _friendlify_model_id(mid: str) -> str:
    """把 ``paddleocr-zh-fast-9c2d`` 这种 hash 后缀的 ID 转成"PaddleOCR 中文 (快速版)"。

    规则:
    1. 先查 _MODEL_DISPLAY_OVERRIDES (常见模型一对一)
    2. 若 mid 含 ``Owner/Name``,只显示 Name 段(owner 已在 group label)
    3. 末尾 8-16 个十六进制字符 + ``-/_`` 前缀 → 视作 hash, 去掉
    4. ``-`` / ``_`` 切片,词首字母大写化(只对 ASCII 段;v1.5/7B 等保持)
    5. 长度 <= 80;过长截断
    """
    import re

    # 1. overrides
    if mid in _MODEL_DISPLAY_OVERRIDES:
        return _MODEL_DISPLAY_OVERRIDES[mid]

    s = mid
    # 2. owner/name → name
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
        # owner/name 也可能命中 overrides
        if s in _MODEL_DISPLAY_OVERRIDES:
            return _MODEL_DISPLAY_OVERRIDES[s]
    # 3. 去 hash 后缀
    s = re.sub(r"[-_][0-9a-f]{8,16}$", "", s)
    # 4. 词首字母大写
    parts = re.split(r"[-_]", s)
    out_parts = []
    for p in parts:
        if not p:
            continue
        if p.isascii() and p[0].isalpha():
            out_parts.append(p[0].upper() + p[1:])
        else:
            out_parts.append(p)
    label = " ".join(out_parts) or s
    # 5. 长度
    if len(label) > 80:
        label = label[:77] + "..."
    return label


# 模块级 refresh 钩子 — 跨面板的级联刷新,避免"用户改了 A,要手动点 B 刷新"的烂体验。
#   * 改厂商 api_key  → 默认模型选择器需要拉新模型(_CAPABILITY_DEFAULTS_REFRESH)
#   * 启/停 daemon    → 框架卡片状态需要重探(_FRAMEWORK_CARDS_REFRESH)
#   * 启/停 daemon    → 默认模型候选可能变(本地框架开了才能选其本地模型)
_CAPABILITY_DEFAULTS_REFRESH: List[Callable[..., None]] = []
_FRAMEWORK_CARDS_REFRESH: List[Callable[..., None]] = []


def trigger_capability_defaults_refresh() -> None:
    """供其它面板(model_config 保存厂商后)调用,触发默认模型选择器拉新列表。

    62 题:迭代 list(_CAPABILITY_DEFAULTS_REFRESH);_render 内部已加 client
    alive guard,失活时会 self-remove,这里只是兜一层 try/except。
    """
    for fn in list(_CAPABILITY_DEFAULTS_REFRESH):
        try:
            fn(force=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("[capability_defaults] external refresh failed: %r", e)
            # 调用失败的 fn 也直接清掉,避免下次再触发同样错误
            try:
                _CAPABILITY_DEFAULTS_REFRESH.remove(fn)
            except ValueError:
                pass


def trigger_framework_cards_refresh() -> None:
    """启/停 daemon、改 ai_platform repo 后调它,框架卡片重探活。"""
    invalidate_probe_cache()
    for fn in list(_FRAMEWORK_CARDS_REFRESH):
        try:
            fn(force=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("[framework_cards] external refresh failed: %r", e)
            try:
                _FRAMEWORK_CARDS_REFRESH.remove(fn)
            except ValueError:
                pass


def render_capability_defaults_row(
    ui: Any,
    *,
    _prefetched_grouped: Optional[Dict[str, Dict[str, List[Tuple[str, str]]]]] = None,
    _prefetched_defaults: Optional[Dict[str, str]] = None,
) -> Callable[..., None]:
    """9 类 capability 默认模型选择器。**写盘到 model_settings.yaml**。

    返回 ``_render(*, force=False)`` 让外部(如保存厂商配置后)触发刷新。

    Args:
        _prefetched_grouped: 48 题 — wrapper 在 thread 预取的 capability 分组。
            ``None`` 时主线程同步调 ``_capability_grouped()``(yaml 读 + 本地索引扫,
            首次约 100-300ms,卡 NiceGUI asyncio loop)。
        _prefetched_defaults: 同上,预取的 ``_load_capability_defaults()`` 结果。
    """
    container = ui.column().classes("w-full q-mb-sm").style("gap: 6px;")
    # 首次渲染用 prefetched;force=True 时绕过(走重新读 yaml)
    _initial = {"grouped": _prefetched_grouped, "defaults": _prefetched_defaults}

    def _render(*, force: bool = False) -> None:  # noqa: ARG001
        # 62 题:client 已死 → silent return + self-remove(防 _do_cascade_refresh
        # 操作旧 client 触发 NiceGUI "Client has been deleted" 警告,导致
        # 事件循环短暂 race,卡片后续点击被吞)
        from chayuan.server.config_panel._safe_ui import is_client_alive
        if not is_client_alive(container):
            try:
                _CAPABILITY_DEFAULTS_REFRESH.remove(_render)
            except ValueError:
                pass
            return
        # 首次:用 wrapper 预取的数据,跳过同步 IO;后续刷新走原同步路径
        if not force and _initial["grouped"] is not None:
            grouped = _initial["grouped"]
            _initial["grouped"] = None  # 只用一次
        else:
            grouped = _capability_grouped()
        if not force and _initial["defaults"] is not None:
            defaults = dict(_initial["defaults"])
            _initial["defaults"] = None
        else:
            defaults = _load_capability_defaults()

        # 智能默认: 厂商已配好 + 启用了某类型模型,但默认值为空 → 自动选第一个候选
        # 让用户"配完厂商 = 立即可用",不必再去 9 个 capability 一个个挑
        # 48 题:auto-pick 涉及**写盘 9 次**(每个空 cap 一次),首次启动如果 9 个都空
        # 就 9 × 50ms = 450ms 主线程阻塞。把这步**推到 ui.timer 异步执行**,首屏 mount
        # 不等它完成 — 用户先看到 UI(默认值就用空 / 候选第一个 in-memory),写盘后台进行。
        pending_picks: List[Tuple[str, str]] = []
        for cap, _label in CAPABILITY_LABELS:
            if defaults.get(cap):
                continue  # 已有用户保存的默认 — 尊重
            cap_groups = grouped.get(cap, {})
            if not cap_groups:
                continue  # 无候选 — 跳过
            try:
                first_group = next(iter(cap_groups.values()))
                if not first_group:
                    continue
                first_mid = first_group[0][0]
                # in-memory 立即可用(UI 渲染时就显示这个值)
                defaults[cap] = first_mid
                pending_picks.append((cap, first_mid))
            except Exception as e:  # noqa: BLE001
                logger.debug("[capability_defaults] auto-pick %s prep failed: %r", cap, e)

        if pending_picks:
            # 异步写盘 — 不卡当前主线程 mount
            def _flush_picks_async() -> None:
                ok_list: List[str] = []
                for cap, mid in pending_picks:
                    try:
                        ok, _msg = _save_capability_default(cap, mid)
                        if ok:
                            ok_list.append(f"{_CAP_TO_ZH.get(cap, cap)}={mid}")
                    except Exception as e:  # noqa: BLE001
                        logger.debug("[capability_defaults] async-save %s failed: %r", cap, e)
                if ok_list:
                    logger.info(
                        "[capability_defaults] 自动选默认(异步落盘): %s",
                        "; ".join(ok_list),
                    )

            try:
                ui.timer(0.5, _flush_picks_async, once=True)
            except Exception as e:  # noqa: BLE001
                # ui.timer 不可用就退回同步 — 至少保证写盘
                logger.debug("[capability_defaults] schedule async save failed: %r", e)
                _flush_picks_async()
        container.clear()
        with container:
            with ui.card().classes("w-full").props("flat bordered").style(
                "background: #fafbfc; padding: 10px 12px;"
            ):
                with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                    ui.icon("auto_awesome", size="18px").classes("text-grey-7")
                    ui.label("默认模型选择(9 类能力)").classes("text-subtitle2")
                    ui.label(
                        "下拉按 云供应商 / 本地框架 分组;候选 = "
                        "(enabled=true 且 api_key 非空) 的云模型 ∪ 本地已下载模型"
                    ).classes("text-caption text-grey-6")
                    ui.space()

                    def _full_refresh() -> None:
                        # 默认模型选择器自身重渲染 + 框架卡片重探活
                        # 用户直觉:点"刷新"= 把这屏所有相关数据都刷一遍
                        _render(force=True)
                        trigger_framework_cards_refresh()

                    ui.button(
                        "刷新", icon="refresh",
                        on_click=_full_refresh,
                    ).props("dense flat color=primary size=sm").tooltip(
                        "重新拉取候选模型 + 探活所有框架"
                    )

                # 4 列 wrap (与第一行框架卡片同密度)
                with ui.row().classes("w-full q-mt-sm").style(
                    "gap: 8px; flex-wrap: wrap;"
                ):
                    for cap, label in CAPABILITY_LABELS:
                        _render_cap_card(cap, label, grouped.get(cap, {}), defaults.get(cap, ""))

    def _render_cap_card(
        cap: str, label: str,
        groups: Dict[str, List[Tuple[str, str]]], current: str,
    ) -> None:
        with ui.card().props("flat bordered").style(
            "flex: 0 0 calc((100% - 24px) / 4); min-width: 220px; "
            "padding: 8px 10px;"
        ):
            with ui.row().classes("items-center w-full no-wrap").style("gap: 6px;"):
                ui.label(label).style("font-weight: 600; font-size: 13px;")
                ui.label(f"({cap})").classes("text-caption text-grey-6").style(
                    "font-family: ui-monospace, monospace;"
                )
                ui.space()
                # 89-8:clip 行右侧显示运行位置标识(在线 / 降级 / 未配置)
                if cap == "clip":
                    _render_clip_runtime_badge(ui)
                if current:
                    ui.label("已选").classes("text-caption").style(
                        "background: #ecfdf5; color: #065f46; "
                        "border-radius: 4px; padding: 0 4px; font-size: 10px;"
                    )
            if not groups:
                # 104 题:即使 groups 空也 mount **主按钮 + 诊断菜单**。之前直接
                # return,用户看到"已选"chip 但没有任何可点元素,体验像"被锁死"。
                # 实际上 groups 空一般是:用户在 ② 厂商配了模型但 5s platforms
                # cache 还没失效,或 yaml 写盘了但 state_cache.grouped 未刷新。
                # 给用户:
                #   * 主按钮(标 "(无候选,点击诊断)")— 让点击有反馈
                #   * 弹出菜单 — 显示已选 model id + "刷新候选" + "清除选择"
                empty_btn = ui.button(
                    f"({_friendlify_model_id(current) if current else '未选'},无候选)  ▾",
                ).props("flat align=between no-caps dense").classes("w-full q-mt-xs").style(
                    "border: 1px solid #fde68a; border-radius: 6px; "
                    "justify-content: space-between; padding: 4px 10px; "
                    "font-weight: 400; text-align: left; "
                    "background: #fffbeb; color: #92400e;"
                )
                empty_menu = ui.menu().props("auto-close=false")
                with empty_btn:
                    empty_menu

                with empty_menu:
                    with ui.column().style(
                        "min-width: 320px; max-width: 420px; "
                        "padding: 8px 12px; gap: 6px;"
                    ):
                        ui.label(f"{label} · 暂无可选候选").classes(
                            "text-subtitle2"
                        ).style("color: #92400e;")
                        ui.label(
                            "可能原因:\n"
                            "  • 厂商已配但未点【💾 保存】(草稿不会出现在候选)\n"
                            f"  • 厂商已保存但分组字段不匹配 (期待: "
                            f"{_CAP_TO_GROUP_KEY.get(cap, '?')})\n"
                            "  • platforms 5s 缓存还没刷新 — 点【刷新候选】"
                        ).classes("text-caption text-grey-7").style(
                            "white-space: pre-wrap; line-height: 1.5;"
                        )

                        if current:
                            with ui.row().classes("items-center w-full").style(
                                "gap: 6px; padding: 4px 6px; "
                                "background: #ecfdf5; border-radius: 4px;"
                            ):
                                ui.icon("check_circle", size="14px").classes(
                                    "text-positive"
                                )
                                ui.label(f"已选: {current}").classes(
                                    "text-caption"
                                ).style(
                                    "font-family: ui-monospace, monospace; "
                                    "color: #065f46; flex: 1;"
                                )

                        with ui.row().classes("items-center w-full no-wrap q-mt-sm").style(
                            "gap: 6px;"
                        ):
                            def _refresh_now(_e=None, _menu=empty_menu) -> None:
                                # 强制 bump platform cache + 重渲整张默认模型卡片
                                try:
                                    from chayuan.server.db.repository.model_platform_repository import (
                                        bump_platform_version,
                                    )
                                    bump_platform_version()
                                except Exception:  # noqa: BLE001
                                    # DB 不可用 → 5s TTL 自然过期,功能仍可用
                                    pass
                                try:
                                    from chayuan.server.config_panel.model_settings \
                                        import state_cache
                                    state_cache.invalidate("grouped", "defaults")
                                except Exception:  # noqa: BLE001
                                    pass
                                _menu.close()
                                _render(force=True)
                                ui.notify(
                                    f"已刷新 {label} 候选清单", type="info",
                                )

                            ui.button(
                                "刷新候选", icon="refresh",
                                on_click=_refresh_now,
                            ).props("dense unelevated color=primary size=sm")

                            if current:
                                def _clear_current(_e=None, _cap=cap, _menu=empty_menu) -> None:
                                    ok, msg = _save_capability_default(_cap, "")
                                    ui.notify(
                                        f"{_CAP_TO_ZH[_cap]} → 清除 · {msg}",
                                        type="positive" if ok else "negative",
                                    )
                                    _menu.close()
                                    _render(force=True)

                                ui.button(
                                    "清除当前选择", icon="clear",
                                    on_click=_clear_current,
                                ).props("dense flat color=grey-7 size=sm")
                return

            # ============== 按厂商分组 + 可折叠的弹出选择器(NEW) ==============
            #
            # 替代之前的扁平 ui.select(用 ── 分隔符模拟分组,但下拉里都展开看
            # 不清).改为:
            #   * 按钮 → 弹出菜单
            #   * 菜单内每个 group 是 ui.expansion(默认折叠,点击展开)
            #   * 当前选中项所在 group 默认展开,其它折叠 — 减视觉噪音
            #   * 模型项是 button,点击 → 写盘 + 关菜单
            #
            # NiceGUI / Quasar 原生 ui.select 不支持子组折叠,所以这是必要的
            # 自定义实现。

            # 当前显示文本(按钮上):用友好名,如 "Claude Sonnet 4.6"
            current_display = "(未选)"
            current_group = ""
            for group_label, models in groups.items():
                for mid, disp in models:
                    if mid == current:
                        current_display = disp or mid
                        current_group = group_label
                        break

            # 主按钮(显示当前选)
            trigger_btn = ui.button(
                f"{current_display}  ▾",
            ).props("flat align=between no-caps dense").classes("w-full q-mt-xs").style(
                "border: 1px solid #e5e7eb; border-radius: 6px; "
                "justify-content: space-between; padding: 4px 10px; "
                "font-weight: 400; text-align: left;"
            )

            # 菜单,挂在按钮下方
            menu = ui.menu().props("auto-close=false")
            with trigger_btn:
                menu

            with menu:
                with ui.column().style(
                    "min-width: 320px; max-width: 480px; "
                    "max-height: 480px; overflow-y: auto; "
                    "padding: 4px 0; gap: 0;"
                ):
                    # 顶部搜索框
                    search = ui.input(placeholder="搜索模型 / 厂商").props(
                        "dense outlined clearable"
                    ).classes("q-mx-sm q-mb-xs").style("font-size: 12px;")

                    expansions: List[Any] = []
                    rows_by_group: Dict[str, List[Any]] = {}

                    for group_label, models in groups.items():
                        # **所有厂商组默认展开** — 用户期望打开下拉就看到所有候选
                        # 上一版本只展开"当前选中项所在组",但用户反馈视觉割裂
                        # (大多数 group 默认折叠 → 看不到能选什么 → 还得一个个点开)
                        # 改为全展开;搜索时仍按命中收缩
                        # 从 group_label("云 · qwen" / "本地 · ollama")抽 pid 查厂商 logo/icon/color
                        visual = _provider_visual_from_label(group_label)
                        with ui.expansion(
                            value=True,
                        ).classes("w-full").style(
                            "border-bottom: 1px solid #f3f4f6;"
                        ).props("dense") as exp:
                            with exp.add_slot("header"):
                                with ui.row().classes("items-center w-full no-wrap").style(
                                    "gap: 8px; padding: 4px 8px;"
                                ):
                                    # logo 优先,无 logo 退回首字母色块
                                    _render_provider_badge(visual, group_label, models)
                            expansions.append(exp)
                            for mid, disp in models:
                                # 每个模型一行 button
                                disp_text = disp or mid
                                # 当前选中显示对勾
                                tick = "✓ " if mid == current else "  "
                                row_btn = ui.button(
                                    f"{tick}{disp_text}",
                                ).props("flat dense no-caps align=left").classes("w-full").style(
                                    "justify-content: flex-start; "
                                    "padding: 3px 24px; font-size: 12px; "
                                    "color: " + ("#2563eb" if mid == current else "#1f2937") + ";"
                                )

                                def _on_pick(_e: Any = None,
                                             _mid: str = mid,
                                             _cap: str = cap,
                                             _btn: Any = trigger_btn,
                                             _menu: Any = menu) -> None:
                                    ok, msg = _save_capability_default(_cap, _mid)
                                    ui.notify(
                                        f"{_CAP_TO_ZH[_cap]} → {_friendlify_model_id(_mid)} · {msg}",
                                        type="positive" if ok else "negative",
                                    )
                                    if ok:
                                        _btn.set_text(f"{_friendlify_model_id(_mid)}  ▾")
                                    _menu.close()

                                row_btn.on("click", _on_pick)
                                rows_by_group.setdefault(group_label, []).append(
                                    (mid, disp_text, row_btn)
                                )

                    def _on_search(e: Any) -> None:
                        q = (str(e.args or "")).strip().lower()
                        # 搜索为空 → 全部展开(与初始默认一致)
                        if not q:
                            for group_label, exp in zip(rows_by_group.keys(), expansions):
                                exp.value = True
                                for _mid, _disp, btn in rows_by_group[group_label]:
                                    btn.style(remove="display: none;")
                            return
                        # 搜索时:命中行显示;命中行所在 group 自动展开
                        for group_label, exp in zip(rows_by_group.keys(), expansions):
                            any_match = False
                            for _mid, _disp, btn in rows_by_group[group_label]:
                                hit = q in _mid.lower() or q in _disp.lower() or q in group_label.lower()
                                if hit:
                                    btn.style(remove="display: none;")
                                    any_match = True
                                else:
                                    btn.style("display: none;")
                            exp.value = any_match

                    search.on("update:model-value", _on_search)

    _render()
    # 注册到全局,让 model_config 保存厂商后能调到这里
    _CAPABILITY_DEFAULTS_REFRESH.append(_render)
    return _render


__all__ = [
    "CAPABILITY_LABELS",
    "RuntimeHealth",
    "probe_framework",
    "probe_all_frameworks",
    "invalidate_probe_cache",
    "render_runtime_framework_row",
    "render_capability_defaults_row",
    "trigger_capability_defaults_refresh",
    "trigger_framework_cards_refresh",
    "_CAP_TO_YAML_KEY",
    "_FRAMEWORK_CATALOG",
]
