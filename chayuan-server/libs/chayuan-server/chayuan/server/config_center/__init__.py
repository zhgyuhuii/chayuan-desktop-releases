"""Chayuan 配置中心（DB + Redis + yaml 三级）。

- **DB** 作为**持久化真实源**（``chayuan_config`` 表），每次保存带版本号 + 写
  ``chayuan_config_history`` 审计；
- **Redis** 作为**二级缓存 + 跨副本一致性总线**：读时优先命中；写时 `SET` + `PUBLISH`
  通知所有副本失效并 reload；
- **yaml** 降级为**种子 + 应急 fallback**：启动时若 DB 对应 namespace 为空，
  把 `CHAYUAN_ROOT/<ns>.yaml` 或 store 自己声明的种子 dict 导入 DB；运行期 DB 不可达时
  只读走 yaml 旧值。

对业务代码而言只需要认识 ``get_store().get(ns, key)`` / ``.set(ns, key, val)``，
加一个本地回调订阅热更新即可；ORM / Redis / yaml 细节对上层透明。
"""
from .store import ConfigStore, get_store  # noqa: F401
from .subscribe import (  # noqa: F401
    register_callback,
    start_background_subscriber,
    stop_background_subscriber,
)
from .bootstrap import ensure_seeded, seed_from_yaml  # noqa: F401
from .yaml_sync import (  # noqa: F401
    make_yaml_sync_callback,
    sync_namespace_to_yaml,
)
