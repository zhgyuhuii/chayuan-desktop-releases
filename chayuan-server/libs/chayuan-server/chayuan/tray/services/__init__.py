"""企业版内嵌服务（Postgres / Redis / Milvus-Lite）生命周期管理。

**本模块只在企业版 + "embedded 模式"下被 import**。个人版 / 企业版但用户
自备外部服务时，这里的代码都不会执行。触发条件在 :func:`should_enable`。

目录结构（由 manager 约定，各子模块共用）：

    ~/.chayuan/
    ├── services/
    │   ├── bin/              # 从 bundle copy / symlink 过来的 PG / Redis 二进制
    │   ├── lib/              # 相应动态库
    │   ├── postgres/
    │   │   ├── data/         # pgdata（initdb 输出）
    │   │   ├── socket/       # unix socket（避免 listen tcp 端口冲突）
    │   │   └── postgres.log
    │   ├── redis/
    │   │   ├── data/         # RDB
    │   │   └── redis.log
    │   └── ports.json        # 实际使用的端口
    └── data/...              # CHAYUAN_ROOT（业务数据）

设计原则：

- **完全本地回环**：PG 用 unix socket（性能 + 不怕端口冲突），Redis 用 127.0.0.1。
- **幂等**：已 initdb 则不再 initdb；进程已 running 则不再 start。
- **可关停**：tray 退出时统一 stop，避免残留进程卡端口。
- **零外部依赖**：这里不使用 psycopg / redis-py 做验证，只用 pg_isready /
  redis-cli（shell out），因为这些 Python 包在 tray 主进程里不一定 import 过。
"""
from __future__ import annotations

import os
from pathlib import Path


def should_enable() -> bool:
    """判断是否应该起 embedded 服务。

    条件（全部满足才起）：

    1. ``CHAYUAN_EDITION=enterprise``（launcher 注入）；
    2. ``CHAYUAN_EMBEDDED_SERVICES != 0``（env 开关，默认走 embedded）；
    3. bundle 内有服务二进制（pg_ctl 存在）——个人版 .app 里不会有这个目录。

    任一条件不满足则返回 False，走"用户自备外部服务"路径。
    """
    if os.environ.get("CHAYUAN_EDITION", "").lower() != "enterprise":
        return False
    if os.environ.get("CHAYUAN_EMBEDDED_SERVICES", "1") == "0":
        return False
    return _bundled_bin_dir() is not None


def _bundled_bin_dir() -> Path | None:
    """返回 bundle 内的服务 bin 目录（若存在）。

    查找顺序：

    1. ``$CHAYUAN_SERVICES_BIN`` —— 显式覆盖（测试用）；
    2. ``RESOURCES_DIR/services/bin/`` —— macOS .app 安装包的约定位置；
    3. ``APP_SUPPORT/services/bin/`` —— first_run 解包到用户目录后的位置。
    """
    cand = os.environ.get("CHAYUAN_SERVICES_BIN")
    if cand and Path(cand).is_dir():
        return Path(cand)

    resources = os.environ.get("RESOURCES_DIR")
    if resources:
        p = Path(resources) / "services" / "bin"
        if p.is_dir():
            return p

    home = Path.home()
    p = home / ".chayuan" / "services" / "bin"
    if p.is_dir():
        return p

    return None
