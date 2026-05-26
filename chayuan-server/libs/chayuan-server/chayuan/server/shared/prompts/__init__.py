"""Prompt 版本化仓库（P2-6）。

为什么要沉下来？
- 线上 prompt 调优不应该走代码合并 → 重启；应能热改、A/B、回滚；
- 同一 prompt 往往多处被 import（graph_text2sql 的 generate_sys、supervisor 三个角色提示），
  散在 .py 里没法集中管理；
- 迁移阶段不能破坏既有调用点 —— 保留 fallback：YAML 缺失时直接返回 inline 默认值。

用法：
    from chayuan.server.shared.prompts import load_prompt
    sys = load_prompt("text2sql.classify")

    # A/B 分桶（按稳定 user_id 哈希）
    sys = load_prompt("text2sql.generate", ab_key=str(user_id))

YAML 结构（``registry.yaml`` 按 name 聚合）：

    text2sql.classify:
      default: v1
      versions:
        v1:
          body: |
            你是数据库问答分诊员...
        v2:
          body: |
            <new prompt>
      ab:
        split: 0.3    # 30% 用户命中 b
        a: v1
        b: v2
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("chayuan.shared.prompts")

_LOCK = threading.Lock()
_CACHE: Optional[Dict[str, Any]] = None
_MTIME: float = 0.0


def _registry_path() -> Path:
    return Path(__file__).resolve().parent / "registry.yaml"


def _load_yaml() -> Dict[str, Any]:
    """读 registry.yaml；文件缺失 / yaml 未装时返回空 dict。带 mtime 缓存。"""
    global _CACHE, _MTIME
    p = _registry_path()
    try:
        mtime = p.stat().st_mtime if p.exists() else 0.0
    except Exception:  # noqa: BLE001
        mtime = 0.0
    if _CACHE is not None and mtime == _MTIME:
        return _CACHE
    with _LOCK:
        if _CACHE is not None and mtime == _MTIME:
            return _CACHE
        data: Dict[str, Any] = {}
        if p.exists():
            try:
                import yaml  # type: ignore
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception as e:  # noqa: BLE001
                logger.warning("registry.yaml 解析失败：%r", e)
                data = {}
        _CACHE = data
        _MTIME = mtime
    return _CACHE


def _pick_version(entry: Dict[str, Any], ab_key: Optional[str]) -> str:
    """优先级：用户显式 version > A/B 分桶 > default。"""
    versions = entry.get("versions") or {}
    if not versions:
        return ""
    default_ver = str(entry.get("default") or next(iter(versions.keys())))
    ab = entry.get("ab") or {}
    if ab_key and ab and ab.get("split") is not None and "a" in ab and "b" in ab:
        try:
            bucket = int(hashlib.md5(ab_key.encode("utf-8")).hexdigest(), 16) % 10000
            threshold = int(float(ab["split"]) * 10000)
            return str(ab["b"] if bucket < threshold else ab["a"])
        except Exception:  # noqa: BLE001
            return default_ver
    return default_ver


# 内置兜底（P2-6 迁入第一批）：当 registry.yaml 缺失或 key 未录入时使用，
# 与历史代码内 inline 提示保持一致，保证无 YAML 的 dev 环境依然跑得起来。
_FALLBACK: Dict[str, str] = {
    "text2sql.classify": """你是数据库问答分诊员。给定用户问题和可用表清单（只含表名与注释），
仅需判断"基于这些表，问题原则上能否用 SELECT 查询回答"。返回 JSON：
{"can_answer": true|false, "reason": "一句话"}。
注意：不判断 SQL 是否简单，只判断"是否可回答"。""",
    # text2sql.generate：{dialect} / {top_k} 留给调用方 .format 填充。
    "text2sql.generate": """你是 {dialect} 数据库资深工程师。请严格遵守以下规则：
1. 只生成只读 SELECT，绝不生成 INSERT/UPDATE/DELETE/DDL。
2. 仅使用给定「可用表」中的表名与列名。
3. 必要时使用方言原生函数（MySQL: NOW()、PostgreSQL: CURRENT_DATE、Oracle: SYSDATE 等）。
4. 自动 LIMIT {top_k}，聚合/排行 类问题默认 DESC。
5. 参考「历史成功范例」（question → sql）的风格；不要照抄范例里不存在的列。
6. 输出严格 JSON：{{"sql": "<SQL>", "reason": "<两句话理由>"}}。
7. 若无法用给定表回答，输出 {{"sql": "", "reason": "无法回答：<简述>"}}。""",
    "supervisor.researcher": """你是调研员。给你用户问题后：
1. 使用所给工具收集资料（知识库 / 搜索 / SQL 等）
2. 产出一份"事实清单 + 证据出处"交给写作员
不要自己下结论。""",
    "supervisor.writer": """你是撰写员。依据调研员给出的事实清单，写一份完整、结构化、可读的答复。
- 引用证据时标[出处编号]
- 篇幅适中（300-800 字）；结构：开门见山 → 论据 → 结论""",
    "supervisor.reviewer": """你是审校员。检查写作员的草稿：
- 事实准确性（有没有引用错）
- 结构合理性（开头/论据/结论齐全）
- 风险（有没有泄露敏感信息 / 过度推断）
若有问题直接回改；若 OK 输出 FINAL:<原文>。""",
}


def load_prompt(
    name: str,
    *,
    version: Optional[str] = None,
    ab_key: Optional[str] = None,
    default: Optional[str] = None,
) -> str:
    """按 name 加载 prompt 文本。

    - ``version``：显式选某版本，忽略 A/B 分桶
    - ``ab_key``：A/B 分桶命中键（通常是 user_id）
    - ``default``：既没在 YAML 里也没在内置 fallback 里时的兜底；仍缺则返回空串

    任何异常都 swallow，fallback 到空串 + 日志 warning，保证业务路径不被 prompt
    系统挂掉。
    """
    try:
        data = _load_yaml()
        entry = (data.get(name) or {}) if isinstance(data, dict) else {}
        versions = entry.get("versions") if isinstance(entry, dict) else None
        if isinstance(versions, dict) and versions:
            ver = str(version or _pick_version(entry, ab_key) or "")
            body = (versions.get(ver) or {}).get("body") if isinstance(versions.get(ver), dict) else ""
            if body:
                return str(body)
    except Exception as e:  # noqa: BLE001
        logger.warning("load_prompt(%s) YAML 路径失败：%r", name, e)

    # fallback：内置 → default
    if name in _FALLBACK:
        return _FALLBACK[name]
    return str(default or "")


def list_registered() -> Dict[str, str]:
    """config_panel 用：展示当前生效的 prompt 名 → 版本。"""
    out: Dict[str, str] = {}
    try:
        data = _load_yaml()
        if isinstance(data, dict):
            for name, entry in data.items():
                if isinstance(entry, dict) and entry.get("default"):
                    out[name] = str(entry["default"])
    except Exception:  # noqa: BLE001
        pass
    for name in _FALLBACK:
        out.setdefault(name, "builtin")
    return out


def clear_cache() -> None:
    """手动清缓存；config_panel 改完 YAML 后调用。"""
    global _CACHE, _MTIME
    with _LOCK:
        _CACHE = None
        _MTIME = 0.0
