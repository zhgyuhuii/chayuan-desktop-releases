"""ModelPlatform DB CRUD + 缓存版本号。

写操作（create/update/delete）会 ``bump_platform_version()``，
``server.utils.get_config_platforms`` 的 5s TTL 缓存以 ``_PLATFORM_VERSION`` 为
key，version 一变缓存就失效——这是"配置完即生效、不重启"的核心。
"""
from __future__ import annotations

import logging
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from chayuan.server.db.models.model_platform_model import ModelPlatformModel
from chayuan.server.db.session import session_scope

logger = logging.getLogger("chayuan.repository.model_platform")


# ---------------------------------------------------------------------------
# 全局版本号——任何写操作 +1，被 ``server.utils.get_config_platforms`` 当 cache key
# ---------------------------------------------------------------------------

_version_lock = Lock()
_PLATFORM_VERSION: int = 0


def get_platform_version() -> int:
    return _PLATFORM_VERSION


def bump_platform_version() -> int:
    global _PLATFORM_VERSION
    with _version_lock:
        _PLATFORM_VERSION += 1
        return _PLATFORM_VERSION


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------

def list_platforms() -> List[Dict[str, Any]]:
    """列出 DB 里的全部平台（包括 disabled）。"""
    with session_scope() as s:
        rows = s.query(ModelPlatformModel).order_by(ModelPlatformModel.id).all()
        return [r.to_dict() for r in rows]


def get_platform(name: str) -> Optional[Dict[str, Any]]:
    with session_scope() as s:
        row = (
            s.query(ModelPlatformModel)
            .filter(ModelPlatformModel.platform_name == name)
            .one_or_none()
        )
        return row.to_dict() if row else None


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------

_LIST_FIELDS = {
    "llm_models",
    "embed_models",
    "text2image_models",
    "image2text_models",
    "image2image_models",
    "rerank_models",
    "speech2text_models",
    "text2speech_models",
    "disabled_models",
}

_SCALAR_FIELDS = {
    "platform_type",
    "api_base_url",
    "api_key",
    "api_proxy",
    "api_concurrencies",
    "enabled",
    "auto_detect_model",
    "description",
    "extra",
}


def _coerce_list(v: Any) -> List[str]:
    """把"auto" / list / 单字符串都规范为 list[str]。"""
    if v is None:
        return []
    if isinstance(v, str):
        if v.strip().lower() == "auto":
            # auto_detect 走另一开关；这里不存 'auto' 字面量
            return []
        return [v.strip()]
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v if str(x).strip()]
    return []


def create_platform(
    *, platform_name: str, fields: Dict[str, Any], created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """新增。``platform_name`` 必填且唯一。"""
    name = (platform_name or "").strip()
    if not name:
        raise ValueError("platform_name 必填")

    payload: Dict[str, Any] = {"platform_name": name, "create_by": created_by, "update_by": created_by}
    for k, v in fields.items():
        if k in _LIST_FIELDS:
            payload[k] = _coerce_list(v)
        elif k in _SCALAR_FIELDS:
            payload[k] = v

    # JSON 列默认值
    for k in _LIST_FIELDS:
        payload.setdefault(k, [])

    try:
        with session_scope() as s:
            row = ModelPlatformModel(**payload)
            s.add(row)
            s.flush()
            data = row.to_dict()
        bump_platform_version()
        _maybe_clear_detect_cache()
        return data
    except IntegrityError as e:
        raise ValueError(f"platform_name '{name}' 已存在") from e


def update_platform(
    name: str, fields: Dict[str, Any], updated_by: Optional[str] = None,
) -> Dict[str, Any]:
    """更新。仅更新传入的字段。"""
    with session_scope() as s:
        row = (
            s.query(ModelPlatformModel)
            .filter(ModelPlatformModel.platform_name == name)
            .one_or_none()
        )
        if row is None:
            raise LookupError(f"platform '{name}' not found")

        changed = False
        for k, v in fields.items():
            if k in _LIST_FIELDS:
                setattr(row, k, _coerce_list(v))
                changed = True
            elif k in _SCALAR_FIELDS:
                setattr(row, k, v)
                changed = True
        if changed:
            row.update_by = updated_by
            row.update_time = datetime.utcnow()
            s.flush()
        data = row.to_dict()

    bump_platform_version()
    _maybe_clear_detect_cache()
    return data


def upsert_platform(
    *, platform_name: str, fields: Dict[str, Any], by: Optional[str] = None,
) -> Dict[str, Any]:
    """存在则 update，不存在则 create。"""
    if get_platform(platform_name):
        return update_platform(platform_name, fields, updated_by=by)
    return create_platform(platform_name=platform_name, fields=fields, created_by=by)


def delete_platform(name: str) -> bool:
    with session_scope() as s:
        row = (
            s.query(ModelPlatformModel)
            .filter(ModelPlatformModel.platform_name == name)
            .one_or_none()
        )
        if row is None:
            return False
        s.delete(row)
    bump_platform_version()
    _maybe_clear_detect_cache()
    return True


# ---------------------------------------------------------------------------
# 周边：清掉 ollama / xinference auto-detect 的 60s LRU 缓存，防止旧值残留
# ---------------------------------------------------------------------------

def _maybe_clear_detect_cache() -> None:
    try:
        from chayuan.server.utils import detect_ollama_models  # type: ignore
        detect_ollama_models.cache_clear()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    try:
        from chayuan.server.utils import detect_xf_models  # type: ignore
        detect_xf_models.cache_clear()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
