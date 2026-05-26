"""自动分析样本字段 —— 给 UI"自动分析"按钮 + materializer 用。

输入: 一组 :class:`DocumentRecord` (通常 N=20-100)
输出: ``[FieldSchema, ...]`` 描述 metadata 字段的画像

设计原则:
* 只看 ``metadata``;``text`` 字段统一不分析(它就是"长文本",没意义)
* 类型推断容忍 mixed: 大多数 string 即标 string;混合则标 mixed
* 抽样值最多保留 5 个,长度不超过 80 char
* 非空率 fill_rate 用于 UI 标"几乎全空"红字提示
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Sequence

from chayuan.server.data_mount.base import DocumentRecord, FieldSchema


_TYPE_PRIORITY = {"null": 0, "bool": 1, "int": 2, "float": 3, "string": 4, "list": 5, "dict": 6}


def _type_of(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return "string"


def _truncate(v: Any) -> Any:
    s = str(v)
    return s if len(s) <= 80 else s[:77] + "..."


def analyze_schema(records: Sequence[DocumentRecord]) -> List[FieldSchema]:
    if not records:
        return []
    n = len(records)
    type_counts: Dict[str, Counter] = {}
    fill_counts: Counter = Counter()
    sample_values: Dict[str, list] = {}
    unique: Dict[str, set] = {}

    for r in records:
        for k, v in (r.metadata or {}).items():
            type_counts.setdefault(k, Counter())[_type_of(v)] += 1
            if v is not None and v != "":
                fill_counts[k] += 1
            sample_values.setdefault(k, [])
            if len(sample_values[k]) < 5 and v not in (None, ""):
                if v not in sample_values[k]:
                    sample_values[k].append(v)
            unique.setdefault(k, set())
            try:
                unique[k].add(_truncate(v))
            except Exception:  # noqa: BLE001
                pass

    out: List[FieldSchema] = []
    for k, types in type_counts.items():
        # 判定主类型: 选频率最高 + 非 null 优先
        non_null = {t: c for t, c in types.items() if t != "null"}
        if non_null:
            primary = max(non_null.items(), key=lambda kv: kv[1])[0]
        else:
            primary = "null"
        if len(non_null) > 1:
            primary = f"mixed:{primary}"  # 显示"主+其它"
        notes = ""
        if fill_counts.get(k, 0) < n:
            notes = f"非空率 {fill_counts.get(k, 0)}/{n}"
        out.append(FieldSchema(
            name=k,
            type=primary,
            sample_values=[_truncate(s) for s in sample_values.get(k, [])],
            fill_rate=fill_counts.get(k, 0) / n,
            unique_count=len(unique.get(k, ())),
            notes=notes,
        ))

    # 按"信息熵"近似排序: unique_count 大 + fill_rate 高的字段排前
    out.sort(key=lambda f: (-f.fill_rate, -f.unique_count))
    return out


__all__ = ["analyze_schema"]
