"""察元安装失败诊断器 — 跨平台,纯 stdlib。

读取 pip / poetry / 安装脚本输出的日志,匹配已知失败模式,给出可执行的修复命令。
被 ``install_smart.ps1`` / ``install_smart.sh`` 在原安装脚本失败后调用。

用法:
    # 1) 解析日志文件,人类可读输出
    python scripts/install_diagnose.py install.log

    # 2) 解析日志,JSON 输出(给 CI / 自动化)
    python scripts/install_diagnose.py install.log --json

    # 3) 解析日志 + 交互式逐条执行修复命令
    python scripts/install_diagnose.py install.log --auto-fix

    # 4) 从 stdin 读
    cat install.log | python scripts/install_diagnose.py --stdin

设计原则:
    * 规则按优先级匹配,第一个命中即返回 — 错误链通常是顶部最具体
    * 不擅自重启用户进程,需要确认才执行
    * 修复命令尽量幂等(可重复跑不损坏)
    * 退出码:0=诊断完成 / 1=未识别 / 2=修复执行失败
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional


# ----------------------------------------------------------------------------
# 诊断结果数据结构
# ----------------------------------------------------------------------------

@dataclass
class FixCommand:
    label: str                      # UI 上显示的中文描述
    cmd: List[str]                  # subprocess 执行的命令
    needs_admin: bool = False       # Windows 是否需要管理员
    note: str = ""                  # 命令补充说明
    optional: bool = False          # True 时跳过不算失败


@dataclass
class Diagnosis:
    kind: str                       # 短分类名(供脚本判断)
    summary: str                    # 一句话总结
    cause: str = ""                 # 详细成因(多行)
    fixes: List[FixCommand] = field(default_factory=list)
    refs: List[str] = field(default_factory=list)  # 文档链接


# ----------------------------------------------------------------------------
# 平台辅助
# ----------------------------------------------------------------------------

def _is_windows() -> bool:
    return platform.system() == "Windows"


def _ps_cmd(ps_script: str) -> List[str]:
    """Windows 上跑 PowerShell 一行命令的标准 cmd 列表。"""
    return ["powershell", "-NoProfile", "-Command", ps_script]


def _ps_file(rel_path: str) -> List[str]:
    """Windows 上跑 .ps1 脚本(从 repo 根相对路径)。"""
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", rel_path]


def _platform_repair_torch() -> List[FixCommand]:
    """torch 重装 — Windows / *nix 走不同脚本。"""
    if _is_windows():
        return [
            FixCommand(
                label="关闭所有占用 torch 的 Python 进程",
                cmd=_ps_cmd(
                    "Get-Process python,pythonw,jupyter*,chayuan* -EA 0 | "
                    "Stop-Process -Force"
                ),
                note="若仍报 [WinError 5],请用 *管理员模式* 重新打开 PowerShell",
            ),
            FixCommand(
                label="运行 scripts/repair_torch.ps1(自动卸载 + 重装稳定 2.4.1 CPU 版)",
                cmd=_ps_file("scripts/repair_torch.ps1"),
                needs_admin=False,
            ),
        ]
    # macOS / Linux
    return [
        FixCommand(
            label="卸载 torch 系列",
            cmd=["pip", "uninstall", "-y", "torch", "torchvision", "torchaudio"],
        ),
        FixCommand(
            label="重装 torch 2.4.1 CPU 稳定组合",
            cmd=[
                "pip", "install",
                "torch==2.4.1", "torchvision==0.19.1", "torchaudio==2.4.1",
                "--index-url", "https://download.pytorch.org/whl/cpu",
            ],
        ),
    ]


def _platform_install_libs() -> List[FixCommand]:
    """11 个 sibling 库 editable 安装。"""
    if _is_windows():
        return [FixCommand(
            label="安装 11 个 chayuan-* sibling 库(scripts/install_ai_platform.ps1)",
            cmd=_ps_file("scripts/install_ai_platform.ps1"),
        )]
    return [FixCommand(
        label="安装 11 个 chayuan-* sibling 库(scripts/install_ai_platform.sh)",
        cmd=["bash", "scripts/install_ai_platform.sh"],
    )]


# ----------------------------------------------------------------------------
# 诊断规则:每条 = (优先级 desc, classify_fn -> Optional[Diagnosis])
# ----------------------------------------------------------------------------

def _rule_winerror5_pyd_lock(log: str) -> Optional[Diagnosis]:
    """Windows: [WinError 5] 拒绝访问 _C.pyd / *.dll(典型 torch / numpy 升级冲突)。

    长路径会把 ``WinError 5]`` 与 ``_C.pyd`` 拉得很远(60+ 字符),不能用
    ``.{0,40}`` 兜;改成"WinError 5 出现 + 同段日志命中 .pyd / .dll / torch"。
    """
    has_err = re.search(r"WinError 5\b|拒绝访问", log)
    if not has_err:
        return None
    has_artifact = re.search(
        r"\.(pyd|dll)\b|site-packages[\\/]torch|site-packages[\\/]numpy",
        log,
        re.IGNORECASE,
    )
    if not has_artifact:
        return None
    return Diagnosis(
        kind="winerror5_dll_lock",
        summary="Windows pip 升级 PyTorch / 数值库时被 DLL 文件锁住",
        cause=(
            "另一个 Python 进程(IDE / Jupyter / chayuan server / 调试器)\n"
            "已加载这个 .pyd / .dll,Windows 不允许 pip 在文件被使用时替换。\n"
            "通常发生在 install_ai_platform 最后一步触发 pip 解依赖时。"
        ),
        fixes=_platform_repair_torch() + [
            FixCommand(
                label="重跑安装(此时 torch 已新装,sibling 库依赖会自动满足)",
                cmd=_ps_file("scripts/install_ai_platform.ps1"),
            ),
        ],
        refs=[
            "docs/contributing/README_dev.md#windows-装包遇到-winerror-5",
            "scripts/repair_torch.ps1",
        ],
    )


def _rule_torch_broken_install(log: str) -> Optional[Diagnosis]:
    """torch 半装:torch.SymInt 缺失 / DLL load failed / _C 导入失败。"""
    patterns = [
        r"AttributeError.*module 'torch' has no attribute 'SymInt'",
        r"AttributeError.*module 'torch' has no attribute '_C'",
        r"ImportError.*DLL load failed.*_C",
        r"ImportError.*libtorch.*not found",
    ]
    if not any(re.search(p, log) for p in patterns):
        return None
    return Diagnosis(
        kind="torch_broken_install",
        summary="PyTorch 已装但 C 扩展加载失败 — 半装产物",
        cause=(
            "上次 pip 升级 torch 被 [WinError 5] 或权限错误中断,\n"
            "site-packages\\torch\\ 目录在但 _C.pyd 没装全。\n"
            "症状:torch.SymInt / torch._C 缺失 → langchain_text_splitters\n"
            "       → sentence_transformers → torch.distributed 链式失败。"
        ),
        fixes=_platform_repair_torch(),
        refs=["docs/contributing/README_dev.md#windows-装包遇到-symint"],
    )


def _rule_missing_chayuan_lib(log: str) -> Optional[Diagnosis]:
    """No module named 'chayuan_xxx' — sibling 库没装。"""
    m = re.search(r"No module named '(chayuan[_-][a-zA-Z_]+)'", log)
    if not m:
        return None
    lib = m.group(1)
    return Diagnosis(
        kind="missing_chayuan_lib",
        summary=f"sibling 库 {lib} 未安装",
        cause=(
            f"chayuan-server 依赖 libs/ 下 11 个 sibling 库(chayuan-modelmgr /\n"
            f"chayuan-runtime / chayuan-core / ...);单跑 ``poetry install`` 不会\n"
            f"自动装它们,需另外执行 install_ai_platform 脚本。"
        ),
        fixes=_platform_install_libs(),
        refs=["docs/contributing/README_dev.md"],
    )


def _rule_docker_layer_missing(log: str) -> Optional[Diagnosis]:
    """Docker pull 失败 — **镜像源问题**统称(三类合并):

    A. 某层在源上找不到(同步延迟):
       ``could not fetch content descriptor sha256:xxx from remote: not found``
    B. 拉到一半连接中断(EOF / 网络抖动):
       ``failed to do request: Get "..." : EOF``
       ``failed to do request: ... : connection reset by peer``
       ``failed to do request: ... : i/o timeout``
    C. 镜像签名 / blob 损坏:
       ``failed to copy: httpReadSeeker: failed open``(不带 not found 后缀)

    A/B/C 修复方法相同:换源 / 清缓存 / 重试。
    """
    patterns = [
        r"could not fetch content descriptor.*from remote: not found",
        r"failed to copy: httpReadSeeker",
        r"failed to do request:.*: EOF",
        r"failed to do request:.*connection reset",
        r"failed to do request:.*i/o timeout",
        r"failed to do request:.*read tcp.*timeout",
        r"manifest.*not found",   # docker manifest 完全缺
        r"unauthorized: authentication required",  # 私库未登录但说明源在
    ]
    if not any(re.search(p, log, re.IGNORECASE) for p in patterns):
        return None
    # 抽当前用的镜像 URL,告诉用户具体哪个源失败
    m = re.search(r"docker run\s+.*?\s+([\w./-]+/[\w./-]+(?::[\w.-]+)?)", log)
    image = m.group(1) if m else "(未识别)"
    # 提取常见替代源
    alt_sources = [
        ("Docker Hub 官方", "(去掉前缀)"),
        ("DaoCloud", "docker.m.daocloud.io"),
        ("1ms.run", "docker.1ms.run"),
        ("玄垣", "docker.xuanyuan.me"),
    ]
    cause = (
        f"docker pull 失败 — 通常是镜像源问题(同步落后 / 网络中断 / blob 缺):\n"
        f"  · 某层 .tar.gzip 找不到(源同步延迟)\n"
        f"  · 拉到一半连接断了(EOF / connection reset / 网络抖动)\n"
        f"  · manifest 损坏 / 鉴权失败\n\n"
        f"失败的镜像:{image}\n"
        f"常见解法:**换另一个源**重试(各源完整度差异较大)"
    )
    # 抽容器名(若用户 cmd 含 --name <X>),清理用
    cm = re.search(r"--name\s+(\S+)", log)
    container_name = cm.group(1) if cm else ""

    fixes: List[FixCommand] = []

    # 1. 先清掉残留(半成品镜像 + 半成品容器)
    if container_name:
        fixes.append(FixCommand(
            label=f"清掉半成品容器 {container_name}",
            cmd=["docker", "rm", "-f", container_name],
            optional=True,
            note="若容器创建一半失败,docker run 重试会报 name conflict",
        ))
    if image != "(未识别)":
        fixes.append(FixCommand(
            label=f"清掉半成品镜像 {image}",
            cmd=["docker", "rmi", "-f", image],
            optional=True,
            note="清掉部分下载的镜像,避免 layer 缓存污染",
        ))
    fixes.append(FixCommand(
        label="清掉所有 docker 悬挂层 + 网络缓存",
        cmd=["docker", "system", "prune", "-f"],
    ))

    # 2. 立即重试(若是网络抖动通常一次就好)
    if image != "(未识别)":
        fixes.append(FixCommand(
            label=f"重试拉取(临时网络抖动 EOF 通常重试就好)",
            cmd=["docker", "pull", image],
            optional=True,
        ))

    # 3. 换源 — 给文字提示而非真命令(因为换源在 chayuan 弹窗里点 chip)
    fixes.append(FixCommand(
        label="换 DaoCloud 源(国内首选,完整度高)",
        cmd=(_ps_cmd("Write-Host '在 chayuan 弹窗 chip 行选 \"Docker · DaoCloud 镜像\" 重新安装'")
             if _is_windows()
             else ["bash", "-c",
                   "echo '在 chayuan 弹窗 chip 行选 \"Docker · DaoCloud 镜像\" 重新安装'"]),
        note="提示:回 chayuan 安装弹窗选另一个 chip;activated 源会自动排前",
        optional=True,
    ))
    fixes.append(FixCommand(
        label="换 Docker Hub 官方(完整度最高,海外快)",
        cmd=(_ps_cmd("Write-Host '在 chayuan 弹窗 chip 行选 \"Docker · Docker Hub\" 重新安装'")
             if _is_windows()
             else ["bash", "-c",
                   "echo '在 chayuan 弹窗 chip 行选 \"Docker · Docker Hub\" 重新安装'"]),
        note="如海外网络好,Docker Hub 是最稳定的源",
        optional=True,
    ))
    return Diagnosis(
        kind="docker_layer_missing",
        summary="Docker 镜像层在当前源不完整(镜像源同步落后)",
        cause=cause,
        fixes=fixes,
        refs=["docs/contributing/README_dev.md"],
    )


def _rule_pip_dependency_conflict(log: str) -> Optional[Diagnosis]:
    """pip 解析失败 / 装完之后 dependency-resolver 警告版本互不兼容。

    与 ``Could not find a version`` 不同,这种是"已装上但版本冲突",
    通常来自 sibling 库(如 chayuan-gateway)与主库(chayuan-server)
    对同一个第三方依赖(fastapi / starlette 等)写了不相容的版本范围。
    """
    if "pip's dependency resolver does not currently take into account" not in log \
       and "dependency conflicts" not in log:
        return None
    # 提取冲突详情。pip 的固定输出格式:
    #   "{owner} {ver} requires {pkg}{spec}, but you have {pkg2} {actual_ver} which is incompatible."
    # owner 包名带连字符,spec 含 <>=!~,
    conflict_re = re.compile(
        r"(\S+)\s+\S+\s+requires\s+([A-Za-z0-9_\-]+)([<>=!~,\d.\s]*?),\s+"
        r"but you have\s+\S+\s+(\S+)\s+which\s+is\s+incompatible",
    )
    conflicts = conflict_re.findall(log)
    detail_lines = []
    for c in conflicts[:10]:  # 最多列 10 条避免刷屏
        owner, dep, want, got = c
        detail_lines.append(
            f"  ✗ {owner} 要求 {dep}{want.strip()},实际装的是 {got}"
        )
    detail_str = "\n".join(detail_lines) if detail_lines else "  (具体冲突见上方 pip 输出)"

    cause = (
        "pip 安装看似成功,但解析器警告若干包对同一依赖写了不相容的版本范围。\n"
        "通常是 chayuan 项目内部 sibling 库(如 chayuan-gateway)的依赖声明\n"
        "和主库(chayuan-server / nicegui)互相打架。\n\n"
        "冲突详情:\n" + detail_str
    )
    return Diagnosis(
        kind="pip_dependency_conflict",
        summary="pip 装完报版本冲突 — sibling 库与主库依赖声明不一致",
        cause=cause,
        fixes=[
            FixCommand(
                label="拉取最新代码(很可能上游已修依赖声明)",
                cmd=["git", "pull", "--ff-only"],
                optional=True,
            ),
            FixCommand(
                label="强制把冲突的包降回主库锁定的版本(以 fastapi / sse-starlette 为例)",
                cmd=["pip", "install", "--upgrade-strategy", "only-if-needed",
                     "fastapi>=0.109.2,<0.110.0",
                     "sse-starlette>=1.8.2,<1.9.0",
                     "starlette>=0.36.0,<0.37.0"],
            ),
            FixCommand(
                label="(若仍冲突)用 chayuan-server pyproject 重新解全量依赖",
                cmd=["pip", "install", "-e", "libs/chayuan-server",
                     "--upgrade-strategy", "only-if-needed"],
                optional=True,
            ),
        ],
        refs=["docs/contributing/README_dev.md"],
    )


def _rule_pip_invalid_distribution(log: str) -> Optional[Diagnosis]:
    r"""pip 发现 site-packages 里有 ``~xxx`` 残骸目录(上次中断的孤儿)。

    这只是 WARNING 不致命,但每次 pip 运行都唠叨,日志噪音大,且
    可能让用户误以为安装失败。一键清理: 删 site-packages\~* 目录。
    """
    if not re.search(r"Ignoring invalid distribution\s+~\S*", log):
        return None
    # 提取所有残骸名字
    stale_names = sorted(set(re.findall(r"Ignoring invalid distribution\s+(~\S*)", log)))
    cause = (
        "pip 在上次安装被中断时把原包重命名为 ~xxx 准备替换,但中断后\n"
        "没清理。每次 pip 运行都会扫到这些目录,发出 WARNING。\n"
        "本身不影响安装功能,但建议清理以恢复干净的环境。\n\n"
        + f"检测到的残骸: {', '.join(stale_names) if stale_names else '~ / ~xxx'}"
    )
    if _is_windows():
        fixes = [
            FixCommand(
                label="清理 site-packages\\~* 残骸目录",
                cmd=_ps_cmd(
                    "$sp = & python -c \"import sysconfig;print(sysconfig.get_paths()['purelib'])\";"
                    "Get-ChildItem $sp -Filter '~*' -Directory -Force -EA 0 | "
                    "ForEach-Object { Write-Host \"删除 $($_.Name)\"; Remove-Item -Recurse -Force $_.FullName -EA 0 }"
                ),
                note="幂等,可重复跑",
            ),
        ]
    else:
        fixes = [
            FixCommand(
                label="清理 site-packages/~* 残骸目录",
                cmd=["bash", "-c",
                     "sp=$(python -c 'import sysconfig;print(sysconfig.get_paths()[\"purelib\"])'); "
                     "find \"$sp\" -maxdepth 1 -name '~*' -type d -print -exec rm -rf {} +"],
                note="幂等,可重复跑",
            ),
        ]
    return Diagnosis(
        kind="pip_invalid_distribution",
        summary="site-packages 里有 ~* 残骸目录(噪音警告,不致命)",
        cause=cause,
        fixes=fixes,
    )


def _rule_pip_network(log: str) -> Optional[Diagnosis]:
    """pip 网络超时(国内常见,pypi.org 走不通)。"""
    patterns = [
        r"ReadTimeoutError",
        r"Connection.*aborted",
        r"Failed to establish a new connection",
        r"Read timed out",
        r"HTTPSConnectionPool.*Max retries",
    ]
    if not any(re.search(p, log) for p in patterns):
        return None
    return Diagnosis(
        kind="pip_network_timeout",
        summary="pip 拉包超时 — 通常是 pypi.org 国内访问慢",
        cause=(
            "pip 默认从 https://pypi.org/simple 下载,国内访问不稳。\n"
            "建议切换到清华 / 阿里 / 中科大镜像。"
        ),
        fixes=[
            FixCommand(
                label="切换 pip 全局源到清华镜像",
                cmd=["pip", "config", "set", "global.index-url",
                     "https://pypi.tuna.tsinghua.edu.cn/simple"],
            ),
            FixCommand(
                label="增加 pip 超时阈值",
                cmd=["pip", "config", "set", "global.timeout", "120"],
                optional=True,
            ),
            FixCommand(
                label="重试原安装命令",
                cmd=_ps_file("scripts/install_ai_platform.ps1") if _is_windows()
                    else ["bash", "scripts/install_ai_platform.sh"],
            ),
        ],
    )


def _rule_missing_msvc(log: str) -> Optional[Diagnosis]:
    """Windows: 缺 MSVC Build Tools(C 扩展编译失败)。"""
    if "Microsoft Visual C++ 14.0 or greater is required" not in log \
       and "error: Microsoft Visual C++" not in log:
        return None
    return Diagnosis(
        kind="missing_msvc_build_tools",
        summary="Windows 缺 Microsoft Visual C++ Build Tools(编译 C 扩展用)",
        cause=(
            "某些 PyPI 包(如 hnswlib / chroma-hnswlib / faiss)需要本机编译 C++,\n"
            "Windows 上必须装 'Microsoft C++ Build Tools'。"
        ),
        fixes=[
            FixCommand(
                label="打开 Build Tools 官方下载页",
                cmd=_ps_cmd(
                    "Start-Process 'https://visualstudio.microsoft.com/visual-cpp-build-tools/'"
                ),
                note="安装时勾选 'Desktop development with C++' 工作负载",
            ),
            FixCommand(
                label="或者改装预编译 wheel(避开本机编译)",
                cmd=["pip", "install", "--prefer-binary", "--upgrade",
                     "hnswlib", "chroma-hnswlib"],
                optional=True,
                note="若客户端不需要 hnswlib 可跳过这一步",
            ),
        ],
    )


def _rule_missing_python_dev(log: str) -> Optional[Diagnosis]:
    """Linux: Python.h 缺(没装 python3-dev / python3-devel)。"""
    if not re.search(r"Python\.h.*No such file", log):
        return None
    return Diagnosis(
        kind="missing_python_dev",
        summary="Linux 缺 python3-dev / python3-devel(C 扩展编译失败)",
        cause="编译 PyPI 的 C 扩展需要 Python 头文件,默认未装。",
        fixes=[
            FixCommand(
                label="Debian/Ubuntu 安装 python3-dev",
                cmd=["bash", "-c",
                     "sudo apt-get update && sudo apt-get install -y python3-dev build-essential"],
            ),
            FixCommand(
                label="RHEL/CentOS/Rocky 安装 python3-devel",
                cmd=["bash", "-c", "sudo yum install -y python3-devel gcc gcc-c++"],
                optional=True,
            ),
        ],
    )


def _rule_permission_denied(log: str) -> Optional[Diagnosis]:
    """[Errno 13] Permission denied — 用 sudo pip 或没激活 venv。"""
    if "[Errno 13] Permission denied" not in log \
       and "could not install packages due to an OSError" not in log.lower():
        return None
    if "WinError 5" in log:
        return None  # 已被 winerror5 规则吃掉
    return Diagnosis(
        kind="permission_denied",
        summary="pip 写 site-packages 时权限不足",
        cause=(
            "可能成因:\n"
            "  1. 没激活 venv / conda env,在系统 Python 装包\n"
            "  2. 错误地用 sudo pip(应该用 venv 后普通用户)\n"
            "  3. site-packages 目录被其他用户拥有"
        ),
        fixes=[
            FixCommand(
                label="激活 chayuan-server 的 venv",
                cmd=["bash", "-c", "source .venv/bin/activate && pip --version"],
                note="Windows: .\\.venv\\Scripts\\Activate.ps1",
            ),
            FixCommand(
                label="或用户级安装(不推荐,但可绕过权限)",
                cmd=["pip", "install", "--user", "-e", "libs/chayuan-server"],
                optional=True,
            ),
        ],
    )


def _rule_ssl_cert(log: str) -> Optional[Diagnosis]:
    """SSL 证书问题(企业代理/老客户端)。"""
    if not re.search(r"SSL.*CERTIFICATE_VERIFY_FAILED|SSL.*WRONG_VERSION_NUMBER",
                     log, re.IGNORECASE):
        return None
    return Diagnosis(
        kind="ssl_cert_error",
        summary="SSL 证书验证失败(常见于公司代理 / 老 OpenSSL)",
        cause=(
            "公司代理可能使用自签证书,或本机 CA store 太旧。\n"
            "可以临时把 pypi 加到 trusted-host,长期建议导入公司根证书。"
        ),
        fixes=[
            FixCommand(
                label="把 pypi 加到 trusted-host(临时绕过)",
                cmd=["pip", "config", "set", "global.trusted-host",
                     "pypi.org files.pythonhosted.org pypi.tuna.tsinghua.edu.cn"],
            ),
            FixCommand(
                label="升级 pip / certifi 到最新",
                cmd=["pip", "install", "--upgrade", "pip", "certifi"],
            ),
        ],
    )


def _rule_disk_full(log: str) -> Optional[Diagnosis]:
    """磁盘满。"""
    if not re.search(r"\[Errno 28\] No space left|disk.{0,5}full",
                     log, re.IGNORECASE):
        return None
    return Diagnosis(
        kind="disk_full",
        summary="磁盘空间不足",
        cause="安装 PyTorch + 转换器模型常占用 5-15GB,系统盘空间不够。",
        fixes=[
            FixCommand(
                label="清 pip 缓存",
                cmd=["pip", "cache", "purge"],
            ),
            FixCommand(
                label="(macOS/Linux)查 site-packages 占用",
                cmd=["bash", "-c",
                     "du -sh \"$(python -c 'import sysconfig; print(sysconfig.get_paths()[\"purelib\"])')\""],
                optional=True,
            ),
        ],
    )


def _rule_version_conflict(log: str) -> Optional[Diagnosis]:
    """pip 找不到匹配版本(约束冲突 / 版本被 yank / 镜像同步延迟)。"""
    if "Could not find a version that satisfies the requirement" not in log:
        return None
    m = re.search(r"satisfies the requirement (\S+)", log)
    pkg = m.group(1) if m else "(未知包)"
    return Diagnosis(
        kind="version_conflict",
        summary=f"找不到满足约束的 {pkg}",
        cause=(
            "可能成因:\n"
            "  1. 包版本被 PyPI yank,需放宽约束\n"
            "  2. 当前镜像未同步最新版本(切官方源试)\n"
            "  3. 约束彼此冲突(查 poetry.lock / pip 输出)"
        ),
        fixes=[
            FixCommand(
                label="切回官方 pypi 重试",
                cmd=["pip", "install", "--index-url", "https://pypi.org/simple",
                     pkg if pkg != "(未知包)" else "--help"],
                optional=True,
            ),
            FixCommand(
                label="清 pip 缓存重试",
                cmd=["pip", "cache", "purge"],
            ),
        ],
    )


# 规则注册表 — 按精确度排序,精确的规则在前
_RULES: List[Callable[[str], Optional[Diagnosis]]] = [
    _rule_winerror5_pyd_lock,    # 最具体:Windows + DLL 锁
    _rule_torch_broken_install,  # 具体:torch 半装
    _rule_missing_msvc,           # 具体:MSVC 缺
    _rule_missing_python_dev,     # 具体:Linux dev 头文件
    _rule_missing_chayuan_lib,    # 具体:chayuan_* 缺
    _rule_ssl_cert,               # 中等:SSL
    _rule_disk_full,              # 中等:磁盘
    _rule_permission_denied,      # 中等:权限
    _rule_docker_layer_missing,   # 具体:docker 镜像层不完整(换源即解)
    _rule_pip_network,            # 一般:网络超时
    _rule_version_conflict,       # 一般:版本约束(找不到版本)
    _rule_pip_dependency_conflict,  # 一般:版本冲突(装上了但不兼容)
    _rule_pip_invalid_distribution,  # 兜底:noise 警告(无功能影响)
]


def diagnose(log: str) -> Optional[Diagnosis]:
    """对外接口:输入 stderr/stdout 文本,返回最佳匹配 Diagnosis 或 None。"""
    for rule in _RULES:
        try:
            d = rule(log)
        except Exception:  # noqa: BLE001
            continue
        if d is not None:
            return d
    return None


# ----------------------------------------------------------------------------
# 输出与交互
# ----------------------------------------------------------------------------

def _print_human(d: Diagnosis) -> None:
    sep = "=" * 72
    print()
    print(sep)
    print(f"[chayuan] 安装失败诊断 · {d.kind}")
    print(sep)
    print()
    print(f"问题: {d.summary}")
    print()
    if d.cause:
        print("成因:")
        for line in d.cause.split("\n"):
            print(f"  {line}")
        print()
    if d.fixes:
        print("建议修复(按顺序执行):")
        for i, f in enumerate(d.fixes, 1):
            cmd_str = " ".join(_quote(x) for x in f.cmd)
            tag = " (可选)" if f.optional else ""
            admin = " ⚠ 需管理员" if f.needs_admin else ""
            print(f"  {i}. {f.label}{tag}{admin}")
            print(f"     $ {cmd_str}")
            if f.note:
                print(f"     说明: {f.note}")
            print()
    if d.refs:
        print("更多详情:")
        for r in d.refs:
            print(f"  - {r}")
    print(sep)
    print()


def _quote(s: str) -> str:
    """命令显示时的简单 shell 引用,只加必要的引号。"""
    if not s:
        return "''"
    if any(c in s for c in (' ', '|', '&', ';', '<', '>', '(', ')', '$', '`',
                            '"', "'", '*', '?', '[', ']', '{', '}')):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _confirm(msg: str) -> bool:
    """y/N 确认。stdin 不可用时返回 False。"""
    try:
        sys.stdout.write(msg)
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes", "是")


def _run_fixes_interactive(d: Diagnosis) -> int:
    print(f"[一键修复] 共 {len(d.fixes)} 个修复步骤,逐条确认。")
    print(f"[一键修复] 直接 Enter = 跳过该步骤;输入 y = 执行;Ctrl+C = 中止。")
    print()
    failures: List[int] = []
    for i, f in enumerate(d.fixes, 1):
        cmd_str = " ".join(_quote(x) for x in f.cmd)
        tag = " (可选,直接 Enter 跳过)" if f.optional else ""
        prompt = f"  [{i}/{len(d.fixes)}] {f.label}{tag}\n     $ {cmd_str}\n     执行? [y/N]: "
        if not _confirm(prompt):
            print(f"  → 跳过")
            print()
            continue
        print(f"  → 执行...")
        try:
            rc = subprocess.run(f.cmd).returncode
        except FileNotFoundError as e:
            print(f"  ✗ 找不到命令: {e}")
            print(f"  说明: {f.note}" if f.note else "")
            failures.append(i)
            print()
            continue
        if rc != 0:
            print(f"  ✗ 步骤 {i} 退出码 {rc}")
            if not f.optional:
                failures.append(i)
        else:
            print(f"  ✓ 步骤 {i} 完成")
        print()
    if failures:
        print(f"[一键修复] {len(failures)} 个必选步骤失败: {failures}")
        return 2
    print(f"[一键修复] 全部步骤完成。请重新执行原安装命令。")
    return 0


def _run_fixes_unattended(d: Diagnosis, *, skip_optional: bool = True) -> int:
    """非交互模式:顺序执行所有非可选修复命令,不询问 y/N。

    被 UI(NiceGUI 安装弹窗的"一键修复"按钮)和 CI 调用。
    实时打印命令边界,让上层日志流能看到进度。
    """
    print(f"[一键修复 · 非交互] 共 {len(d.fixes)} 个修复步骤")
    print()
    failures: List[int] = []
    for i, f in enumerate(d.fixes, 1):
        cmd_str = " ".join(_quote(x) for x in f.cmd)
        tag = " (可选)" if f.optional else ""
        if f.optional and skip_optional:
            print(f"  [{i}/{len(d.fixes)}] 跳过 (optional): {f.label}")
            print()
            continue
        print(f"  [{i}/{len(d.fixes)}] {f.label}{tag}")
        print(f"     $ {cmd_str}")
        sys.stdout.flush()
        try:
            rc = subprocess.run(f.cmd).returncode
        except FileNotFoundError as e:
            print(f"  ✗ 找不到命令: {e}")
            if f.note:
                print(f"  说明: {f.note}")
            if not f.optional:
                failures.append(i)
            print()
            continue
        if rc != 0:
            print(f"  ✗ 步骤 {i} 退出码 {rc}")
            if not f.optional:
                failures.append(i)
        else:
            print(f"  ✓ 步骤 {i} 完成")
        print()
        sys.stdout.flush()
    if failures:
        print(f"[一键修复] {len(failures)} 个必选步骤失败: {failures}")
        return 2
    print(f"[一键修复] 全部步骤完成。请重新执行原安装命令。")
    return 0


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="察元安装失败诊断 — 解析 pip / poetry 错误日志,给出修复建议",
    )
    p.add_argument("log_file", nargs="?",
                   help="安装日志文件路径;省略则从 stdin 读")
    p.add_argument("--stdin", action="store_true", help="强制从 stdin 读")
    p.add_argument("--auto-fix", action="store_true",
                   help="诊断后交互式逐条执行修复命令")
    p.add_argument("--auto-fix-yes", action="store_true",
                   help="非交互式 — 自动执行所有非可选修复命令(给 UI / CI 用)")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    args = p.parse_args()

    if args.stdin or not args.log_file:
        log = sys.stdin.read()
    else:
        log_path = Path(args.log_file)
        if not log_path.exists():
            sys.stderr.write(f"[install_diagnose] 日志文件不存在: {log_path}\n")
            return 3
        log = log_path.read_text(encoding="utf-8", errors="replace")

    d = diagnose(log)

    if d is None:
        if args.json:
            print(json.dumps({
                "kind": "unknown",
                "summary": "未匹配到已知失败模式",
                "fixes": [],
            }, ensure_ascii=False))
        else:
            print()
            print("=" * 72)
            print("[chayuan] 未匹配到已知安装失败模式。")
            print("=" * 72)
            print()
            print("建议:")
            print("  - 仔细阅读上方 pip / poetry 的错误段落")
            print("  - 把完整日志贴到 chayuan-server GitHub Issues 或开发组")
            print("  - 日志关键字搜:'ERROR' / 'fail' / 'Could not' / 'Errno'")
            print()
        return 1

    if args.json:
        print(json.dumps({
            "kind": d.kind,
            "summary": d.summary,
            "cause": d.cause,
            "fixes": [
                {"label": f.label, "cmd": f.cmd, "needs_admin": f.needs_admin,
                 "note": f.note, "optional": f.optional}
                for f in d.fixes
            ],
            "refs": d.refs,
        }, ensure_ascii=False, indent=2))
        return 0

    _print_human(d)

    if args.auto_fix_yes:
        return _run_fixes_unattended(d)
    if args.auto_fix:
        return _run_fixes_interactive(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
