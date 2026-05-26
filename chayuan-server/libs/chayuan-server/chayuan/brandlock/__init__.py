"""察元品牌锁(brandlock)— 商标 / 名称 / 文案防篡改保护层。

设计哲学
==========
绝对的"客户端防篡改"不存在 — 客户端代码到了用户手里,有 root 权限就能改。
我们的目标不是"绝对防篡改",而是:

1. **改的成本 >> 自研的成本** — 让用户不如重写一套
2. **改的痕迹无法清除** — canary 永远在多个位置触发
3. **法律惩罚 >> 技术绕过的收益** — 检测到 → 上报 → 律师函

所以本模块只做检测 + 取证,**不阻断用户使用**(防误伤合法部署的轻微改动,
而对真正的商标侵权可以从 telemetry 数据 → 法务流程拿证据)。

公开 API
==========
* :func:`is_tampered() -> bool` — 是否检测到改动
* :func:`get_tamper_evidence() -> Dict` — 改动详情(用于 API header / 上报)
* :func:`verify_now()` — 主动重新校验(默认启动时已校验)
* :func:`assert_brand_intact()` — 校验失败抛 BrandTamperedError(给关键路径用)

集成点(本模块外暴露的 5 个调用点)
======================================
1. ``cli.py``:启动时 ``verify_now()`` + log 一行结果
2. ``startup.py``:启动 banner 含 brand intact 状态
3. ``api_server`` / ``config_panel/app.py``:每个 HTTP 响应加 X-Chayuan-Trust header
4. ``admin_routes.py``:``/admin/brand-status`` API 暴露给客户审计自查
5. ``settings.py``:加载 settings 时 canary 调用一次

防御纵深
==========
* :mod:`._manifest` — 打包时生成,hash 所有受保护资源,Ed25519 签名
* :mod:`._verifier` — 运行时校验,公钥嵌入(分散),签名验证 + 文件 hash 验证
* :mod:`._canary` — 5 个散点位置调 ``_canary_check`` 间接触发校验
* :mod:`._evidence` — 收集证据,生成可追溯的 fingerprint
"""
from __future__ import annotations

from chayuan.brandlock._verifier import (
    BrandTamperedError,
    assert_brand_intact,
    get_tamper_evidence,
    is_tampered,
    verify_now,
)

__all__ = [
    "BrandTamperedError",
    "is_tampered",
    "get_tamper_evidence",
    "verify_now",
    "assert_brand_intact",
]
