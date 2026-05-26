"""察元运行时编排子系统（runtime）。

本子包把"端口分配 / 凭据生成 / vendor 离线包扫描 / 进程编排 / 运行时
endpoint 信息暴露"等能力集中在一处。它**不**取代 ``config_panel``、
``startup``、``init_prod_profile`` 等现有模块，而是给它们提供更可靠的底
层 primitive：

* :class:`PortAllocator` —— 统一的"偏好端口 + 自动 bump"实现，所有
  受管子进程（vendor 二进制 / docker compose 一次性服务 / 内置面板 /
  API server）都可以走它，避免端口冲突时悄悄起在 0 号端口。
* :class:`Credentials` / :func:`ensure_credentials` —— 第一次启动时自动生成
  user/password，落到 ``<CHAYUAN_ROOT>/runtime.json``（chmod 600），重启
  后稳定复用；同时支持把账号注入子进程环境变量、作为 ``expose`` 元数据回显。
* :class:`VendorLayout` —— 扫描 ``<CHAYUAN_ROOT>/vendor`` 与仓库根的 ``vendor/``
  目录，识别开发者放进来的 Postgres / Redis / MinIO / Milvus / Ollama /
  llama-cpp 等离线包；判定可用性并给出"请把可执行文件放在 X/Y/Z"的提示。
* :class:`RuntimeInfo` —— ``<CHAYUAN_ROOT>/runtime.json`` 的读写门面，记录
  最终 endpoint（host/port/scheme/url）+ credentials；CLI 与 API 都从这里取。
* :class:`SupervisorSpec` / :class:`ProcessSupervisor` —— 把声明式的 vendor
  进程拓扑跑起来；为可选能力，未配置时不影响 chayuan 主链路。

公开 API 即本文件 ``__all__``，子模块全部允许 ``from chayuan.server.runtime
import …`` 直接用。
"""
from __future__ import annotations

from chayuan.server.runtime.bootstrap import (
    BootstrapResult,
    allocate_core_ports,
    render_endpoints_table,
)
from chayuan.server.runtime.credentials import (
    Credentials,
    ensure_credentials,
    mask_password_in_url,
)
from chayuan.server.runtime.port_allocator import (
    PortAllocator,
    PortInUseError,
    is_port_free,
)
from chayuan.server.runtime.runtime_info import (
    RuntimeInfo,
    ServiceEndpoint,
    get_runtime_info,
    runtime_info_path,
)
from chayuan.server.runtime.vendor_loader import (
    VENDOR_DIRNAME,
    VendorEntry,
    VendorLayout,
    discover_vendor,
    known_runtimes,
    known_services,
)

__all__ = [
    "BootstrapResult",
    "allocate_core_ports",
    "render_endpoints_table",
    "Credentials",
    "ensure_credentials",
    "mask_password_in_url",
    "PortAllocator",
    "PortInUseError",
    "is_port_free",
    "RuntimeInfo",
    "ServiceEndpoint",
    "get_runtime_info",
    "runtime_info_path",
    "VENDOR_DIRNAME",
    "VendorEntry",
    "VendorLayout",
    "discover_vendor",
    "known_runtimes",
    "known_services",
]
