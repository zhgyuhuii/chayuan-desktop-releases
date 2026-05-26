"""72 题诊断:dump 厂商 + 模型在各层的真实状态。

仅供排查使用。生产环境若不希望暴露,把 DEBUG_DUMP_ENABLED 设为 False。

数据流(用户端 ④ 默认模型 / chayuan-client 模型下拉真正读到的链路):

    yaml(model_settings.yaml)
      │
      ├─ Settings.model_settings.MODEL_PLATFORMS    ← Settings 缓存 + yaml mtime
      │
      ├─ model_platform DB 表(by admin UI / _save_all 71 修复)
      │
      └─ _resolved_platforms() (5s LRU,key=platform_version)
           └─ _merge_platforms(yaml_seed, db_rows, json_overrides)
                └─ get_config_platforms() / get_config_models()
                     └─ /v1/models 返回的 data
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter

logger = logging.getLogger("chayuan.api.debug_routes")

debug_router = APIRouter(prefix="/api/debug", tags=["debug"])


@debug_router.get(
    "/dump_platforms",
    summary="72 题诊断:dump 厂商在 yaml/DB/Settings/get_config_models 各层的实际状态",
)
def dump_platforms(name: str = "deepseek") -> Dict[str, Any]:
    """精准定位某个厂商(默认 deepseek)在每一层的状态。

    访问 ``http://<server>/api/debug/dump_platforms?name=deepseek``。
    """
    out: Dict[str, Any] = {"target_name": name, "layers": {}}

    # ---- 1. yaml 直读 ----
    try:
        from chayuan.server.config_panel import yaml_store
        load = yaml_store.load_yaml("model_settings.yaml")
        doc = load.doc if isinstance(load.doc, dict) else {}
        platforms = doc.get("MODEL_PLATFORMS") or []
        match = [p for p in platforms if p.get("platform_name") == name]
        out["layers"]["yaml"] = {
            "exists": load.exists,
            "path": str(load.path),
            "total_platforms": len(platforms),
            "all_platform_names": [p.get("platform_name") for p in platforms],
            "target_entry": match[0] if match else None,
        }
    except Exception as e:  # noqa: BLE001
        out["layers"]["yaml"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 2. Settings.model_settings.MODEL_PLATFORMS(经 mtime cache + yaml 加载) ----
    try:
        from chayuan.settings import Settings
        platforms_obj = list(Settings.model_settings.MODEL_PLATFORMS or [])
        match_obj = [p for p in platforms_obj
                     if str(getattr(p, "platform_name", "")) == name]
        out["layers"]["settings"] = {
            "total": len(platforms_obj),
            "all_platform_names": [
                str(getattr(p, "platform_name", "")) for p in platforms_obj
            ],
            "target_dump": (
                match_obj[0].model_dump() if match_obj
                and hasattr(match_obj[0], "model_dump") else None
            ),
        }
    except Exception as e:  # noqa: BLE001
        out["layers"]["settings"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 3. model_platform DB 表 ----
    try:
        from chayuan.server.db.repository.model_platform_repository import (
            list_platforms, get_platform_version,
        )
        rows = list_platforms() or []
        match_db = [r for r in rows if r.get("platform_name") == name]
        out["layers"]["db"] = {
            "platform_version": get_platform_version(),
            "total": len(rows),
            "all_platform_names": [r.get("platform_name") for r in rows],
            "target_row": match_db[0] if match_db else None,
        }
    except Exception as e:  # noqa: BLE001
        out["layers"]["db"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 4. _resolved_platforms() (经 5s LRU + merge) ----
    try:
        from chayuan.server.utils import (
            get_all_platforms_with_state,
            _resolved_platforms,
            _get_platform_version_safe,
        )
        merged = _resolved_platforms(_get_platform_version_safe())
        match_merged = [m for m in merged if m.get("platform_name") == name]
        out["layers"]["merged"] = {
            "total": len(merged),
            "all_platform_names": [m.get("platform_name") for m in merged],
            "target_merged": match_merged[0] if match_merged else None,
        }
    except Exception as e:  # noqa: BLE001
        out["layers"]["merged"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 5. get_config_platforms (过滤 enabled=False) ----
    try:
        from chayuan.server.utils import get_config_platforms
        cfg = get_config_platforms() or {}
        out["layers"]["enabled_only"] = {
            "total": len(cfg),
            "all_platform_names": list(cfg.keys()),
            "target_present": name in cfg,
            "target_value": cfg.get(name),
        }
    except Exception as e:  # noqa: BLE001
        out["layers"]["enabled_only"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 6. get_config_models(model_type='llm') 实际拿到的模型 ----
    try:
        from chayuan.server.utils import get_config_models
        models = get_config_models(model_type="llm") or {}
        # 按 platform 分组方便看
        by_platform: Dict[str, List[str]] = {}
        for mid, info in models.items():
            pname = str(info.get("platform_name") or "")
            by_platform.setdefault(pname, []).append(mid)
        out["layers"]["llm_models"] = {
            "total": len(models),
            "by_platform": by_platform,
            "target_models": by_platform.get(name, []),
        }
    except Exception as e:  # noqa: BLE001
        out["layers"]["llm_models"] = {"error": f"{type(e).__name__}: {e}"}

    # ---- 7. 诊断结论 ----
    diag: List[str] = []
    yaml_layer = out["layers"].get("yaml") or {}
    settings_layer = out["layers"].get("settings") or {}
    db_layer = out["layers"].get("db") or {}
    merged_layer = out["layers"].get("merged") or {}
    enabled_layer = out["layers"].get("enabled_only") or {}
    llm_layer = out["layers"].get("llm_models") or {}

    if yaml_layer.get("target_entry"):
        diag.append("✅ yaml 中已存在 deepseek 条目")
    else:
        diag.append("❌ yaml 中**没有** deepseek — 用户没真保存,或 yaml 路径不对")

    if settings_layer.get("target_dump"):
        diag.append("✅ Settings.model_settings 已加载 deepseek")
    else:
        diag.append("⚠️ Settings.model_settings **没有** deepseek — Settings cache 没刷新?(mtime 同秒?)")

    if db_layer.get("target_row"):
        row = db_layer["target_row"]
        if row.get("enabled"):
            diag.append("✅ DB 中 deepseek enabled=True")
        else:
            diag.append("❌ DB 中 deepseek **enabled=False** — 即使 yaml 启用,DB 覆盖会让最终禁用!")
    else:
        diag.append("⚠️ DB 中**没有** deepseek — 71 修复(_save_all 同步写 DB)未生效;进程没重启?")

    if merged_layer.get("target_merged"):
        m = merged_layer["target_merged"]
        diag.append(
            f"merged 后 deepseek: enabled={m.get('enabled')}, "
            f"llm_models={m.get('llm_models')}"
        )

    if enabled_layer.get("target_present"):
        diag.append("✅ get_config_platforms() 包含 deepseek")
    else:
        diag.append("❌ get_config_platforms() **不含** deepseek — 被 enabled=False 过滤")

    target_models = llm_layer.get("target_models") or []
    if target_models:
        diag.append(f"✅ get_config_models(llm) 返回 deepseek 模型: {target_models}")
    else:
        diag.append("❌ get_config_models(llm) **没有** deepseek 的 llm_models")

    out["diagnosis"] = diag
    return out
