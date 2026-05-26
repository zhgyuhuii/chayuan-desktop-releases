"""部署形态(profile)集中收口。

单机版重构后(``CLAUDE.md §3.3``)引入,目的是把"挑选实现"的分支收敛到 bootstrap
阶段,而不是散落到业务代码各处:

* ``single-machine``:Tauri 桌面应用打包形态(本地、单进程、anonymous user、
  sqlite-vec、cachetools 缓存、asyncio.Queue 队列)
* 默认(空):多用户 SaaS 形态,保留原有 Redis / Celery / PG / ES 等依赖

Profile 由 ``CHAYUAN_PROFILE`` 环境变量决定;``chayuan start --single-machine``
CLI 也会自动 setdefault 此变量。``apply_profile()`` 在 ``startup.main`` 早期
调用,把 ``Settings.*`` 等全局设置改写到对应形态。

设计要点:

1. **bootstrap-time 切换**:profile 只在启动早期 mutate ``Settings``,业务代码
   照常 ``getattr(Settings.basic_settings, 'AUTH_REQUIRED')`` —— 不需要到处写
   ``if is_single_machine(): ...`` 分支
2. **幂等**:多次调 ``apply_profile()`` 行为一致(dev / 测试场景便于重置)
3. **不破坏老代码**:未设 ``CHAYUAN_PROFILE`` 时 ``apply_profile()`` no-op,
   原 SaaS 路径完整保留
"""
from __future__ import annotations

from chayuan.server.profiles.bootstrap import (
    active_profile,
    apply_profile,
    is_single_machine,
)
from chayuan.server.profiles.single_machine import (
    LOCAL_USER,
    apply_single_machine,
    local_user,
)

__all__ = [
    "active_profile",
    "apply_profile",
    "apply_single_machine",
    "is_single_machine",
    "local_user",
    "LOCAL_USER",
]
