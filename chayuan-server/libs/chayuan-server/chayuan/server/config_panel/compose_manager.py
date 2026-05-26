"""docker-compose 统一管理 — 单一真源(端口 / 镜像 / 容器名 / 网络)。

设计原则
==========
1. **专属目录** — ``<CHAYUAN_ROOT>/compose/docker-compose.yaml`` 是配置中心,
   与 modality wrapper 配置 ``<CHAYUAN_ROOT>/runtime/`` **分开存放**:
     * compose 文件是声明式容器编排(端口 / 镜像 / 网络)
     * runtime/*.yaml 是 wrapper 进程参数(model_path / device 等)
2. **项目初始化时生成** — ``chayuan init`` 流程会调 :func:`ensure_compose_file`
   写默认模板,新机器零手动配置即可启动
3. **链式迁移** 兼容老路径,启动时按顺序探测并自动迁到新位置:
     ``~/.chayuan/runtime/docker-compose.yaml``
        → ``<CHAYUAN_ROOT>/runtime/docker-compose.yaml``  (上一版本临时位置)
        → ``<CHAYUAN_ROOT>/compose/docker-compose.yaml``  ← 新规范位置
4. **用户可编辑** — 弹窗"⚙ 配置"tab 直接显示这个 yaml 让用户改,
   保存后下次启动就用新配置
5. **探活从 yaml 读** — 不再硬编码 default_url,探活用 yaml 里的 ports

4 个 docker 类 framework 统一走 compose:
* vllm, infinity, comfyui, llamacpp

非 docker 框架(funasr/cosyvoice/...) 不影响 — 它们有自己的 wrapper。

操作
====
* ``compose up -d <service>`` 启动单个
* ``compose stop <service>`` 停止
* ``compose ps <service>`` 状态
* ``compose pull <service>`` 单独拉镜像

为什么比 ``docker run`` 稳
==========================
* 端口冲突 / 容器名冲突 docker-compose 自动处理
* 容器之间能用 service name 当 hostname 互联
* 重启系统后 ``compose up -d`` 一行恢复全部
* yaml 是声明式 — 改 yaml 就改了真实状态,无须记 100+ 行 docker run 命令
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.compose_manager")


# 走 compose 管理的 framework 名(必须是 _FRAMEWORK_CATALOG 里 install_kind=docker 的)
COMPOSE_MANAGED_FRAMEWORKS: List[str] = [
    "vllm", "infinity", "comfyui", "llamacpp",
]


def get_compose_file_path() -> Path:
    """``<CHAYUAN_ROOT>/compose/docker-compose.yaml`` 默认路径(专属目录)。

    优先级:
      1. 环境变量 ``CHAYUAN_DOCKER_COMPOSE_FILE``(显式指定整文件)
      2. :func:`chayuan.paths.compose_config_path` ``("docker-compose.yaml")``
         即 ``<CHAYUAN_ROOT>/compose/docker-compose.yaml``;
         支持 ``$CHAYUAN_COMPOSE_HOME`` 覆盖整目录

    调用本函数之前应先调 :func:`ensure_compose_file`,它会按链路迁移老路径
    并在新位置不存在时写默认模板。
    """
    env = os.environ.get("CHAYUAN_DOCKER_COMPOSE_FILE", "").strip()
    if env:
        return Path(env)
    from chayuan.paths import compose_config_path
    return compose_config_path("docker-compose.yaml")


def _legacy_compose_locations() -> List[Path]:
    """按老到新的顺序列出 docker-compose.yaml 历史位置。"""
    out: List[Path] = []
    # 1.x 时期:硬编码 ~/.chayuan/runtime/
    out.append(Path.home() / ".chayuan" / "runtime" / "docker-compose.yaml")
    # 2.x 早期:跟 modality wrapper 同居 <CHAYUAN_ROOT>/runtime/
    try:
        from chayuan.paths import runtime_config_path
        out.append(runtime_config_path("docker-compose.yaml"))
    except Exception:  # noqa: BLE001
        pass
    return out


def _migrate_legacy_compose_file(target: Path) -> bool:
    """把老位置的 docker-compose.yaml 迁到新专属目录。

    * 已有 target 不动 — 用户在新位置改过的优先
    * 找到第一个老位置就拷贝过去,**保留老文件**(只读不删)
    * 拷贝失败吞错,不影响主流程
    返回 True 表示迁移发生。
    """
    if target.exists():
        return False
    for src in _legacy_compose_locations():
        try:
            if src.resolve() == target.resolve():
                continue
        except OSError:
            continue
        if not src.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            logger.info("[compose] migrated legacy file: %s -> %s", src, target)
            return True
        except OSError as e:
            logger.warning("[compose] migrate failed (%s -> %s): %r", src, target, e)
            continue
    return False


def get_compose_binary() -> Optional[List[str]]:
    """返回可用的 docker compose 命令(新版用 ``docker compose``,老版 ``docker-compose``)。

    都没有时返 None — 上层应回退到 docker run 老路径。
    """
    if shutil.which("docker"):
        # 优先 docker compose v2 plugin
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


# ============================================================================
# 默认 compose 模板 — 与 _FRAMEWORK_CATALOG 默认值对齐
# ============================================================================

_DEFAULT_COMPOSE_TEMPLATE = """\
# Chayuan 统一容器编排配置(Phase 2 — 加 healthcheck + Phase 4 — 增强)
# ---------------------------------------------------------------------------
# 此文件由 chayuan 首次启动自动生成。可编辑,保存后下次启动生效。
# 改了端口/镜像后,chayuan 探活会自动 parse 此文件读真实值,无需改代码。
#
# 启动单服务:        docker compose -f <thisfile> up -d <service>
# 启动并等就绪:      docker compose -f <thisfile> up -d --wait <service>
# 停止单服务:        docker compose -f <thisfile> stop <service>
# 查看状态:          docker compose -f <thisfile> ps
# 看日志:            docker compose -f <thisfile> logs -f <service>
#
# Profiles 说明:
#   --profile gpu   启用 GPU 推理(vllm / comfyui)— 需 NVIDIA Docker
#   --profile cpu   启用 CPU 模式(性能差,但无 GPU 也能跑)
#   不指定 profile  → 仅启动无 profile 的 service(infinity / llamacpp)
# ---------------------------------------------------------------------------
version: "3.8"

# 共享网络 — 所有 chayuan 编排的 service 用同一 bridge,可通过 service name 互连
networks:
  chayuan-net:
    name: chayuan-net
    driver: bridge

# 命名卷 — 持久化模型文件 / 生成内容,容器删除后数据保留
volumes:
  vllm-models:           # vLLM HuggingFace 模型缓存
  comfyui-models:        # ComfyUI 模型 + 工作流
  comfyui-output:        # 生成图片
  llamacpp-models:       # llama.cpp gguf 模型

services:

  # ════════════════════════════════════════════════════════════════════════
  # vLLM — 高吞吐 LLM 推理(GPU 推荐)
  # ════════════════════════════════════════════════════════════════════════
  vllm:
    image: vllm/vllm-openai:latest
    container_name: chayuan-vllm
    restart: unless-stopped
    ports:
      - "18000:8000"
    networks: [chayuan-net]
    volumes:
      - vllm-models:/root/.cache/huggingface
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8000/health || exit 1"]
      interval: 10s
      timeout: 5s
      start_period: 60s    # 给加载模型时间(大模型可能 30-60s)
      retries: 3
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    profiles: [gpu]   # docker compose --profile gpu up -d --wait vllm

  # vLLM CPU 模式(性能较差,仅无 GPU 时使用)
  vllm-cpu:
    image: vllm/vllm-openai:latest-cpu     # 注意:CPU 镜像 tag 可能需调整
    container_name: chayuan-vllm-cpu
    restart: unless-stopped
    ports:
      - "18000:8000"
    networks: [chayuan-net]
    volumes:
      - vllm-models:/root/.cache/huggingface
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8000/health || exit 1"]
      interval: 10s
      timeout: 5s
      start_period: 120s   # CPU 模式启动慢
      retries: 3
    profiles: [cpu]
    # ⚠ CPU 模式性能远差于 GPU,大模型可能加载几分钟,请耐心等待

  # ════════════════════════════════════════════════════════════════════════
  # Infinity — 文本嵌入 / 重排 server(CPU 也能跑,GPU 加速可选)
  # ════════════════════════════════════════════════════════════════════════
  infinity:
    image: michaelf34/infinity:latest
    container_name: chayuan-infinity
    restart: unless-stopped
    ports:
      - "37997:7997"
    networks: [chayuan-net]
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:7997/health || exit 1"]
      interval: 10s
      timeout: 5s
      start_period: 30s
      retries: 3
    # GPU 加速(可选);取消注释下面两段启用 NVIDIA 加速
    # runtime: nvidia
    # environment:
    #   - NVIDIA_VISIBLE_DEVICES=all

  # ════════════════════════════════════════════════════════════════════════
  # ComfyUI — Stable Diffusion 工作流(GPU 强烈推荐)
  # ════════════════════════════════════════════════════════════════════════
  comfyui:
    image: ghcr.io/comfyanonymous/comfyui:latest
    container_name: chayuan-comfyui
    restart: unless-stopped
    ports:
      - "18188:8188"
    networks: [chayuan-net]
    volumes:
      - comfyui-models:/app/models
      - comfyui-output:/app/output
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8188/ || exit 1"]
      interval: 10s
      timeout: 5s
      start_period: 60s
      retries: 3
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    runtime: nvidia
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
    profiles: [gpu]
    # ⚠ 没 GPU 时几乎不能用 — 单张 SD 出图 CPU 模式要 5-10 分钟

  # ════════════════════════════════════════════════════════════════════════
  # llama.cpp — CPU 友好的 LLM 推理(默认启用,无需 GPU)
  # ════════════════════════════════════════════════════════════════════════
  llamacpp:
    image: ghcr.io/ggerganov/llama.cpp:server
    container_name: chayuan-llamacpp
    restart: unless-stopped
    ports:
      - "18080:8080"
    networks: [chayuan-net]
    volumes:
      - llamacpp-models:/models
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8080/health || exit 1"]
      interval: 10s
      timeout: 5s
      start_period: 30s
      retries: 3
    # llama.cpp 默认 CPU,GPU 加速需用 :server-cuda image
"""


def ensure_compose_file() -> Path:
    """确保 docker-compose.yaml 在专属目录存在。

    顺序:
      1. 计算新位置 ``<CHAYUAN_ROOT>/compose/docker-compose.yaml``
      2. **链式迁移** — 不存在时尝试从老位置(``~/.chayuan/runtime/`` 或
         ``<CHAYUAN_ROOT>/runtime/``)拷贝过来
      3. 还不存在(纯新装) → 写默认模板

    `chayuan init` 会调本函数完成项目初始化,首次启动时也会兜底调一次。
    """
    p = get_compose_file_path()
    if p.exists():
        return p

    # 链式迁移老位置文件
    if _migrate_legacy_compose_file(p):
        return p

    # 纯新装 — 写默认模板
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_DEFAULT_COMPOSE_TEMPLATE, encoding="utf-8")
        logger.info("[compose] wrote default template: %s", p)
    except Exception as e:  # noqa: BLE001
        logger.warning("[compose] write default failed: %r", e)
    return p


def load_compose() -> Dict[str, Any]:
    """读 docker-compose.yaml,返回 dict;不存在 / 解析失败返空 dict。"""
    p = get_compose_file_path()
    if not p.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[compose] parse failed: %r", e)
        return {}


# ============================================================================
# yaml 字段读取
# ============================================================================

def get_service_definition(service: str) -> Dict[str, Any]:
    """单个 service 的完整定义。"""
    doc = load_compose()
    services = doc.get("services") or {}
    return services.get(service) or {}


def get_service_ports(service: str) -> List[str]:
    """从 yaml 读端口映射列表,返 ``["18000:8000", ...]``;无返空 list。"""
    sdef = get_service_definition(service)
    ports = sdef.get("ports")
    if isinstance(ports, list):
        return [str(p) for p in ports]
    return []


def get_service_host_port(service: str) -> Optional[int]:
    """抽 service 的第一个 host 端口(如 ``18000:8000`` → 18000)。"""
    for p in get_service_ports(service):
        # 格式 "<host>:<container>" 或 "<host>:<container>/<protocol>"
        h = p.split(":", 1)[0].strip()
        try:
            return int(h)
        except ValueError:
            continue
    return None


def get_service_container_name(service: str) -> Optional[str]:
    """从 yaml 读容器名(``container_name`` 字段);无则 fallback ``<project>_<service>``。"""
    sdef = get_service_definition(service)
    cn = sdef.get("container_name")
    return str(cn) if cn else None


def get_service_image(service: str) -> Optional[str]:
    sdef = get_service_definition(service)
    img = sdef.get("image")
    return str(img) if img else None


def is_managed(framework: str) -> bool:
    """该 framework 是否走 compose 管理。"""
    return framework in COMPOSE_MANAGED_FRAMEWORKS


# ============================================================================
# 55 题:每服务独立 yaml 文件目录扫描机制
# ============================================================================
#
# 设计:
#   <CHAYUAN_ROOT>/compose/
#     ├── docker-compose.yaml          # 老式聚合(向后兼容,可选保留)
#     ├── vllm.yaml                    # 单 service yaml
#     ├── infinity.yaml                # 单 service yaml
#     ├── ...                          # 用户可自加
#     ├── sources/                     # 56-B 题各源 yaml 模板
#     ├── compose-services/            # (废弃名,留空)
#     └── active_source.txt            # 当前源名标记
#
# 扫描规则:
#   * 扫 <CHAYUAN_ROOT>/compose/*.yaml(递归 *不* 进 sources/)
#   * 排除聚合 docker-compose.yaml(避免重复)
#   * 每个文件的 ``services:`` 顶层 key 视为该 yaml 提供的 service
#   * 一个文件通常只含 1 个 service(配套 README 推荐),但允许多个

@dataclass
class ComposeServiceFile:
    """单个 docker-compose yaml 文件 + 它声明的 service 列表。"""
    file_path: Path
    services: List[str]  # yaml 里 services: 下的 key 列表

    @property
    def primary_service(self) -> str:
        """通常 yaml 内只有 1 个 service。多个时返第一个,UI 显示第一个为主。"""
        return self.services[0] if self.services else self.file_path.stem


# 排除项:扫描时不当 service yaml 看待的文件名
_SCAN_EXCLUDE_FILENAMES = {"docker-compose.yaml", "docker-compose.yml"}
# 排除项:扫描时不进入的子目录
_SCAN_EXCLUDE_DIRS = {"sources", "templates", ".bak", "_backup"}


def list_compose_service_files() -> List[ComposeServiceFile]:
    """扫描 ``<CHAYUAN_ROOT>/compose/*.yaml``,返每个文件 + 它声明的 service 列表。

    用户在该目录新增 yaml 文件,UI"运行时与服务"会自动出现新卡片。

    返回顺序:按文件名字典序;聚合 docker-compose.yaml 不在内。
    """
    out: List[ComposeServiceFile] = []
    base = get_compose_file_path().parent  # <CHAYUAN_ROOT>/compose/
    if not base.exists():
        return out

    try:
        files = sorted(base.glob("*.yaml")) + sorted(base.glob("*.yml"))
    except OSError:
        return out

    seen_names: set = set()
    for f in files:
        if f.name.lower() in _SCAN_EXCLUDE_FILENAMES:
            continue
        if f.parent.name in _SCAN_EXCLUDE_DIRS:
            continue
        # parse yaml,拿 services 列表
        try:
            import yaml
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except Exception as e:  # noqa: BLE001
            logger.debug("[scan] %s parse failed: %r", f, e)
            continue
        if not isinstance(doc, dict):
            continue
        services = doc.get("services") or {}
        if not isinstance(services, dict) or not services:
            continue
        names = list(services.keys())
        # 去重保护(同名 service 多文件时,后扫的覆盖)
        names = [n for n in names if n not in seen_names]
        for n in names:
            seen_names.add(n)
        if not names:
            continue
        out.append(ComposeServiceFile(file_path=f, services=names))
    return out


def get_compose_file_for_service(service: str) -> Optional[Path]:
    """根据 service 名找其所在的 yaml 文件。

    扫描顺序:
      1. ``<CHAYUAN_ROOT>/compose/<service>.yaml``(命名约定,优先)
      2. 扫描其它 yaml 看它们的 ``services:`` 顶层是否含此 service
      3. 都没有 → 返 None
    """
    base = get_compose_file_path().parent
    # 命名约定优先
    direct = base / f"{service}.yaml"
    if direct.exists():
        return direct
    direct_yml = base / f"{service}.yml"
    if direct_yml.exists():
        return direct_yml
    # 扫描其他文件
    for csf in list_compose_service_files():
        if service in csf.services:
            return csf.file_path
    return None


def get_template_compose_dir() -> Optional[Path]:
    """返回仓库内 ``server-compose/`` 目录(模板源)。

    搜索路径:
      1. ``<package_root>/../server-compose``(libs/chayuan-server/server-compose)
      2. ``<repo_root>/libs/chayuan-server/server-compose``
      3. 环境变量 ``CHAYUAN_SERVER_COMPOSE_TEMPLATES``

    都不存在返 None。
    """
    env = os.environ.get("CHAYUAN_SERVER_COMPOSE_TEMPLATES", "").strip()
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    # __file__ → libs/chayuan-server/chayuan/server/config_panel/compose_manager.py
    # 上溯 4 级到 libs/chayuan-server/
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "server-compose",   # libs/chayuan-server/server-compose
        here.parents[4] / "libs" / "chayuan-server" / "server-compose",  # repo root
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def seed_compose_services_from_templates(*, force: bool = False) -> Dict[str, int]:
    """init / 首次启动时把仓库 ``server-compose/*.yaml`` 复制到 ``<CHAYUAN_ROOT>/compose/``。

    Args:
        force: True 时强制覆盖已存在文件(慎用,会丢用户编辑)

    返回:::

        {"copied": int, "skipped_existing": int, "skipped_no_template": int}
    """
    report = {"copied": 0, "skipped_existing": 0, "skipped_no_template": 0}
    src_dir = get_template_compose_dir()
    if src_dir is None:
        logger.info("[seed-compose] 找不到 server-compose 模板目录,跳过")
        report["skipped_no_template"] = 1
        return report

    dst_dir = get_compose_file_path().parent
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("[seed-compose] 创建 %s 失败: %r", dst_dir, e)
        return report

    import shutil
    for tmpl in sorted(src_dir.glob("*.yaml")):
        # 排除 README / 辅助文件
        target = dst_dir / tmpl.name
        if target.exists() and not force:
            report["skipped_existing"] += 1
            continue
        try:
            shutil.copy2(tmpl, target)
            logger.info("[seed-compose] 复制 %s → %s", tmpl.name, target)
            report["copied"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("[seed-compose] 复制 %s 失败: %r", tmpl.name, e)

    # README.md 也复制(给用户看)
    readme = src_dir / "README.md"
    if readme.exists():
        target_readme = dst_dir / "README.md"
        if not target_readme.exists() or force:
            try:
                shutil.copy2(readme, target_readme)
            except Exception:  # noqa: BLE001
                pass
    return report


# ============================================================================
# 端口冲突自动处理(45 题)
# ============================================================================

def is_port_in_use(port: int, host: str = "0.0.0.0") -> bool:
    """检查 host:port 是否已被占用(用 socket bind 试探,毫秒级)。

    Windows / Linux / macOS 都支持。bind 失败即占用。
    """
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # SO_REUSEADDR 不开,严格判断
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


def find_free_port(start: int = 18000, end: int = 65535, host: str = "0.0.0.0") -> int:
    """从 start 开始找第一个空闲端口。

    超出 end 抛 RuntimeError。
    """
    if start < 1024:
        start = 1024
    for p in range(start, min(end, 65535) + 1):
        if not is_port_in_use(p, host=host):
            return p
    raise RuntimeError(f"no free port in range [{start}, {end}]")


def _update_yaml_service_port(
    service: str,
    new_host_port: int,
    *,
    file_path: Optional[Path] = None,
) -> bool:
    """把 service 的第一个 host port 改成 new_host_port,写回 yaml。

    注意:用 PyYAML 写会丢失注释 — 我们的默认模板有大量注释,这是已知 trade-off。
    用户编辑过的 yaml 第一次 auto-realloc 后注释会被规范化。要保住注释需要
    ruamel.yaml,引入依赖暂不做。

    返回 True 表示改成功。
    """
    p = file_path or get_compose_file_path()
    if not p.exists():
        logger.warning("[port-realloc] %s 不存在,跳过", p)
        return False
    try:
        import yaml
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[port-realloc] yaml 解析失败:%r", e)
        return False
    if not isinstance(doc, dict):
        return False
    services = doc.get("services") or {}
    sdef = services.get(service)
    if not isinstance(sdef, dict):
        return False
    ports = sdef.get("ports")
    if not isinstance(ports, list) or not ports:
        return False

    # 改第一个 ``"<host>:<container>"``
    first = ports[0]
    if isinstance(first, str) and ":" in first:
        _, container_part = first.split(":", 1)
        ports[0] = f"{new_host_port}:{container_part}"
    elif isinstance(first, dict):
        # 长格式 ``{published: 18000, target: 8000}``
        first["published"] = new_host_port
    else:
        return False
    sdef["ports"] = ports

    try:
        import yaml
        p.write_text(
            yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info(
            "[port-realloc] %s host port → %d(写入 %s,注释将丢失)",
            service, new_host_port, p,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[port-realloc] 写 yaml 失败:%r", e)
        return False


def auto_reallocate_port_if_occupied(service: str) -> Optional[int]:
    """检测 service 的 host port 是否被占用;占用则自动换并写回 yaml。

    返回:
      * 新端口(int):成功重分配
      * None:未改动(端口空闲 / 找不到 service / yaml 写失败)

    用例:::

        new_port = auto_reallocate_port_if_occupied("infinity")
        if new_port:
            ui.notify(f"端口 37997 被占,已自动改用 {new_port}")
        # 然后 ContainerLifecycle.up(...) 用新端口起容器
    """
    cur = get_service_host_port(service)
    if cur is None:
        return None
    if not is_port_in_use(cur):
        return None
    # 从 cur+1 起找,避开"老进程没退干净"的可能(给 cur+1 优先级)
    try:
        new_port = find_free_port(start=cur + 1)
    except RuntimeError as e:
        logger.warning("[port-realloc] %s: %s", service, e)
        return None
    if not _update_yaml_service_port(service, new_port):
        return None
    return new_port


# ============================================================================
# 命令生成 — 给 install_task_manager / install_recipes 用
# ============================================================================

def make_up_cmd(service: str) -> Optional[List[str]]:
    """``docker compose -f <file> up -d <service>``。"""
    binary = get_compose_binary()
    if not binary:
        return None
    p = ensure_compose_file()
    return [*binary, "-f", str(p), "up", "-d", service]


def make_stop_cmd(service: str) -> Optional[List[str]]:
    """``docker compose -f <file> stop <service>``。"""
    binary = get_compose_binary()
    if not binary:
        return None
    p = ensure_compose_file()
    return [*binary, "-f", str(p), "stop", service]


def make_pull_cmd(service: str) -> Optional[List[str]]:
    """``docker compose -f <file> pull <service>``(单独拉镜像)。"""
    binary = get_compose_binary()
    if not binary:
        return None
    p = ensure_compose_file()
    return [*binary, "-f", str(p), "pull", service]


def make_ps_cmd() -> Optional[List[str]]:
    """``docker compose -f <file> ps`` — 看所有 service 状态。"""
    binary = get_compose_binary()
    if not binary:
        return None
    p = ensure_compose_file()
    return [*binary, "-f", str(p), "ps"]


__all__ = [
    "COMPOSE_MANAGED_FRAMEWORKS",
    "get_compose_file_path",
    "get_compose_binary",
    "ensure_compose_file",
    "load_compose",
    "get_service_definition",
    "get_service_ports",
    "get_service_host_port",
    "get_service_container_name",
    "get_service_image",
    "is_managed",
    "make_up_cmd",
    "make_stop_cmd",
    "make_pull_cmd",
    "make_ps_cmd",
    "is_port_in_use",
    "find_free_port",
    "auto_reallocate_port_if_occupied",
    # 55 题:每服务独立 yaml
    "ComposeServiceFile",
    "list_compose_service_files",
    "get_compose_file_for_service",
    "get_template_compose_dir",
    "seed_compose_services_from_templates",
]
