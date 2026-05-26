"""本地服务的"是否随 chayuan-server 启动时自动拉起"持久化设置。

用单个文件 ``<CHAYUAN_ROOT>/data/sidecar_settings.json``:

  {
    "auto_start": {
      "rapidocr":        true,
      "chat":            false,
      "embedding":       false,
      "rerank":          false,
      "asr":             false,
      "image-embedding": false
    }
  }

兼容老 schema(rapidocr_lifecycle 一开始用的扁平字段 ``rapidocr_auto_start``):
读取时若没有 ``auto_start`` 字典 + 有老字段,自动迁移。

为什么默认值有差异:
  - ``rapidocr`` / ``image-embedding`` 默认 ``true`` — 知识中心图像 KB 上传
    需要这两个 sidecar 协作(OCR 文字 + CLIP 向量),装了察元就该可用;
  - 其它 4 个 capability(chat / embedding / rerank / asr)默认 ``false`` —
    老用户原本就是手动启,贸然全开会拉起一堆没装模型的进程出 failed 状态
    污染 UI;用户在 UI 显式打开后再生效。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable

logger = logging.getLogger("chayuan.runtime.auto_start_store")

_SETTINGS_FILE_NAME = "sidecar_settings.json"
_LOCK = threading.Lock()

# 各 key 默认值 — 知识中心图像 KB 上传同时用 OCR + CLIP,两个都默认开,
# 其它需要用户显式打开。
_DEFAULTS: Dict[str, bool] = {
    "rapidocr": True,
    "image-embedding": True,
    "chat": False,
    "embedding": False,
    "rerank": False,
    "asr": False,
}

# UI / 前端用的所有已知 key — POST .../auto-start 会校验
KNOWN_KEYS = tuple(_DEFAULTS.keys())


def _settings_path() -> Path:
    """<CHAYUAN_ROOT>/data/sidecar_settings.json。"""
    from chayuan.settings import Settings
    base = Path(Settings.basic_settings.DATA_PATH)
    base.mkdir(parents=True, exist_ok=True)
    return base / _SETTINGS_FILE_NAME


def _read_raw() -> Dict[str, Any]:
    p = _settings_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        logger.warning("[auto-start-store] 读 %s 失败: %r;视作空配置", p, e)
        return {}


def _read_auto_start_dict() -> Dict[str, bool]:
    """读 auto_start 字典;兼容老的扁平 ``rapidocr_auto_start`` 字段。"""
    raw = _read_raw()
    auto = raw.get("auto_start") or {}
    if not isinstance(auto, dict):
        auto = {}
    auto_norm: Dict[str, bool] = {k: bool(v) for k, v in auto.items()}
    # 兼容老 schema:rapidocr_lifecycle 早期写过扁平 ``rapidocr_auto_start``
    legacy = raw.get("rapidocr_auto_start")
    if "rapidocr" not in auto_norm and isinstance(legacy, bool):
        auto_norm["rapidocr"] = legacy
    return auto_norm


def _write_raw(data: Dict[str, Any]) -> None:
    p = _settings_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def get(key: str) -> bool:
    """返回 key 的 auto-start 值;未设过返默认。"""
    with _LOCK:
        return bool(_read_auto_start_dict().get(key, _DEFAULTS.get(key, False)))


def set_(key: str, enabled: bool) -> bool:
    """设置某个 key 的 auto-start 并持久化。返回写入后的值。"""
    with _LOCK:
        raw = _read_raw()
        auto = dict(_read_auto_start_dict())
        auto[key] = bool(enabled)
        raw["auto_start"] = auto
        # 同时清理掉老的扁平字段(以新 schema 为准)
        raw.pop("rapidocr_auto_start", None)
        _write_raw(raw)
        logger.info("[auto-start-store] %s := %s", key, enabled)
        return bool(enabled)


def list_all(keys: Iterable[str] | None = None) -> Dict[str, bool]:
    """返回 {key: auto_start} 字典;keys 不给就返已知全集。"""
    target = list(keys) if keys is not None else list(KNOWN_KEYS)
    with _LOCK:
        stored = _read_auto_start_dict()
    return {k: bool(stored.get(k, _DEFAULTS.get(k, False))) for k in target}


# ───────────────────── bootstrap 标记(first_launch 用) ─────────────────────
#
# first_launch 在 seed_bundled_models 之后,会针对 bundled 有内容的 capability
# 把 preload_*/auto_start.* 翻成 True,实现"装机即用"。该翻转必须幂等 ——
# 用户后续在 UI 显式关掉某个 cap 之后,下次启动不能再被翻回 True。
#
# 这里用一个独立的 ``bootstrapped_caps`` 数组,记录"我已经替这个 cap 翻过初始
# 默认值"。是否需要翻 → 看这个 list 里有没有 cap;翻过之后用 mark_bootstrapped
# 标。即使用户清掉 sidecar_settings.json,首次启动会重新跑一次 bootstrap,
# 也算"开箱即用"的合理重置。
#
# 与 auto_start dict 共存于同一份 sidecar_settings.json,不引入新文件。

_BOOTSTRAPPED_FIELD = "bootstrapped_caps"


def _read_bootstrapped_set() -> set:
    raw = _read_raw()
    arr = raw.get(_BOOTSTRAPPED_FIELD) or []
    if not isinstance(arr, list):
        return set()
    return {str(x) for x in arr if isinstance(x, (str, int))}


def is_bootstrapped(cap: str) -> bool:
    """该 cap 是否已经被 first_launch 翻过初始 preload/auto_start 默认。"""
    with _LOCK:
        return cap in _read_bootstrapped_set()


def mark_bootstrapped(cap: str) -> None:
    """记录 cap 已被翻过初始默认。幂等。"""
    with _LOCK:
        raw = _read_raw()
        cur = _read_bootstrapped_set()
        if cap in cur:
            return
        cur.add(cap)
        raw[_BOOTSTRAPPED_FIELD] = sorted(cur)
        _write_raw(raw)
        logger.info("[auto-start-store] mark_bootstrapped %s", cap)
