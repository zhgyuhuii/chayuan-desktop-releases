"""配置面板的 YAML 读写工具。

职责：
- 从 ``CHAYUAN_ROOT / <name>`` 加载 yaml（ruamel，保留注释和结构）；
- 按「点号路径」写回单个字段，或整体覆盖为新的 doc（仍走 ruamel 保留注释）；
- 提供一个把表单值标准化为 YAML 值的 ``coerce_for_widget``，让上层 UI 不需要关心类型。

注意：这里所有写操作都会创建 ``<file>.bak`` 备份，并通过「写临时文件 + 原子重命名」
防止写到一半崩溃。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from chayuan.pydantic_settings_file import import_yaml


@dataclass
class LoadResult:
    doc: Any
    """ruamel.yaml 解析出的文档对象（CommentedMap / CommentedSeq / None）。"""

    path: Path
    exists: bool


def yaml_path(name: str) -> Path:
    """返回目标 yaml 的绝对路径（不检查存在性）。

    懒取 ``CHAYUAN_ROOT``：避免 import 时冻结；单测切换 CHAYUAN_ROOT 也能立即生效。
    """
    from chayuan.settings import CHAYUAN_ROOT as _ROOT
    return Path(_ROOT) / name


def load_yaml(name: str) -> LoadResult:
    """用 ruamel 加载 yaml；文件不存在时返回空 doc。"""
    path = yaml_path(name)
    if not path.is_file():
        return LoadResult(doc={}, path=path, exists=False)
    y = import_yaml()
    with open(path, "r", encoding="utf-8") as f:
        doc = y.load(f)
    if doc is None:
        doc = {}
    return LoadResult(doc=doc, path=path, exists=True)


def load_text(name: str) -> str:
    """原样读取 yaml 文件文本（用于「原始 YAML」编辑器）。"""
    path = yaml_path(name)
    if not path.is_file():
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 路径操作
# ---------------------------------------------------------------------------

def split_path(path: str) -> List[str]:
    return [p for p in path.split(".") if p]


def get_by_path(doc: Any, path: str, default: Any = None) -> Any:
    node = doc
    for key in split_path(path):
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return default
    return node


def set_by_path(doc: Any, path: str, value: Any) -> None:
    """按点号路径写入。缺少的中间节点会自动创建为 dict。"""
    keys = split_path(path)
    if not keys:
        raise ValueError("set_by_path: 空路径")
    node = doc
    for key in keys[:-1]:
        if not isinstance(node, dict):
            raise TypeError(f"无法在非 dict 节点 {type(node).__name__} 上创建子键 {key!r}")
        if key not in node or not isinstance(node[key], dict):
            node[key] = {}
        node = node[key]
    if not isinstance(node, dict):
        raise TypeError(f"路径 {path!r} 的父节点不是 dict，无法赋值")
    node[keys[-1]] = value


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, render: "callable") -> None:
    """把 ``render(buf)`` 的结果写到 path，使用临时文件 + 重命名。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            render(f)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    try:
        shutil.copy2(path, bak)
        return bak
    except OSError:
        return None


# yaml 文件名 → 配置中心 namespace；``basic_settings.yaml`` 因自举循环不迁库，
# 不登记；其它 5 个（tool / prompt / kb / model / 新的 3 个 store 走独立路径）
# 都在此。登记后 ``save_updates`` / ``save_raw_text`` 自动 mirror；使用
# ``_atomic_write`` 的旁路路径（如 model_config）需要显式调 ``mirror_namespace_to_db``。
_YAML_TO_NAMESPACE: Dict[str, str] = {
    "tool_settings.yaml": "tool_settings",
    "prompt_settings.yaml": "prompt_settings",
    "kb_settings.yaml": "kb_settings",
    "model_settings.yaml": "model_settings",
}


def _config_center_namespace_for(name: str) -> str:
    return _YAML_TO_NAMESPACE.get(name, "")


def _config_center_disabled() -> bool:
    return os.environ.get("CHAYUAN_CONFIG_CENTER_DISABLED", "").strip() in (
        "1", "true", "yes", "on",
    )


def mirror_namespace_to_db(name: str, doc: Any) -> None:
    """公开入口：把某 yaml 的全量 doc 同步到配置中心（每个顶层 key 一条 entry）。

    调用方：绕过 ``save_updates`` / ``save_raw_text`` 直接写 yaml 的地方（如
    ``model_config.py`` 里定制化的 dump），写完 yaml 后调一次即可。
    """
    if isinstance(doc, dict) and doc:
        _mirror_to_config_center(name, doc, list(doc.keys()))


def _mirror_to_config_center(
    name: str, doc: Any, changed_paths: List[str],
) -> None:
    """在 yaml 写入完成后，把该 namespace 全量同步到配置中心。

    - 只对 ``_YAML_TO_NAMESPACE`` 登记过的 yaml 生效；
    - doc 的顶层 key 逐个 ``ConfigStore.set``（值是嵌套 dict / list / scalar 都支持）；
    - 失败只打 warning，不影响 yaml 落盘。
    """
    if _config_center_disabled():
        return
    namespace = _config_center_namespace_for(name)
    if not namespace:
        return
    try:
        from chayuan.server.config_center import get_store
    except Exception:  # noqa: BLE001
        return

    if not isinstance(doc, dict):
        return

    store = get_store()
    comment = f"yaml save: {', '.join(changed_paths[:5])}" if changed_paths else "yaml save"
    # 只同步受影响的顶层 key，避免把未变的 key 全部 bump version
    top_keys = set()
    for p in changed_paths:
        top_keys.add(p.split(".", 1)[0] if "." in p else p)
    if not top_keys:
        # 没有 path（全量写入场景）→ 同步全部顶层
        top_keys = set(doc.keys())

    import logging as _logging
    _log = _logging.getLogger("chayuan.yaml_store")
    for k in top_keys:
        if k not in doc:
            continue
        try:
            store.set(namespace, str(k), doc[k],
                      updated_by="yaml_store", comment=comment)
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "config_center 写入失败（yaml 仍然落盘）ns=%s key=%s err=%r",
                namespace, k, e,
            )


def save_updates(name: str, updates: Dict[str, Any]) -> Tuple[Path, Path | None, Dict[str, Tuple[Any, Any]]]:
    """把 ``{path: value}`` 的一组更新写回 yaml 文件，返回 (文件路径, 备份路径, 变更明细)。

    - 使用 ruamel 读回 doc，按 path 写入，再 dump 回；注释与结构保留；
    - 变更明细形如 ``{"API_SERVER.port": (old, new)}``，仅包含真正发生变化的字段；
    - 对登记过的 yaml 文件（tool_settings / prompt_settings），额外把新值同步写
      到 config center（DB + Redis + Pub/Sub）。
    """
    result = load_yaml(name)
    doc = result.doc if result.exists else {}

    changes: Dict[str, Tuple[Any, Any]] = {}
    for path, new_val in updates.items():
        old = get_by_path(doc, path, default=None)
        if _yaml_equal(old, new_val):
            continue
        set_by_path(doc, path, new_val)
        changes[path] = (old, new_val)

    if not changes:
        return result.path, None, changes

    bak = _backup(result.path)
    y = import_yaml()
    _atomic_write(result.path, lambda f: y.dump(doc, f))

    # 同步 config center（失败不影响 yaml 落盘）
    _mirror_to_config_center(name, doc, list(changes.keys()))

    return result.path, bak, changes


def save_raw_text(name: str, text: str) -> Tuple[Path, Path | None]:
    """覆盖式写入 yaml 原文。调用方应先用 ``validate_text`` 校验。"""
    path = yaml_path(name)
    bak = _backup(path)
    _atomic_write(path, lambda f: f.write(text if text.endswith("\n") else text + "\n"))

    # 对登记过的 yaml，解析一次 raw text 再全量 mirror 到 config center
    namespace = _config_center_namespace_for(name)
    if namespace and not _config_center_disabled():
        try:
            parsed = import_yaml().load(text) or {}
            if isinstance(parsed, dict):
                _mirror_to_config_center(name, parsed, list(parsed.keys()))
        except Exception:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger("chayuan.yaml_store").warning(
                "raw yaml 解析失败，跳过 config center 同步：%s", name,
            )
    return path, bak


def validate_text(text: str) -> Tuple[bool, str]:
    """尝试解析 yaml 文本，返回 ``(ok, error_message)``。"""
    if text is None:
        return False, "空文本"
    try:
        import_yaml().load(text or "")
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _yaml_equal(a: Any, b: Any) -> bool:
    """把 ruamel 的 scalar 与 python 原生值都归一化再比较。"""
    try:
        return _normalize(a) == _normalize(b)
    except Exception:
        return a == b


def _normalize(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, str)):
        return v
    if isinstance(v, dict):
        return {str(k): _normalize(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_normalize(x) for x in v]
    return str(v)


def coerce_for_widget(widget: str, raw: Any) -> Any:
    """把 UI 里拿到的值转换为写回 yaml 时合适的 Python 值。"""
    if widget == "switch":
        return bool(raw)
    if widget == "number":
        if raw in (None, ""):
            return None
        try:
            f = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"无法解析为数字：{raw!r}")
        if f.is_integer() and "." not in str(raw):
            return int(f)
        return f
    if widget in ("text", "password", "textarea", "select"):
        if raw is None:
            return ""
        return str(raw)
    return raw
