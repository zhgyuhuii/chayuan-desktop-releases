"""数据治理（P1-9）子系统入口。

四模块：
- ``policy``  — 策略读写（按 scope 查有效策略）
- ``pii``     — PII 识别（中文/英文内置规则；Presidio 可选后端）
- ``masking`` — 角色级脱敏
- ``lineage`` — 血缘写入
- ``quota``   — Redis 令牌桶 + 每日累计

所有模块的"失败路径"都是 fail-open（不要阻塞主业务）；仅在策略显式拒绝时才拦截。
"""

from chayuan.server.governance import lineage, masking, pii, policy, quota  # noqa: F401
