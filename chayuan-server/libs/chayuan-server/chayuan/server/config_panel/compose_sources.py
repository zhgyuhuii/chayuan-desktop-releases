"""docker-compose 多源管理(56-B 题)。

设计目标
========
让用户在不同镜像源之间一键切换,解决:
  * 国内拉 ``vllm/vllm-openai:latest``(Docker Hub) 慢 / 失败
  * 不同部署环境(海外 / 国内 / 内网私服)需要不同镜像 prefix
  * 用户已编辑的端口 / volumes 切源时要保留

目录结构
========
::

    <CHAYUAN_ROOT>/compose/
    ├── docker-compose.yaml             # 当前活动源(由 activate_source 写)
    ├── active_source.txt               # 记录当前是哪个源
    └── sources/
        ├── official.yaml               # 默认 Docker Hub(内置)
        ├── daocloud.yaml               # daocloud.io 国内镜像(内置)
        ├── 1ms.yaml                    # 1ms.run 国内镜像(内置)
        ├── aliyun.yaml                 # 阿里云国内(内置)
        └── <user-custom>.yaml          # 用户自定义源(可选)

切换语义
========
* ``activate_source(name)`` = 复制 ``sources/<name>.yaml`` 到 ``docker-compose.yaml``
* **保留用户已编辑的端口 / volumes / 环境变量**(merge 而非 overwrite)
* 写 ``active_source.txt`` 标记当前源
* UI 重启容器才生效:``docker compose up -d <service>`` 用新镜像

各源的差异
==========
仅 ``image:`` 字段不同(镜像 prefix);其他配置(ports/healthcheck/volumes/networks)继承 official。
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("chayuan.compose_sources")


# ============================================================================
# 内置源定义 — 只列出 image 重映射(其他字段继承 official 模板)
# ============================================================================

@dataclass
class ComposeSource:
    name: str          # 源 id(英文,文件名)
    label: str         # UI 显示名
    description: str   # 说明
    image_map: Dict[str, str]  # framework_name → 镜像名(替换 official 默认)


# 4 个 docker service 在各源的镜像名
# - vllm:           vllm/vllm-openai:latest
# - infinity:       michaelf34/infinity:latest
# - comfyui:        ghcr.io/comfyanonymous/comfyui:latest
# - llamacpp:       ghcr.io/ggerganov/llama.cpp:server

_BUILTIN_SOURCES: List[ComposeSource] = [
    ComposeSource(
        name="official",
        label="官方源 (Docker Hub)",
        description="海外网络优选;国内可能慢或失败",
        image_map={
            "vllm":       "vllm/vllm-openai:latest",
            "vllm-cpu":   "vllm/vllm-openai:latest-cpu",
            "infinity":   "michaelf34/infinity:latest",
            "comfyui":    "ghcr.io/comfyanonymous/comfyui:latest",
            "llamacpp":   "ghcr.io/ggerganov/llama.cpp:server",
        },
    ),
    ComposeSource(
        name="daocloud",
        label="DaoCloud (国内)",
        description="国内 docker.m.daocloud.io 镜像加速;通用稳定",
        image_map={
            "vllm":       "docker.m.daocloud.io/vllm/vllm-openai:latest",
            "vllm-cpu":   "docker.m.daocloud.io/vllm/vllm-openai:latest-cpu",
            "infinity":   "docker.m.daocloud.io/michaelf34/infinity:latest",
            "comfyui":    "ghcr.m.daocloud.io/comfyanonymous/comfyui:latest",
            "llamacpp":   "ghcr.m.daocloud.io/ggerganov/llama.cpp:server",
        },
    ),
    ComposeSource(
        name="1ms",
        label="1ms.run (国内)",
        description="国内 docker.1ms.run 镜像;近期稳定",
        image_map={
            "vllm":       "docker.1ms.run/vllm/vllm-openai:latest",
            "vllm-cpu":   "docker.1ms.run/vllm/vllm-openai:latest-cpu",
            "infinity":   "docker.1ms.run/michaelf34/infinity:latest",
            "comfyui":    "ghcr.1ms.run/comfyanonymous/comfyui:latest",
            "llamacpp":   "ghcr.1ms.run/ggerganov/llama.cpp:server",
        },
    ),
    ComposeSource(
        name="aliyun",
        label="阿里云 (国内)",
        description="阿里云 registry.cn-hangzhou.aliyuncs.com;企业用户优选",
        image_map={
            "vllm":       "registry.cn-hangzhou.aliyuncs.com/chayuan/vllm-openai:latest",
            "vllm-cpu":   "registry.cn-hangzhou.aliyuncs.com/chayuan/vllm-openai:latest-cpu",
            "infinity":   "registry.cn-hangzhou.aliyuncs.com/chayuan/infinity:latest",
            "comfyui":    "registry.cn-hangzhou.aliyuncs.com/chayuan/comfyui:latest",
            "llamacpp":   "registry.cn-hangzhou.aliyuncs.com/chayuan/llama-cpp-server:latest",
        },
    ),
]


_SOURCE_BY_NAME: Dict[str, ComposeSource] = {s.name: s for s in _BUILTIN_SOURCES}


# ============================================================================
# 路径 helpers
# ============================================================================


def get_sources_dir() -> Path:
    """``<CHAYUAN_ROOT>/compose/sources/`` — 各源 yaml 模板。"""
    from chayuan.paths import compose_config_dir
    p = compose_config_dir() / "sources"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def get_active_source_marker() -> Path:
    """``<CHAYUAN_ROOT>/compose/active_source.txt`` — 当前源标记。"""
    from chayuan.paths import compose_config_dir
    return compose_config_dir() / "active_source.txt"


# ============================================================================
# 源管理 API
# ============================================================================


def list_compose_sources() -> List[ComposeSource]:
    """列出所有可用源(内置 + 用户自定义)。"""
    out = list(_BUILTIN_SOURCES)
    # 扫描 user-custom yaml 文件 (sources/<name>.yaml,但不在内置列表里)
    try:
        sources_dir = get_sources_dir()
        builtin_names = {s.name for s in _BUILTIN_SOURCES}
        for f in sorted(sources_dir.glob("*.yaml")):
            stem = f.stem
            if stem in builtin_names:
                continue
            out.append(ComposeSource(
                name=stem,
                label=f"自定义 · {stem}",
                description=f"用户自定义源 ({f.name})",
                image_map={},  # 自定义源完全自由,不强制 image_map
            ))
    except Exception as e:  # noqa: BLE001
        logger.debug("list user sources failed: %r", e)
    return out


def get_active_source_name() -> str:
    """读 active_source.txt 返回当前源名;无标记返 'official'(默认)。"""
    marker = get_active_source_marker()
    if marker.exists():
        try:
            return marker.read_text(encoding="utf-8").strip() or "official"
        except OSError:
            pass
    return "official"


def _generate_source_yaml(source: ComposeSource) -> str:
    """从 official 默认模板克隆一份,把 image 字段替换成 source.image_map 里的镜像。"""
    from chayuan.server.config_panel.compose_manager import _DEFAULT_COMPOSE_TEMPLATE
    import yaml as _yaml

    doc = _yaml.safe_load(_DEFAULT_COMPOSE_TEMPLATE) or {}
    services = doc.get("services") or {}
    for svc_name, svc_def in services.items():
        if not isinstance(svc_def, dict):
            continue
        new_image = source.image_map.get(svc_name)
        if new_image:
            svc_def["image"] = new_image
    # 顶部注释带源标识
    header = (
        f"# Chayuan compose 源:{source.label}\n"
        f"# {source.description}\n"
        f"# 切换源:UI 模型配置 → 运行时与服务 → 切换镜像源 chip 行\n"
        f"#\n"
    )
    return header + _yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def ensure_source_files() -> List[Path]:
    """确保 sources/ 目录下的内置源 yaml 都存在(首次启动生成)。"""
    sources_dir = get_sources_dir()
    written: List[Path] = []
    for source in _BUILTIN_SOURCES:
        target = sources_dir / f"{source.name}.yaml"
        if target.exists():
            continue
        try:
            target.write_text(_generate_source_yaml(source), encoding="utf-8")
            logger.info("[compose-sources] 生成内置源:%s", target)
            written.append(target)
        except OSError as e:
            logger.warning("[compose-sources] 写 %s 失败:%r", target, e)
    return written


def activate_source(name: str, *, preserve_user_edits: bool = True) -> bool:
    """切换到指定源:复制 ``sources/<name>.yaml`` 到 ``docker-compose.yaml``。

    Args:
        name: 源 id(official / daocloud / 1ms / aliyun / 用户自定义名)
        preserve_user_edits: True 时保留用户在主 yaml 已编辑的字段
            (端口 / volumes / environment / 自加的 service)— 仅 image / image-prefix
            会被新源覆盖

    返回 True 表示切换成功。
    """
    sources_dir = get_sources_dir()
    src_yaml = sources_dir / f"{name}.yaml"

    # 先确保内置源文件存在
    if not src_yaml.exists() and name in _SOURCE_BY_NAME:
        ensure_source_files()
    if not src_yaml.exists():
        logger.warning("[compose-sources] 源 %s 不存在 (%s)", name, src_yaml)
        return False

    # 读源 yaml
    try:
        import yaml as _yaml
        new_doc = _yaml.safe_load(src_yaml.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("[compose-sources] 读 %s 失败:%r", src_yaml, e)
        return False

    # 准备目标 yaml 路径
    from chayuan.server.config_panel.compose_manager import (
        get_compose_file_path, ensure_compose_file,
    )
    ensure_compose_file()  # 确保主 yaml 存在
    target = get_compose_file_path()

    if preserve_user_edits and target.exists():
        try:
            import yaml as _yaml
            cur_doc = _yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            # merge:对每个 service,从 cur 复制 ports / volumes / environment / runtime
            # 等"用户配置",从 new 复制 image
            cur_services = cur_doc.get("services") or {}
            new_services = new_doc.get("services") or {}
            for svc_name, new_svc in new_services.items():
                if not isinstance(new_svc, dict):
                    continue
                cur_svc = cur_services.get(svc_name) or {}
                if not isinstance(cur_svc, dict):
                    continue
                # 从用户当前 yaml 保留这些字段
                for keep_key in (
                    "ports", "volumes", "environment", "deploy",
                    "depends_on", "runtime", "container_name", "restart",
                    "networks", "profiles",
                ):
                    if keep_key in cur_svc:
                        new_svc[keep_key] = cur_svc[keep_key]
            # 写回
            target.write_text(
                _yaml.safe_dump(new_doc, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[compose-sources] merge 失败,退回直接复制:%r", e,
            )
            shutil.copy2(src_yaml, target)
    else:
        shutil.copy2(src_yaml, target)

    # 写 active marker
    try:
        get_active_source_marker().write_text(name, encoding="utf-8")
    except OSError:
        pass

    logger.info(
        "[compose-sources] 已切换到 %s (preserved=%s) → %s",
        name, preserve_user_edits, target,
    )
    return True


__all__ = [
    "ComposeSource",
    "list_compose_sources",
    "get_active_source_name",
    "ensure_source_files",
    "activate_source",
]
