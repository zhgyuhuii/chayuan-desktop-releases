"""95-1:文件夹定时同步任务 ORM。

设计要点:
* 用户配 ``folder_path``(任意绝对路径,用户决策 6)、``target`` (集合名 / KB 名 / ku_id)、
  ``interval_seconds``,后台 apscheduler 触发
* 同步状态记录:``last_sync_at`` / ``last_sync_summary``(JSON)
* state.json(mtime/sha1)走文件,不入 DB(避免 BLOB 膨胀)
* 单子失败跳过 — uploader 内部按文件粒度 catch
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, JSON, String, Text, func,
)

from chayuan.server.db.base import Base


class FolderSyncJobModel(Base):
    """文件夹同步任务。"""

    __tablename__ = "folder_sync_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False, comment="任务显示名")
    folder_path = Column(String(1024), nullable=False,
                         comment="本地绝对路径(任意,用户决策 6)")
    target = Column(String(160), nullable=False,
                    comment="目标:``coll:<id>`` / ``doc:<kb_name>`` / ``src:<id>``")
    owner_id = Column(Integer, nullable=False, index=True)
    interval_seconds = Column(Integer, default=300, nullable=False,
                              comment="同步间隔,默认 5 分钟")
    enabled = Column(Boolean, default=True, nullable=False)
    recursive = Column(Boolean, default=True, nullable=False)
    include_globs = Column(JSON, default=list,
                           comment='默认 ["*.pdf","*.docx","*.txt","*.md","*.jpg","*.png","*.webp"]')
    exclude_globs = Column(JSON, default=list,
                           comment='默认 ["~$*",".DS_Store","*.tmp","*.swp"]')
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_summary = Column(JSON, default=dict,
                               comment="{added,modified,removed,errors}")
    create_time = Column(DateTime, server_default=func.now())
    update_time = Column(DateTime, server_default=func.now(),
                         onupdate=func.now())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "folder_path": self.folder_path,
            "target": self.target,
            "owner_id": self.owner_id,
            "interval_seconds": int(self.interval_seconds or 0),
            "enabled": bool(self.enabled),
            "recursive": bool(self.recursive),
            "include_globs": list(self.include_globs or []),
            "exclude_globs": list(self.exclude_globs or []),
            "last_sync_at": (
                self.last_sync_at.isoformat() if self.last_sync_at else None
            ),
            "last_sync_summary": dict(self.last_sync_summary or {}),
            "create_time": (
                self.create_time.isoformat() if self.create_time else None
            ),
        }


# 默认 glob
DEFAULT_INCLUDE_GLOBS = [
    "*.pdf", "*.docx", "*.doc", "*.txt", "*.md", "*.markdown",
    "*.xlsx", "*.xls", "*.csv", "*.html", "*.htm",
    "*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.bmp", "*.tiff",
]
DEFAULT_EXCLUDE_GLOBS = [
    "~$*", ".DS_Store", "*.tmp", "*.swp", "*.crdownload", "*.part",
]
