"""开放平台「App 注册」持久化：``CHAYUAN_ROOT/apps.yaml``。

结构：

    apps:
      - app_id: 8e3c...            # 32 hex
        app_secret: "xDCp..."       # token_urlsafe(48)
        name: 我的 CRM 集成
        enabled: true
        created_at: "2026-04-21T20:00:00"
        callback_url: https://crm.example.com/chayuan/callback
        callback_events: [chat.completed, kb.doc.updated]
        scopes: [chat, kb]          # 白名单 API 命名空间

管理者通过面板创建：自动生成 app_id + secret（secret 仅创建 / 轮换时明文一次）。
运行时 ``AppAuthMiddleware`` 会按 ``app_id`` 查到 ``app_secret`` 做签名校验。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from chayuan.pydantic_settings_file import import_yaml

from chayuan.server.shared.app_signing import new_app_id, new_app_secret
from chayuan.server.shared.scopes import (
    ALL_SCOPES,
    EVENT_SCOPE_MAP,
    RESOURCE_WILDCARDS,
    sanitize_scope_list,
)
from chayuan.server.shared import secret_store


_FILENAME = "apps.yaml"

# 细粒度 scope + 资源级通配 + 全通配，与 shared.scopes.ALL_SCOPES 保持一致
_ALLOWED_SCOPES = tuple(ALL_SCOPES) + RESOURCE_WILDCARDS + ("*", "*:*")
# 可订阅的事件列表直接来自 EVENT_SCOPE_MAP 的 key，再加几个 pattern 供客户端选
_ALLOWED_EVENTS = tuple(sorted(EVENT_SCOPE_MAP.keys())) + (
    "chat.*", "kb.doc.*", "app.*",
)


@dataclass
class AppSpec:
    app_id: str
    app_secret: str
    name: str = ""
    enabled: bool = True
    created_at: str = ""
    callback_url: str = ""
    callback_events: List[str] = field(default_factory=list)
    scopes: List[str] = field(default_factory=list)
    ku_ids: List[str] = field(default_factory=list)
    tool_ids: List[str] = field(default_factory=list)

    def to_dict(self, *, include_secret: bool = True) -> Dict[str, Any]:
        d = asdict(self)
        if not include_secret:
            d["app_secret"] = _mask(self.app_secret)
        return d


def _mask(s: str) -> str:
    if not s or len(s) <= 8:
        return "****"
    return f"{s[:4]}...{s[-4:]}"


def _path() -> Path:
    # 懒查询 CHAYUAN_ROOT；也支持单测/运维用 ``CHAYUAN_APPS_YAML`` 环境变量直接
    # 指定 yaml 完整路径，无需切整个数据目录。
    import os
    override = os.environ.get("CHAYUAN_APPS_YAML")
    if override:
        return Path(override).expanduser()
    from chayuan.settings import CHAYUAN_ROOT as _ROOT
    return Path(_ROOT) / _FILENAME


# ---------------------------------------------------------------------------
# 持久化：优先走配置中心（DB + Redis），DB 不可达时降级 yaml
# ---------------------------------------------------------------------------

_NAMESPACE = "apps"
_ROOT_KEY = "apps"   # 整个列表以单条 ConfigEntry 存，key=apps


def _config_center_disabled() -> bool:
    import os
    return os.environ.get("CHAYUAN_CONFIG_CENTER_DISABLED", "").strip() in (
        "1", "true", "yes", "on",
    )


def _load() -> Dict[str, Any]:
    if _config_center_disabled():
        p = _path()
        if not p.is_file():
            return {"apps": []}
        with open(p, "r", encoding="utf-8") as f:
            return import_yaml().load(f) or {"apps": []}
    try:
        from chayuan.server.config_center import get_store
        val = get_store().get(
            _NAMESPACE, _ROOT_KEY, default=None,
            yaml_fallback_path=_path(),
        )
        if val is None:
            return {"apps": []}
        if isinstance(val, list):
            return {"apps": val}
        if isinstance(val, dict):
            # 老 yaml 格式：顶层就是 {"apps": [...]}
            return val
        return {"apps": []}
    except Exception:  # noqa: BLE001
        # 兜底纯 yaml 读
        p = _path()
        if not p.is_file():
            return {"apps": []}
        with open(p, "r", encoding="utf-8") as f:
            return import_yaml().load(f) or {"apps": []}


def _dump(doc: Dict[str, Any]) -> None:
    apps_list = doc.get("apps", []) or []
    # 1) 写配置中心（DB + Redis + 发布变更）；可通过 env kill switch 禁用
    if not _config_center_disabled():
        try:
            from chayuan.server.config_center import get_store
            get_store().set(
                _NAMESPACE, _ROOT_KEY, apps_list,
                updated_by="apps_store", comment="panel save",
            )
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("chayuan.apps_store").warning(
                "config_center 写入失败，降级只写 yaml：%r", e,
            )
    # 2) yaml 镜像（即便 DB OK 也写一份作 DR 快照 + 方便运维直接看）
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        import_yaml().dump(doc, f)
    import os
    os.replace(tmp, p)


def _parse(raw: Dict[str, Any]) -> AppSpec:
    # 读取时按需解密；明文老 yaml 走降级不抛错
    raw_secret = str(raw.get("app_secret", ""))
    return AppSpec(
        app_id=str(raw.get("app_id", "")),
        app_secret=secret_store.decrypt(raw_secret),
        name=str(raw.get("name", "")),
        enabled=bool(raw.get("enabled", True)),
        created_at=str(raw.get("created_at", "")),
        callback_url=str(raw.get("callback_url", "")),
        callback_events=list(raw.get("callback_events") or []),
        scopes=list(raw.get("scopes") or []),
        ku_ids=_sanitize_text_list(raw.get("ku_ids") or []),
        tool_ids=_sanitize_text_list(raw.get("tool_ids") or []),
    )


def _persist_secret(spec_dict: Dict[str, Any]) -> Dict[str, Any]:
    """写入 yaml 前，把 ``app_secret`` 字段加密（若已是密文或未启用加密则原样）。"""
    sec = spec_dict.get("app_secret")
    if isinstance(sec, str) and sec:
        spec_dict["app_secret"] = secret_store.encrypt(sec)
    return spec_dict


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_apps() -> List[AppSpec]:
    doc = _load()
    return [_parse(x) for x in (doc.get("apps") or []) if isinstance(x, dict)]


def get_app(app_id: str) -> Optional[AppSpec]:
    for a in list_apps():
        if a.app_id == app_id:
            return a
    return None


def create_app(
    name: str,
    *,
    callback_url: str = "",
    callback_events: Optional[List[str]] = None,
    scopes: Optional[List[str]] = None,
) -> AppSpec:
    # scope 走细粒度清洗（支持老 ``chat / kb / tools / admin`` 自动转为 ``chat:*`` 等）
    initial_scopes = sanitize_scope_list(scopes or []) or ["chat:read"]
    spec = AppSpec(
        app_id=new_app_id(),
        app_secret=new_app_secret(),
        name=name.strip() or "新应用",
        enabled=True,
        created_at=datetime.now().isoformat(timespec="seconds"),
        callback_url=callback_url.strip(),
        callback_events=_sanitize(callback_events, _ALLOWED_EVENTS),
        scopes=initial_scopes,
    )
    doc = _load()
    lst = list(doc.get("apps") or [])
    lst.append(_persist_secret(spec.to_dict()))
    doc["apps"] = lst
    _dump(doc)
    return spec


def update_app(
    app_id: str,
    *,
    name: Optional[str] = None,
    enabled: Optional[bool] = None,
    callback_url: Optional[str] = None,
    callback_events: Optional[List[str]] = None,
    scopes: Optional[List[str]] = None,
    ku_ids: Optional[List[str]] = None,
    tool_ids: Optional[List[str]] = None,
) -> Optional[AppSpec]:
    doc = _load()
    lst = list(doc.get("apps") or [])
    for i, raw in enumerate(lst):
        if not isinstance(raw, dict) or raw.get("app_id") != app_id:
            continue
        if name is not None:
            raw["name"] = name
        if enabled is not None:
            raw["enabled"] = bool(enabled)
        if callback_url is not None:
            raw["callback_url"] = callback_url.strip()
        if callback_events is not None:
            raw["callback_events"] = _sanitize(callback_events, _ALLOWED_EVENTS)
        if scopes is not None:
            raw["scopes"] = sanitize_scope_list(scopes)
        if ku_ids is not None:
            raw["ku_ids"] = _sanitize_text_list(ku_ids)
        if tool_ids is not None:
            raw["tool_ids"] = _sanitize_text_list(tool_ids)
        lst[i] = raw
        doc["apps"] = lst
        _dump(doc)
        return _parse(raw)
    return None


def rotate_secret(app_id: str, *, admin_user: Optional[Dict[str, Any]] = None) -> Optional[AppSpec]:
    """轮换 secret；旧 secret 立即失效。

    先把新 secret 明文赋给 raw（便于后续 _parse 解密时读到正确值），再通过
    ``_persist_secret`` 写入时加密；面板弹窗展示的是解密后的明文。

    plan v1.3 §6.5 第 5 项：rotate 是 7 类审计事件之一，
    自动 hook ``kb_audit.app_rotated``（永不抛异常）。``admin_user`` 由调用方传入，
    没传则记 NULL（兼容旧调用点）。
    """
    doc = _load()
    lst = list(doc.get("apps") or [])
    for i, raw in enumerate(lst):
        if isinstance(raw, dict) and raw.get("app_id") == app_id:
            new_plain = new_app_secret()
            raw["app_secret"] = new_plain
            # 持久化前加密（会把明文覆盖为 enc:v1:...）
            lst[i] = _persist_secret(raw)
            doc["apps"] = lst
            _dump(doc)
            # 返回给调用方的 AppSpec 仍带明文（供「轮换完明文展示一次」场景）
            ret = _parse({**raw, "app_secret": new_plain})
            # audit hook（fail-open；不影响 rotate 本身）
            try:
                from chayuan.server.auth import kb_audit
                kb_audit.app_rotated(admin_user, app_id=app_id, app_name=ret.name)
            except Exception:  # noqa: BLE001
                pass
            return ret
    return None


def delete_app(app_id: str) -> bool:
    doc = _load()
    lst = list(doc.get("apps") or [])
    new = [x for x in lst if not (isinstance(x, dict) and x.get("app_id") == app_id)]
    if len(new) == len(lst):
        return False
    doc["apps"] = new
    _dump(doc)
    try:
        from chayuan.server.auth.app_acl import purge_app_grants
        purge_app_grants(app_id)
    except Exception:
        pass
    return True


def _sanitize(values: Optional[List[str]], allowed: tuple) -> List[str]:
    if not values:
        return []
    s = set(allowed)
    return [v for v in values if v in s]


def _sanitize_text_list(values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


ALLOWED_SCOPES = _ALLOWED_SCOPES
ALLOWED_EVENTS = _ALLOWED_EVENTS
