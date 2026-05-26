"""模态任务持久化 + 编排。

公开符号:
  - ``manager``      高级 API:create_task / ensure_running / subscribe / cancel
  - ``store``        DB CRUD(给管理面板 / 重启恢复 / e2e 测试用)
  - ``event_bus``    底层事件总线(测试用;生产代码走 manager)

进程内 singleton — 不能在多 worker 部署下用(buffer 是本进程内存)。
单机桌面够用;上云后改 Redis pub/sub 替换 event_bus 即可,store 层不动。
"""

from chayuan.server.modality.router.tasks import event_bus, manager, store

__all__ = ["event_bus", "manager", "store"]
