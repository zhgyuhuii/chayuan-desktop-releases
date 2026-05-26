"""DB → yaml 反向同步器。

在 P1/P2 迁移期，我们保留 ``Settings.<section>`` 继续读 yaml 作为运行期源，
但把 yaml 降级成「写入由配置中心托管」的**镜像**：

- 面板写 → ``ConfigStore.set`` + 立刻把该 namespace dump 回 yaml
- 其他副本通过 Redis Pub/Sub 收到 ``chayuan:config:events`` → 回调里
  ``sync_namespace_to_yaml(ns, path)`` → yaml 被覆写 → ``Settings`` 的
  ``set_auto_reload(True)`` 自动吃到新值。

优势：业务代码对 ``Settings.tool_settings`` 等的访问零侵入；Redis/DB 挂了仍能
降级成纯 yaml 运行。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from chayuan.pydantic_settings_file import import_yaml

from .store import get_store


logger = logging.getLogger("chayuan.config_center.yaml_sync")


def sync_namespace_to_yaml(namespace: str, yaml_path: Path) -> bool:
    """把 ``chayuan_config`` 里该 namespace 的全部 key → value 组装成 dict，
    覆写到 ``yaml_path``（原子写）。

    - 顺序按 key asc（ruamel 会保留）；
    - 文件已有注释会因整体覆写而丢失 —— yaml 在迁移后语义是「运行期只读镜像」，
      注释要写就写在代码或文档里，不建议在 yaml 里依赖注释。
    """
    try:
        doc = get_store().get_namespace(namespace)
    except Exception as e:  # noqa: BLE001
        logger.warning("sync_namespace_to_yaml: 读 namespace=%s 失败：%r",
                       namespace, e)
        return False

    # 若 namespace 在 DB 空：不覆写 yaml（避免把老种子文件变成空文件）
    if not doc:
        return False

    try:
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = yaml_path.with_suffix(yaml_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            import_yaml().dump(doc, f)
        import os
        os.replace(tmp, yaml_path)
        logger.info("sync_namespace_to_yaml: wrote %d keys to %s",
                    len(doc), yaml_path)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("sync_namespace_to_yaml: 写 %s 失败：%r", yaml_path, e)
        return False


def make_yaml_sync_callback(namespace: str, yaml_path: Path):
    """工厂方法：给 ``register_callback`` 用的回调，收到变更就做一次 DB→yaml 同步。"""

    def _cb(evt: Dict[str, Any]) -> None:
        # 只对 set/delete 触发（ping 等 op 忽略）
        if evt.get("op") not in ("set", "delete"):
            return
        sync_namespace_to_yaml(namespace, yaml_path)

    return _cb
