"""跨平台 × 多镜像源 × 多框架 安装命令矩阵。

为什么不写在 runtime_framework_panel?
=====================================

矩阵随框架增长会膨胀;需要 (framework, host_os, mirror_source) 三维查询。
独立成模块可被 install_dialog / CLI doctor / docs 共用。

数据模型
========

每个 framework 有一组 :class:`InstallRecipe`,UI 把它们渲染成可选 chip。
当前镜像源命名:
    "official"     —— 上游官方
    "cn-mirror"    —— 国内镜像 (中科大 / 清华 / 阿里云)
    "docker-hub"   —— Docker Hub
    "quay"         —— quay.io 镜像
    "aliyun-acr"   —— 阿里云容器镜像服务
    "tencent-tcr"  —— 腾讯云容器镜像
    "github-cn"    —— GitHub 国内代理
    "custom"       —— 用户填的自定义

任何一个 recipe 缺平台命令会 fall back 到 docker(任意系统都能跑)。
"""
from __future__ import annotations

import os
import platform as _platform
import sys
from typing import Dict, List, Optional

from chayuan.server.config_panel.install_task_manager import InstallRecipe


# 镜像源 → UI 显示名 + 国旗/分类色
MIRROR_SOURCES: Dict[str, Dict[str, str]] = {
    "official":     {"label": "官方上游",     "color": "#3b82f6"},
    "cn-mirror":    {"label": "国内镜像",     "color": "#f59e0b"},
    "docker-hub":   {"label": "Docker Hub",   "color": "#0ea5e9"},
    "daocloud":     {"label": "DaoCloud 镜像","color": "#10b981"},
    "1ms":          {"label": "1ms.run",      "color": "#10b981"},
    "xuanyuan":     {"label": "玄垣镜像",     "color": "#10b981"},
    "quay":         {"label": "Quay.io",      "color": "#8b5cf6"},
    "aliyun-acr":   {"label": "阿里云 ACR",   "color": "#f97316"},
    "tencent-tcr":  {"label": "腾讯云 TCR",   "color": "#06b6d4"},
    "github-cn":    {"label": "GitHub 国内",  "color": "#84cc16"},
    "custom":       {"label": "自定义",       "color": "#71717a"},
}


# Docker 多镜像源前缀 — 把 ``image`` 改写成 ``<mirror>/<image>``;
# 默认走 docker.io (Docker Hub),国内网络换 daocloud / 1ms / xuanyuan。
DOCKER_MIRRORS: Dict[str, str] = {
    "docker-hub":  "docker.io",                          # 官方默认
    "daocloud":    "docker.m.daocloud.io",               # DaoCloud 一阶国内
    "1ms":         "docker.1ms.run",                     # 1ms.run
    "xuanyuan":    "docker.xuanyuan.me",                 # 玄垣
    "aliyun":      "registry.cn-hangzhou.aliyuncs.com",  # 阿里云 ACR
}


# 已下架 / 维护停止 / 路径变更的官方镜像 → 替代镜像 + 搜索引导
#
# 维护原则:用户报错才加;每条都附 reason 和 search_hint 让用户能自行验证。
# docker hub / ghcr 都偶尔 rebrand,这张表会有持续更新。
IMAGE_REWRITES: Dict[str, Dict[str, str]] = {
    "yanwk/comfyui-boot": {
        "replacement": "ghcr.io/comfyanonymous/comfyui:latest",
        "reason": "yanwk/comfyui-boot 已停更; 切到上游官方 GHCR 镜像",
        "search_hint": "https://github.com/comfyanonymous/ComfyUI/pkgs/container/comfyui",
    },
    # llama.cpp 项目 2025 年从 ggerganov/llama.cpp 移交 ggml-org/llama.cpp 组织;
    # 老 ghcr.io/ggerganov/llama.cpp 路径返回 not found
    "ghcr.io/ggerganov/llama.cpp": {
        "replacement": "ghcr.io/ggml-org/llama.cpp:server",
        "reason": "llama.cpp 项目移交 ggml-org 组织, 老 GHCR 镜像 404",
        "search_hint": "https://github.com/ggml-org/llama.cpp/pkgs/container/llama.cpp",
    },
}


def docker_mirror_image(original_image: str, mirror_key: str) -> str:
    """把 image 重写到 ``<mirror_host>/<image>``。

    规则:
    1. 先查 IMAGE_REWRITES — 命中直接换替代 (用户最痛的"已下架/路径变更"场景);
       支持完整路径 ``ghcr.io/x/y`` 和短名 ``x/y`` 双种 key 命中
    2. mirror_key=="docker-hub" → 原样返回
    3. image 已带域名(``ghcr.io`` / ``quay.io`` / ``registry.*``)→ 保留
       (已带域名说明用户明确指定了 registry,不强行 mirror)
    4. 否则前缀 mirror host
    """
    base = original_image.split(":", 1)[0]
    # 完整路径或短名都查一次
    rewrite = IMAGE_REWRITES.get(base) or IMAGE_REWRITES.get(original_image)
    if rewrite:
        return rewrite["replacement"]
    if mirror_key == "docker-hub" or mirror_key not in DOCKER_MIRRORS:
        return original_image
    mirror_host = DOCKER_MIRRORS[mirror_key]
    first_part = original_image.split("/", 1)[0]
    if "." in first_part or first_part == "localhost":
        # ghcr.io / quay.io / registry.* — 已含域名,跳过
        return original_image
    return f"{mirror_host}/{original_image}"


def make_compose_recipe(service: str) -> List[InstallRecipe]:
    """生成 ``docker compose up`` 的安装 recipe — 一条搞定。

    docker compose 自动管理:镜像拉取 / 容器创建 / 端口映射 / 重启策略,
    比 ``docker run`` 一长串参数稳。失败时换镜像源仍可用 — 改 yaml 里
    ``image:`` 字段重启即可。

    83 题:每个服务独立 yaml(``vllm.yaml`` / ``ollama.yaml`` ...),
    不再使用聚合的 ``docker-compose.yaml``。``container_lifecycle``
    会自动找 ``<CHAYUAN_ROOT>/compose/<service>.yaml`` 并 ``-f`` 指定。
    """
    return [InstallRecipe(
        label=f"docker compose up {service}",
        cmd=["docker", "compose", "up", "-d", service],
        note=f"自动从 <CHAYUAN_ROOT>/compose/{service}.yaml 读端口/镜像/网络配置",
        requires="docker",
    )]


def make_docker_recipes(
    base_image: str,
    *,
    port_map: str,
    container_name: str,
    extra_args: Optional[List[str]] = None,
    label_prefix: str = "Docker",
    note_extra: str = "",
) -> List[InstallRecipe]:
    """生成同 image 的 N 条 docker run recipe (优先活源)。

    新逻辑(2026-04+):
        1. 先 ``mirror_pool.probe_pool("docker_hub")`` 探活全部候选源
        2. 把"活"的源排在前面,挂掉的源排在后面(还是给出 — 万一探活假阴)
        3. 用户在 install_dialog 看到顺序就是"通的源在前",点第一个 chip
           大概率成功;若失败可手动切到后面的备选

    探活带 5min 缓存,同 pool 反复调不耗时。
    """
    out: List[InstallRecipe] = []
    extra = list(extra_args or [])
    base = base_image.split(":", 1)[0]
    rewrite_note = ""
    if base in IMAGE_REWRITES:
        rw = IMAGE_REWRITES[base]
        rewrite_note = f" · 注: {rw['reason']}"

    # 通过 mirror_pool 探活,把 alive 源排在前 — 用户 chip 第一个永远是活源
    alive_set: set = set()
    try:
        from chayuan.server.config_panel.mirror_pool import probe_pool
        alive_entries = probe_pool("docker_hub")
        # mirror_pool URL 与 install_recipes 的 DOCKER_MIRRORS 用 url(域名)对齐
        # 反查 mirror_key
        url_to_key = {url: k for k, url in DOCKER_MIRRORS.items()}
        # 加上 mirror_pool 里 registry-1.docker.io 对应 docker-hub
        url_to_key["registry-1.docker.io"] = "docker-hub"
        for e in alive_entries:
            k = url_to_key.get(e.url)
            if k:
                alive_set.add(k)
    except Exception as e:
        # mirror_pool 不可用(httpx 缺/网络异常),退回老路径(全部 4 个都给)
        import logging
        logging.getLogger("install_recipes").debug("mirror probe failed: %r", e)

    # 固定 fallback 顺序:DaoCloud 先(完整度最高的国内源),1ms 因 EOF / layer 缺频繁排后
    all_keys = ("daocloud", "docker-hub", "1ms", "xuanyuan")
    if alive_set:
        ordered = (
            tuple(k for k in all_keys if k in alive_set) +
            tuple(k for k in all_keys if k not in alive_set)
        )
    else:
        ordered = all_keys

    for mirror_key in ordered:
        image = docker_mirror_image(base_image, mirror_key)
        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "--restart", "unless-stopped",
            "-p", port_map,
            *extra,
            image,
        ]
        # alive 源 label 加 ✓ 标记;dead 源加 ⚠ 提示用户它当前不通
        is_alive = (not alive_set) or (mirror_key in alive_set)
        marker = "" if is_alive else " ⚠"
        out.append(InstallRecipe(
            label=f"{label_prefix} · {MIRROR_SOURCES.get(mirror_key, {}).get('label', mirror_key)}{marker}",
            cmd=cmd,
            note=f"image: {image}{rewrite_note} {note_extra}"
                 f"{'' if is_alive else ' (源探活未通,建议优先选前面的)'}".strip(),
            requires="docker",
        ))
    return out


def _host_os() -> str:
    sys_name = _platform.system().lower()
    if "darwin" in sys_name:
        return "mac"
    if "windows" in sys_name:
        return "win"
    return "linux"


# ---------------------------------------------------------------------------
# 各框架 recipes 映射
# (framework_name, mirror_source, host_os) → cmd
# ---------------------------------------------------------------------------

def get_recipes_simplified(framework: str) -> List[InstallRecipe]:
    """52 题:**每个 framework 只返一种安装方式**。

    决策表(优先级从高到低):
      1. docker compose(install_kind=docker 的服务 — vllm/infinity/comfyui/...)
      2. 单个 docker run(没 compose 但有 docker run)
      3. pip 主源(没 docker 但有 pip)
      4. 其他(curl install.sh 之类的官方脚本)— 兜底返第一个

    说明:
    * 用户已表态"有 docker 就只用 docker,没有就 pip"
    * 不再让用户在 docker / pip / git clone 之间纠结
    * 如需多镜像源选择,仍可调 :func:`get_recipes` 拿全部
    """
    all_recipes = get_recipes(framework)
    if not all_recipes:
        return []
    # 占位 / 自定义 recipe 单独保留(没有真实安装命令)
    if len(all_recipes) == 1:
        return all_recipes
    # 1) docker compose
    for r in all_recipes:
        if (len(r.cmd) >= 2 and r.cmd[0] == "docker" and r.cmd[1] == "compose"):
            return [r]
    # 2) docker run
    for r in all_recipes:
        if (len(r.cmd) >= 2 and r.cmd[0] == "docker" and r.cmd[1] == "run"):
            return [r]
    # 3) pip(主源)— pip / pip3 命令
    for r in all_recipes:
        if r.cmd and r.cmd[0] in ("pip", "pip3"):
            return [r]
    # 3b) 嵌入 pip 的复合命令(如 _pip_with_mirrors 用 sys.executable -m pip)
    for r in all_recipes:
        if "pip" in (r.requires or "") or any(
            "pip" in str(t) for t in r.cmd[:4]
        ):
            return [r]
    # 4) 兜底返第一个(curl install.sh / git clone 等)
    return [all_recipes[0]]


def get_recipes(framework: str) -> List[InstallRecipe]:
    """返回当前主机平台下,该 framework 所有可用镜像源的 recipes。"""
    host = _host_os()
    if framework == "ollama":
        return _ollama(host)
    if framework == "vllm":
        return _vllm(host)
    if framework == "infinity":
        return _infinity(host)
    if framework == "comfyui":
        return _comfyui(host)
    if framework == "llamacpp":
        return _llamacpp(host)
    if framework == "whispercpp":
        return _whispercpp(host)
    if framework == "funasr":
        return _pip_with_mirrors("funasr")
    if framework == "piper":
        return _piper(host)
    if framework == "cosyvoice":
        return _pip_with_mirrors("cosyvoice")
    if framework == "voxcpm2":
        return _voxcpm2(host)
    if framework == "rapidocr":
        return _pip_with_mirrors("rapidocr-onnxruntime")
    if framework == "paddleocr":
        return _pip_with_mirrors("paddlepaddle paddleocr")
    if framework == "docker":
        return _docker_install(host)
    if framework == "docker-compose":
        return _docker_compose_install(host)
    return []


# ---------------------------------------------------------------------------
# Docker 自身 (是其它 docker 服务的依赖,优先安装)
# ---------------------------------------------------------------------------

def _docker_install(host: str) -> List[InstallRecipe]:
    out: List[InstallRecipe] = []
    if host == "linux":
        out.append(InstallRecipe(
            label="官方一键脚本",
            cmd=["bash", "-lc", "curl -fsSL https://get.docker.com | sh"],
            note="官方 install script;首次需 sudo systemctl start docker",
        ))
        out.append(InstallRecipe(
            label="国内镜像加速 (DaoCloud)",
            cmd=["bash", "-lc",
                 "curl -fsSL https://get.daocloud.io/docker | sh"],
            note="DaoCloud 镜像版 install.sh",
        ))
    elif host == "mac":
        out.append(InstallRecipe(
            label="Docker Desktop (推荐)",
            cmd=["open", "https://www.docker.com/products/docker-desktop/"],
            note="官方 .dmg;手动下载安装",
        ))
        out.append(InstallRecipe(
            label="Homebrew",
            cmd=["brew", "install", "--cask", "docker"],
            requires="brew",
        ))
    elif host == "win":
        out.append(InstallRecipe(
            label="Docker Desktop (推荐)",
            cmd=["start", "https://www.docker.com/products/docker-desktop/"],
            note="官方 .exe 安装包",
        ))
        out.append(InstallRecipe(
            label="winget",
            cmd=["winget", "install", "-e", "--id", "Docker.DockerDesktop"],
            requires="winget",
        ))
    return out


def _docker_compose_install(host: str) -> List[InstallRecipe]:
    """新版 Docker Desktop / Engine 自带 compose v2 (`docker compose ...`),
    但部分老系统仍需手装 compose-plugin。"""
    out: List[InstallRecipe] = []
    out.append(InstallRecipe(
        label="新版 Docker 已自带 (compose v2)",
        cmd=["docker", "compose", "version"],
        note="若返回版本号即已就绪;否则按下方装",
        requires="docker",
    ))
    if host == "linux":
        out.append(InstallRecipe(
            label="apt 安装 compose-plugin",
            cmd=["bash", "-lc",
                 "sudo apt-get install -y docker-compose-plugin"],
        ))
        out.append(InstallRecipe(
            label="独立二进制 (compose v2)",
            cmd=["bash", "-lc",
                 "curl -fsSL "
                 "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s | tr A-Z a-z)-$(uname -m) "
                 "-o ~/.local/bin/docker-compose && chmod +x ~/.local/bin/docker-compose"],
            note="无 sudo 装到 ~/.local/bin",
        ))
    elif host == "mac":
        out.append(InstallRecipe(
            label="Homebrew (compose plugin)",
            cmd=["brew", "install", "docker-compose"],
            requires="brew",
        ))
    elif host == "win":
        out.append(InstallRecipe(
            label="Windows: Docker Desktop 自带 compose v2",
            cmd=["docker", "compose", "version"],
            note="安装 Docker Desktop 后已自带;无需额外操作",
            requires="docker",
        ))
    return out




# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def _ollama(host: str) -> List[InstallRecipe]:
    out: List[InstallRecipe] = []
    # Docker 优先(跨平台、稳定)
    out.append(InstallRecipe(
        label="Docker · 官方镜像",
        cmd=["docker", "run", "-d", "-p", "11434:11434",
             "--name", "ollama", "--restart", "unless-stopped",
             "ollama/ollama"],
        note="任意系统;需 docker",
        requires="docker",
    ))
    out.append(InstallRecipe(
        label="Docker · 阿里云 ACR 镜像",
        cmd=["docker", "run", "-d", "-p", "11434:11434",
             "--name", "ollama", "--restart", "unless-stopped",
             "registry.cn-hangzhou.aliyuncs.com/ollama/ollama"],
        note="国内 docker 网络快;需 docker",
        requires="docker",
    ))
    # 平台原生方式作为 fallback
    if host == "linux":
        out.append(InstallRecipe(
            label="官方 curl 脚本",
            cmd=["bash", "-lc", "curl -fsSL https://ollama.com/install.sh | sh"],
            note="上游官方;海外网络流畅时首选",
        ))
        out.append(InstallRecipe(
            label="国内 mirror.ghproxy.com",
            cmd=["bash", "-lc",
                 "curl -fsSL https://mirror.ghproxy.com/https://ollama.com/install.sh | sh"],
            note="国内网络;走 ghproxy 加速",
        ))
        out.append(InstallRecipe(
            label="阿里云 OSS 镜像 (ollama-linux-amd64)",
            cmd=["bash", "-lc",
                 "curl -fsSL https://aliyun-mirror.example.com/ollama/install.sh | sh"],
            note="阿里云 OSS 镜像;请按本机 ACR 替换 endpoint",
        ))
    elif host == "mac":
        out.append(InstallRecipe(
            label="Homebrew (cask)",
            cmd=["brew", "install", "--cask", "ollama"],
            note="需 brew",
            requires="brew",
        ))
        out.append(InstallRecipe(
            label="官方 .dmg 直链",
            cmd=["bash", "-lc",
                 "curl -fsSL https://ollama.com/download/Ollama-darwin.zip -o /tmp/ollama.zip"
                 " && unzip -o /tmp/ollama.zip -d /Applications/"],
            note="不依赖 brew",
        ))
    elif host == "win":
        out.append(InstallRecipe(
            label="winget",
            cmd=["winget", "install", "-e", "--id", "Ollama.Ollama"],
            requires="winget",
        ))
        out.append(InstallRecipe(
            label="Scoop",
            cmd=["scoop", "install", "ollama"],
            requires="scoop",
        ))
    return out


# ---------------------------------------------------------------------------
# vLLM
# ---------------------------------------------------------------------------

def _vllm(host: str) -> List[InstallRecipe]:
    # docker compose 优先(端口/容器/GPU 全在 yaml 配,一条命令启)
    out = list(make_compose_recipe("vllm"))
    # docker run 老路径(给不用 compose 的用户)
    out.extend(make_docker_recipes(
        "vllm/vllm-openai:latest",
        port_map="18000:8000",
        container_name="vllm",
        extra_args=["--runtime", "nvidia", "--gpus", "all"],
        label_prefix="Docker · GPU",
        note_extra="需 NVIDIA GPU + nvidia-container-toolkit",
    ))
    out.extend(_pip_with_mirrors("vllm"))
    return out


# ---------------------------------------------------------------------------
# Infinity (text-embedding / rerank server)
# ---------------------------------------------------------------------------

def _infinity(host: str) -> List[InstallRecipe]:
    # docker compose 优先(端口/镜像在 yaml 配)
    out = list(make_compose_recipe("infinity"))
    # docker run 老路径(若用户不用 compose)
    out.extend(make_docker_recipes(
        "michaelf34/infinity:latest",
        port_map="37997:7997",
        container_name="infinity",
        label_prefix="Docker",
    ))
    out.extend(_pip_with_mirrors("infinity-emb[all]"))
    return out


# ---------------------------------------------------------------------------
# ComfyUI
# ---------------------------------------------------------------------------

def _comfyui(host: str) -> List[InstallRecipe]:
    out: List[InstallRecipe] = []
    # 0. docker compose 优先(端口 / GPU 配在 yaml)
    out.extend(make_compose_recipe("comfyui"))
    # 1. Docker 4 镜像源(若用户不用 compose,提供 docker run 老路径)
    out.extend(make_docker_recipes(
        "yanwk/comfyui-boot",
        port_map="18188:8188",
        container_name="comfyui",
        label_prefix="Docker",
    ))
    # 2. pip 包(comfy-cli)— 装好后可 `comfy launch` 启动,跨平台
    out.extend(_pip_with_mirrors("comfy-cli"))
    # 3. git clone 源码 — 老式手动方式,作为兜底
    if host == "win":
        out.append(InstallRecipe(
            label="git clone (官方)",
            cmd=["git", "clone",
                 "https://github.com/comfyanonymous/ComfyUI.git",
                 "%USERPROFILE%\\ComfyUI"],
            note="需 Git for Windows;后续 pip install -r requirements.txt",
            requires="git",
        ))
        out.append(InstallRecipe(
            label="git clone (国内代理 ghproxy)",
            cmd=["git", "clone",
                 "https://mirror.ghproxy.com/https://github.com/comfyanonymous/ComfyUI.git",
                 "%USERPROFILE%\\ComfyUI"],
            note="国内网络优选",
            requires="git",
        ))
    else:
        out.append(InstallRecipe(
            label="git clone (官方)",
            cmd=["git", "clone",
                 "https://github.com/comfyanonymous/ComfyUI.git",
                 os.path.expanduser("~/ComfyUI")],
            note="海外快;后续 pip install -r requirements.txt",
            requires="git",
        ))
        out.append(InstallRecipe(
            label="git clone (国内代理 ghproxy)",
            cmd=["git", "clone",
                 "https://mirror.ghproxy.com/https://github.com/comfyanonymous/ComfyUI.git",
                 os.path.expanduser("~/ComfyUI")],
            note="国内网络优选",
            requires="git",
        ))
    return out


# ---------------------------------------------------------------------------
# llama.cpp
# ---------------------------------------------------------------------------

def _llamacpp(host: str) -> List[InstallRecipe]:
    out: List[InstallRecipe] = []
    # docker compose 优先
    out.extend(make_compose_recipe("llamacpp"))
    # Docker 4 镜像源 fallback
    out.extend(make_docker_recipes(
        "ghcr.io/ggerganov/llama.cpp",   # base 名;rewrite 会换到 ggml-org/llama.cpp:server
        port_map="18080:8080",
        container_name="llamacpp",
        label_prefix="Docker",
    ))
    # 平台原生方式作为 fallback
    if host == "mac":
        out.append(InstallRecipe(label="Homebrew", cmd=["brew", "install", "llama.cpp"]))
    if host == "linux":
        out.append(InstallRecipe(
            label="官方 release 二进制",
            cmd=["bash", "-lc",
                 "curl -fsSL -o /tmp/llamacpp.zip "
                 "https://github.com/ggml-org/llama.cpp/releases/latest/download/llama-bin-linux-x64.zip "
                 "&& unzip -o /tmp/llamacpp.zip -d ~/.local/bin"],
        ))
        out.append(InstallRecipe(
            label="国内代理 release",
            cmd=["bash", "-lc",
                 "curl -fsSL -o /tmp/llamacpp.zip "
                 "https://mirror.ghproxy.com/https://github.com/ggml-org/llama.cpp/releases/latest/download/llama-bin-linux-x64.zip "
                 "&& unzip -o /tmp/llamacpp.zip -d ~/.local/bin"],
        ))
    if host == "win":
        for label, url in [
            ("官方 release zip (PowerShell)",
             "https://github.com/ggml-org/llama.cpp/releases/latest/download/llama-bin-win-x64.zip"),
            ("国内代理 ghproxy (PowerShell)",
             "https://mirror.ghproxy.com/https://github.com/ggml-org/llama.cpp/releases/latest/download/llama-bin-win-x64.zip"),
            ("国内代理 gh-proxy.com (PowerShell)",
             "https://gh-proxy.com/https://github.com/ggml-org/llama.cpp/releases/latest/download/llama-bin-win-x64.zip"),
        ]:
            out.append(InstallRecipe(
                label=label,
                cmd=["powershell", "-NoProfile", "-Command",
                     f"$tmp = \"$env:TEMP\\llamacpp.zip\"; "
                     f"Invoke-WebRequest -UseBasicParsing -Uri '{url}' -OutFile $tmp; "
                     f"Expand-Archive -Force -Path $tmp -DestinationPath \"$env:LOCALAPPDATA\\llamacpp\""],
                note="Windows 原生;-UseBasicParsing 兼容禁用 IE 引擎",
            ))
    return out


# ---------------------------------------------------------------------------
# whisper.cpp
# ---------------------------------------------------------------------------

def _whispercpp(host: str) -> List[InstallRecipe]:
    out: List[InstallRecipe] = []
    if host == "mac":
        out.append(InstallRecipe(label="Homebrew", cmd=["brew", "install", "whisper-cpp"]))
    if host == "win":
        # Windows: 用 git for windows clone + cmake build (不依赖 bash/make)
        out.append(InstallRecipe(
            label="git clone + cmake (PowerShell)",
            cmd=["powershell", "-NoProfile", "-Command",
                 "$dir = \"$env:LOCALAPPDATA\\whisper.cpp\"; "
                 "git clone https://github.com/ggerganov/whisper.cpp.git $dir; "
                 "cd $dir; "
                 "cmake -B build; cmake --build build --config Release"],
            note="需 Git + CMake + MSVC;无 bash 依赖",
            requires="git",
        ))
    else:
        out.append(InstallRecipe(
            label="git clone (官方) + make",
            cmd=["bash", "-lc",
                 "git clone https://github.com/ggerganov/whisper.cpp.git ~/whisper.cpp "
                 "&& cd ~/whisper.cpp && make"],
        ))
        out.append(InstallRecipe(
            label="git clone (国内) + make",
            cmd=["bash", "-lc",
                 "git clone https://mirror.ghproxy.com/https://github.com/ggerganov/whisper.cpp.git ~/whisper.cpp "
                 "&& cd ~/whisper.cpp && make"],
        ))
    return out


# ---------------------------------------------------------------------------
# Piper TTS
# ---------------------------------------------------------------------------

def _voxcpm2(host: str) -> List[InstallRecipe]:
    """VoxCPM 2 安装方案 — pip 包优先 + 源码 git clone 兜底。

    VoxCPM 是 OpenBMB 开源的轻量 TTS(0.5B 参数,CPU 实时合成中文)。
    没有官方 docker 镜像;主要靠 pip(``voxcpm`` 包)或源码 clone。
    """
    out: List[InstallRecipe] = []
    # 1. pip 包(主推)— 4 镜像源
    out.extend(_pip_with_mirrors("voxcpm"))
    # 2. git clone 源码(兜底,适合需要 voice clone 自定义音色的高级用户)
    if host == "win":
        out.append(InstallRecipe(
            label="git clone (官方)",
            cmd=["git", "clone",
                 "https://github.com/OpenBMB/VoxCPM.git",
                 "%USERPROFILE%\\VoxCPM"],
            note="需 Git for Windows;后续 pip install -r requirements.txt",
            requires="git",
        ))
        out.append(InstallRecipe(
            label="git clone (国内代理 ghproxy)",
            cmd=["git", "clone",
                 "https://mirror.ghproxy.com/https://github.com/OpenBMB/VoxCPM.git",
                 "%USERPROFILE%\\VoxCPM"],
            note="国内网络优选",
            requires="git",
        ))
    else:
        out.append(InstallRecipe(
            label="git clone (官方)",
            cmd=["git", "clone",
                 "https://github.com/OpenBMB/VoxCPM.git",
                 os.path.expanduser("~/VoxCPM")],
            note="海外快;后续 pip install -r requirements.txt",
            requires="git",
        ))
        out.append(InstallRecipe(
            label="git clone (国内代理 ghproxy)",
            cmd=["git", "clone",
                 "https://mirror.ghproxy.com/https://github.com/OpenBMB/VoxCPM.git",
                 os.path.expanduser("~/VoxCPM")],
            note="国内网络优选",
            requires="git",
        ))
    return out


def _piper(host: str) -> List[InstallRecipe]:
    out: List[InstallRecipe] = []
    if host == "win":
        # Windows 原生没有 bash;走 PowerShell + Invoke-WebRequest + Expand-Archive
        # 国内用户 github.com 经常连不通,默认配两个代理变体在前
        for label, url in [
            ("官方 release (PowerShell)",
             "https://github.com/rhasspy/piper/releases/latest/download/piper_windows_amd64.zip"),
            ("国内代理 ghproxy (PowerShell)",
             "https://mirror.ghproxy.com/https://github.com/rhasspy/piper/releases/latest/download/piper_windows_amd64.zip"),
            ("国内代理 gh-proxy.com (PowerShell)",
             "https://gh-proxy.com/https://github.com/rhasspy/piper/releases/latest/download/piper_windows_amd64.zip"),
        ]:
            out.append(InstallRecipe(
                label=label,
                cmd=["powershell", "-NoProfile", "-Command",
                     f"$tmp = \"$env:TEMP\\piper.zip\"; "
                     f"Invoke-WebRequest -UseBasicParsing -Uri '{url}' -OutFile $tmp; "
                     f"Expand-Archive -Force -Path $tmp -DestinationPath \"$env:LOCALAPPDATA\\piper\""],
                note="Windows 原生;-UseBasicParsing 兼容禁用 IE 引擎的环境",
            ))
        out.append(InstallRecipe(
            label="winget (社区维护)",
            cmd=["winget", "search", "piper-tts"],
            note="winget 当前可能无官方包;先 search,有再 install",
            requires="winget",
        ))
    else:
        out.append(InstallRecipe(
            label="官方 release",
            cmd=["bash", "-lc",
                 "curl -fsSL -o /tmp/piper.tar.gz "
                 "https://github.com/rhasspy/piper/releases/latest/download/piper_amd64.tar.gz "
                 "&& tar xzf /tmp/piper.tar.gz -C ~/.local"],
        ))
        out.append(InstallRecipe(
            label="国内代理 release",
            cmd=["bash", "-lc",
                 "curl -fsSL -o /tmp/piper.tar.gz "
                 "https://mirror.ghproxy.com/https://github.com/rhasspy/piper/releases/latest/download/piper_amd64.tar.gz "
                 "&& tar xzf /tmp/piper.tar.gz -C ~/.local"],
        ))
    return out


# ---------------------------------------------------------------------------
# 通用 pip 多镜像源
# ---------------------------------------------------------------------------

def _pip_with_mirrors(pkg: str) -> List[InstallRecipe]:
    return [
        InstallRecipe(
            label="pip · PyPI 官方",
            cmd=[sys.executable, "-m", "pip", "install", *pkg.split()],
            note="海外网络;速度有时不稳",
        ),
        InstallRecipe(
            label="pip · 清华 TUNA",
            cmd=[sys.executable, "-m", "pip", "install",
                 "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", *pkg.split()],
            note="国内首选",
        ),
        InstallRecipe(
            label="pip · 阿里云",
            cmd=[sys.executable, "-m", "pip", "install",
                 "-i", "https://mirrors.aliyun.com/pypi/simple/", *pkg.split()],
        ),
        InstallRecipe(
            label="pip · 中科大 USTC",
            cmd=[sys.executable, "-m", "pip", "install",
                 "-i", "https://pypi.mirrors.ustc.edu.cn/simple/", *pkg.split()],
        ),
    ]


def custom_recipe(framework: str, custom_cmd: str) -> InstallRecipe:
    """用户填的自定义命令 — UI 提供输入框,按宿主系统选 shell。

    Windows 原生没 ``/bin/bash``,强行 bash 会 ``execvpe failed``。
    在 Windows 上走 ``cmd /c``;mac/linux 走 ``bash -lc``。
    """
    if _host_os() == "win":
        cmd = ["cmd", "/c", custom_cmd]
    else:
        cmd = ["bash", "-lc", custom_cmd]
    return InstallRecipe(
        label="自定义命令",
        cmd=cmd,
        note="用户提供;请确保命令安全",
    )


__all__ = [
    "MIRROR_SOURCES",
    "custom_recipe",
    "get_recipes",
    "get_recipes_simplified",
]
