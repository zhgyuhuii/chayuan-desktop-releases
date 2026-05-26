"""ORM models. Schema is intentionally rich enough that runtime / gateway /
discovery never have to consult the filesystem to make a decision.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ModelStatus(str, enum.Enum):
    READY = "ready"        # files complete, can be loaded
    COLD = "cold"          # registered, not yet warm-loaded
    DOWNLOADING = "downloading"
    BROKEN = "broken"      # checksum mismatch / missing files
    REMOVED = "removed"    # soft-deleted (files vanished)


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (
        UniqueConstraint("repo", "category", name="uq_model_repo_category"),
        Index("ix_model_status", "status"),
        Index("ix_model_category", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repo: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    format: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    quantization: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[ModelStatus] = mapped_column(
        Enum(ModelStatus, native_enum=False), nullable=False, default=ModelStatus.COLD
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered")
    license_id: Mapped[int | None] = mapped_column(ForeignKey("model_licenses.id"), nullable=True)
    file_mtime: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    license: Mapped["ModelLicense | None"] = relationship(back_populates="models", lazy="joined")
    aliases: Mapped[list["ModelAlias"]] = relationship(
        back_populates="model", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def public_id(self) -> str:
        """Stable id used over the wire (matches OpenAI `model` field)."""
        return self.repo

    def to_public(self) -> dict[str, Any]:
        # 把"文件落在哪儿"展示给前端：
        #   * ``path`` —— 主权重 / manifest 路径（DB 里直接存的）
        #   * ``dir``  —— 父目录，前端"打开文件夹"按钮用
        #   * ``exists`` —— 简单 stat，落盘后被人手删掉时前端能立刻发现
        #
        # path 暴露给非 admin 也是安全的：filesystem path 不含密钥，且 chayuan
        # 部署在用户自己机器上；出于审计需要保留 sha256 / size_bytes。
        from pathlib import Path as _P
        try:
            p = _P(self.path) if self.path else None
            exists = bool(p and p.exists())
            parent = str(p.parent) if p else None
        except Exception:
            exists = False
            parent = None

        return {
            "id": self.public_id,
            "object": "model",
            "owned_by": "chayuan",
            "category": self.category,
            "runtime": self.runtime,
            "format": self.format,
            "quantization": self.quantization,
            "status": self.status.value,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256 or None,
            "path": self.path or None,
            "dir": parent,
            "exists": exists,
            "capabilities": self.capabilities,
            "aliases": [a.alias for a in self.aliases],
            "created": int(self.created_at.timestamp()),
        }


class ModelAlias(Base):
    __tablename__ = "model_aliases"
    __table_args__ = (UniqueConstraint("alias", name="uq_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    model: Mapped[Model] = relationship(back_populates="aliases")


class ModelLicense(Base):
    __tablename__ = "model_licenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    spdx: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    text: Mapped[str] = mapped_column(String(8192), nullable=False, default="")
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    models: Mapped[list[Model]] = relationship(back_populates="license")
