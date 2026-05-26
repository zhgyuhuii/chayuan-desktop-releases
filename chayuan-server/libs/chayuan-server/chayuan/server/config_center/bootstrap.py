"""启动期种子迁移：yaml → DB。

只在对应 namespace 在 DB 里**完全为空**时才做一次导入；避免每次启动都把
yaml 盖回 DB（运维在面板改完 DB，yaml 可能是老的 snapshot）。

设计约定：yaml 里顶层的每个 key 就是 config_center 的 key；value 原样存 JSON。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import select

from .models import ConfigEntry


def _session():
    from chayuan.server.db.base import SessionLocal as _SL
    return _SL()


logger = logging.getLogger("chayuan.config_center.bootstrap")


def _namespace_empty(namespace: str) -> bool:
    try:
        with _session() as session:
            row = session.execute(
                select(ConfigEntry.id)
                .where(ConfigEntry.namespace == namespace)
                .limit(1)
            ).first()
            return row is None
    except Exception as e:  # noqa: BLE001
        logger.warning("bootstrap: check ns %s failed: %r", namespace, e)
        return False


def _insert(namespace: str, doc: Dict[str, Any], *, comment: str) -> int:
    count = 0
    try:
        with _session() as session:
            for key, value in doc.items():
                raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
                session.add(ConfigEntry(
                    namespace=namespace, key=str(key), value=raw,
                    version=1, updated_by="bootstrap",
                    comment=comment,
                ))
                count += 1
            session.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("bootstrap: insert ns=%s failed: %r", namespace, e)
    return count


def seed_from_yaml(namespace: str, yaml_path: Path, *, top_key: str = "") -> int:
    """如果 ``namespace`` 在 DB 里为空，从 yaml 导入一次种子数据。

    - ``top_key=""``：把 yaml 的顶层 dict 当成 namespace 的 key-value 全集；
    - ``top_key="apps"``：只读 yaml 里 ``apps: [...]`` 列表，并以 ``list`` 作为单条
      ``value`` 存到 key ``apps`` 下（我们加的三个 store 就是这种结构）。

    返回种子条数；0 表示没做（已有数据或 yaml 缺失）。
    """
    if not _namespace_empty(namespace):
        return 0
    if not yaml_path.is_file():
        return 0

    try:
        from chayuan.pydantic_settings_file import import_yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            doc = import_yaml().load(f) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("bootstrap: load %s failed: %r", yaml_path, e)
        return 0

    if top_key:
        if top_key not in doc:
            return 0
        payload = {top_key: doc[top_key]}
    else:
        if not isinstance(doc, dict):
            return 0
        payload = dict(doc)

    n = _insert(namespace, payload, comment=f"seeded from {yaml_path.name}")
    if n:
        logger.info("bootstrap: seeded %d keys into ns=%s from %s",
                    n, namespace, yaml_path)
    return n


# ============================================================================
# 51 题:ensure_seeded — yaml ↔ DB 强一致性检查
# ============================================================================


def _existing_keys(namespace: str) -> set:
    """返回 DB 里 namespace 已有的所有 key。失败返空 set(让上层退化为 seed-all)。"""
    try:
        with _session() as session:
            rows = session.execute(
                select(ConfigEntry.key).where(ConfigEntry.namespace == namespace)
            ).all()
            return {str(r[0]) for r in rows}
    except Exception as e:  # noqa: BLE001
        logger.warning("bootstrap: list keys ns=%s failed: %r", namespace, e)
        return set()


def ensure_seeded(
    namespace: str,
    yaml_path: Path,
    *,
    top_key: str = "",
) -> Dict[str, int]:
    """检测 yaml 是否已同步到 DB,缺什么就补什么(delta seed)。

    与 :func:`seed_from_yaml` 的差别:
      * ``seed_from_yaml`` 只在 namespace **完全为空**时一次性 seed
      * ``ensure_seeded`` **检查每个 key**,缺了就单独补,已有的不动
        → 升级 chayuan 后 yaml 新增了字段也能自动同步到 DB

    返回:::

        {"seeded": N, "matched": M, "total": K, "skipped": int}
        # seeded:本次新写入 DB 的 key 数
        # matched:DB 里已有,跳过(尊重用户在面板改过的值)
        # total:yaml 顶层 key 总数
        # skipped:yaml 缺失 / 解析失败时的 fallback

    幂等:多次调用同一 (namespace, yaml_path) 不会重复 insert。
    """
    report = {"seeded": 0, "matched": 0, "total": 0, "skipped": 0}

    if not yaml_path.is_file():
        logger.debug("bootstrap.ensure_seeded: %s 不存在,跳过", yaml_path)
        report["skipped"] = 1
        return report

    try:
        from chayuan.pydantic_settings_file import import_yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            doc = import_yaml().load(f) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("bootstrap.ensure_seeded: load %s failed: %r", yaml_path, e)
        report["skipped"] = 1
        return report

    if top_key:
        if top_key not in doc:
            report["skipped"] = 1
            return report
        payload: Dict[str, Any] = {top_key: doc[top_key]}
    else:
        if not isinstance(doc, dict):
            report["skipped"] = 1
            return report
        payload = dict(doc)

    report["total"] = len(payload)

    # 已存在的 key 跳过,只 seed 缺失的
    existing = _existing_keys(namespace)
    missing_payload = {k: v for k, v in payload.items() if k not in existing}
    report["matched"] = len(payload) - len(missing_payload)

    if missing_payload:
        n = _insert(
            namespace, missing_payload,
            comment=f"ensure_seeded delta from {yaml_path.name}",
        )
        report["seeded"] = n
        logger.info(
            "bootstrap.ensure_seeded: ns=%s — 新增 %d keys / 已存在 %d / 总 %d "
            "(yaml=%s)",
            namespace, n, report["matched"], report["total"], yaml_path.name,
        )
    else:
        # 全部已在 DB
        if report["total"] > 0:
            logger.debug(
                "bootstrap.ensure_seeded: ns=%s 全部 %d keys 都已在 DB(yaml=%s)",
                namespace, report["total"], yaml_path.name,
            )
    return report
