"""三态鉴权上下文（游客 / 登录用户 / App 模式）的统一抽象。

调用约定：路由层在拿到请求后调用 :func:`AuthContext.from_request(request)`，
得到一个稳定结构的对象。下游所有代码（dispatcher / handlers / lineage / quota）
只读这个对象，不再各自从 ``request.state`` 取 dict 字段。

为什么要抽？
- 旧代码到处 ``user.get('id') if isinstance(user, dict) else None`` —— 每处都得
  重写一遍 None / role / admin 的边界处理；
- App 模式（开放平台）拿到的是 ``AppSpec`` 而非 ``user`` dict，从未与游客/登录
  用户路径互通，导致血缘 / 配额 / 审计对外 App 是黑洞；
- 多租户化以后 ``tenant_id`` 也必须成为标识的一部分；统一抽象后只改一处。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AuthMode(str, Enum):
    """鉴权来源。"""

    GUEST = "guest"     # 未带 token，AUTH_REQUIRED=false 时放行的游客
    USER = "user"       # JWT 解出的真实用户
    APP = "app"         # 开放平台 HMAC 签名通过的 App 调用


@dataclass(frozen=True)
class AuthContext:
    """与运行时强相关的最小身份描述。frozen 是为了能直接当 cache key 的一部分。

    字段说明：
    - ``mode``：来源；下游限流 / 配额 / 血缘的"主键"取决于它
    - ``principal_id``：最稳定的标识符；user.id / app_id；guest 时为 None
    - ``display_name``：日志展示用；可空
    - ``role``：业务角色（admin / analyst / user / guest）；用于 PII masking 决策
    - ``app_id`` / ``app_scopes``：App 模式专属
    - ``tenant_id``：预留，多租户启用后由 TenantContextMiddleware 注入
    - ``request_id``：贯穿日志 / 血缘的 trace key
    """

    mode: AuthMode
    principal_id: Optional[int | str] = None
    display_name: str = ""
    role: str = "guest"
    app_id: Optional[str] = None
    app_scopes: List[str] = field(default_factory=list)
    tenant_id: Optional[str] = None
    request_id: str = ""

    # ---- factory methods ------------------------------------------------

    @classmethod
    def guest(cls, request_id: str = "") -> "AuthContext":
        return cls(mode=AuthMode.GUEST, request_id=request_id)

    @classmethod
    def from_user_dict(
        cls, user: Dict[str, Any], request_id: str = "",
    ) -> "AuthContext":
        return cls(
            mode=AuthMode.USER,
            principal_id=user.get("id"),
            display_name=str(user.get("username") or ""),
            role=str(user.get("role") or "user"),
            request_id=request_id,
        )

    @classmethod
    def from_app_spec(cls, app: Any, request_id: str = "") -> "AuthContext":
        return cls(
            mode=AuthMode.APP,
            principal_id=getattr(app, "app_id", None),
            display_name=getattr(app, "name", "") or "",
            role="app",
            app_id=getattr(app, "app_id", None),
            app_scopes=list(getattr(app, "scopes", []) or []),
            request_id=request_id,
        )

    @classmethod
    def from_request(cls, request: Any) -> "AuthContext":
        """根据 ``request.state`` 自动判别三态。

        优先级：``state.app`` > ``state.user`` > guest。同请求里两者并存的可能性
        极小（中间件分前缀生效），但出现时 App 优先（开放平台调用方）。
        """
        rid = ""
        try:
            rid = str(getattr(request.state, "request_id", "") or "")
        except Exception:  # noqa: BLE001
            rid = ""

        try:
            app = getattr(request.state, "app", None)
        except Exception:  # noqa: BLE001
            app = None
        if app is not None:
            return cls.from_app_spec(app, request_id=rid)

        try:
            u = getattr(request.state, "user", None)
        except Exception:  # noqa: BLE001
            u = None
        if isinstance(u, dict) and u.get("id") is not None:
            return cls.from_user_dict(u, request_id=rid)
        return cls.guest(request_id=rid)

    # ---- query helpers ---------------------------------------------------

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_anonymous(self) -> bool:
        return self.mode == AuthMode.GUEST

    @property
    def quota_key(self) -> str:
        """限流 / 配额的归并键。

        - User：按 user_id 聚合
        - App：按 app_id 聚合（后续可分通道）
        - Guest：按 IP/请求 ID 维度由调用方再次包装；这里仅返回 "guest"
        """
        if self.mode == AuthMode.APP and self.app_id:
            return f"app:{self.app_id}"
        if self.mode == AuthMode.USER and self.principal_id is not None:
            return f"user:{self.principal_id}"
        return "guest"

    def to_lineage_fields(self) -> Dict[str, Any]:
        """供 governance.lineage / metrics 落库使用的最小字段集。"""
        return {
            "principal_id": self.principal_id,
            "display_name": self.display_name,
            "role": self.role,
            "auth_mode": self.mode.value,
            "app_id": self.app_id,
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
        }
