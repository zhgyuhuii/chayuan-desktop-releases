"""本机可用服务 → 项目配置点 的业务映射目录。

定位
----
``local_service_detector`` 只告诉你"本机跑着 PG/Redis/MinIO/..."；真正把它们
**接进项目**还需要知道：
- 这个服务在项目里能用作什么（业务库？向量库？缓存？文件存储？）
- 要写哪个 yaml 的哪个字段
- 当前该字段是空的还是已经指向了别的地方
- 写完之后点哪里补密码 / 测连接 / 保存

本模块把这些"业务侧知识"单独抽出来，和探测逻辑解耦。UI 层（``service_page``）
只负责把目录里的条目渲染成按钮；具体"去哪配置"的业务判断留在这里。

Binding 的设计契约
------------------
每个 ``Binding`` 是"某个本机服务 → 项目某个配置点"的**潜在绑定**。三个核心动作：

- ``preview()``：返回当前该配置点在 yaml 里的状态（是否已配、指向哪里），供 UI
  展示"配还是不配、会不会覆盖现有"；
- ``prefill()``：把 DetectedService 的 host/port（只写连接端点，不写密码）
  写入对应 yaml；之所以这步独立，是为了让随后打开的 ``render_*_card``
  能**从 yaml 读回刚填的值**，避免给每个 render 函数都加一个 ``initial_values``
  参数；
- ``open_dialog(ui)``：打开对应的配置卡 dialog，复用已有 ``render_*_card``，
  让用户补齐凭据/做测试/保存。

PostgreSQL 特殊处理
------------------
PG 在项目里有两个用途：
1. 业务数据库（``SQLALCHEMY_DATABASE_URI``）
2. 知识库向量库（``kbs_config.pg.connection_uri``，需要 pgvector 扩展）

所以 ``_postgresql_service_type()`` 返回两个 bindings；UI 会弹"选一个用途"
对话框，其它单用途服务则直接打开对应 dialog。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from chayuan.server.config_panel import yaml_store
from chayuan.server.config_panel.local_service_detector import DetectedService

logger = logging.getLogger("chayuan.config_panel.local_service_catalog")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class BindingPreview:
    """某个配置点当前状态的"预览"，供 UI 判断是否会覆盖。"""

    configured: bool                  # yaml 里是否已经填过
    current_summary: str              # 一行描述当前值（脱敏）
    same_as_detected: bool = False    # 当前 yaml 里的值和探到的端点是否同一个
    warning: str = ""                 # 提示语（如"覆盖会丢失现有凭据"）


@dataclass
class Binding:
    """一个"本机服务 → 项目配置点"的候选绑定。"""

    target_id: str                    # 稳定 id："db_main" / "redis" / "kb_vs_pg" / "minio" / "kb_vs_milvus" / "kb_vs_es"
    label: str                        # 中文标题："业务数据库"
    blurb: str                        # 一句话说用途
    config_file: str                  # yaml 文件名（展示用）

    preview: Callable[[DetectedService], BindingPreview] = field(repr=False)
    prefill: Callable[[DetectedService], List[str]] = field(repr=False)
    open_dialog: Callable[[Any], None] = field(repr=False)  # (ui) -> None


@dataclass
class ServiceTypeEntry:
    """一种"可被检测 + 可被绑定"的服务类型。"""

    kind: str                         # "redis" / "postgresql" / ...
    icon: str                         # material icon
    bindings: List[Binding]           # 该服务可绑定的项目配置点（可能 1 或多）


# ---------------------------------------------------------------------------
# Redis：单绑定 → REDIS_URL
# ---------------------------------------------------------------------------

def _redis_service_type() -> ServiceTypeEntry:
    from urllib.parse import urlparse

    def _preview(det: DetectedService) -> BindingPreview:
        doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
        url = str(doc.get("REDIS_URL") or "").strip()
        if not url:
            return BindingPreview(configured=False, current_summary="（未配置）")
        try:
            p = urlparse(url)
            same = (
                (p.hostname or "") in ("127.0.0.1", "localhost", "0.0.0.0", det.host)
                and int(p.port or 6379) == det.port
            )
            host = p.hostname or "?"
            return BindingPreview(
                configured=True,
                current_summary=f"redis://{host}:{p.port or 6379}/{(p.path or '/0').lstrip('/')}",
                same_as_detected=same,
                warning="" if same else "当前 REDIS_URL 指向其它实例，覆盖会用本机实例替换它",
            )
        except Exception:  # noqa: BLE001
            return BindingPreview(
                configured=True, current_summary=url[:60],
                warning="无法解析现有 URL，建议在 dialog 里核对一下",
            )

    def _prefill(det: DetectedService) -> List[str]:
        # 保留已有的 db/username/password（如果有），只改 host/port
        doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
        old = str(doc.get("REDIS_URL") or "").strip()
        try:
            from chayuan.server.config_panel.redis_config import parse_url, build_url
            v = parse_url(old) if old else None
            if v is None:
                from chayuan.server.config_panel.redis_config import RedisFormValues
                v = RedisFormValues()
            v.host = det.host
            v.port = str(det.port)
            new_url = build_url(v)
        except Exception as e:  # noqa: BLE001
            logger.debug("redis prefill fallback due to %s", e)
            new_url = f"redis://{det.host}:{det.port}/0"
        _path, _bak, changes = yaml_store.save_updates(
            "basic_settings.yaml", {"REDIS_URL": new_url},
        )
        return list(changes.keys())

    def _open(ui) -> None:
        from chayuan.server.config_panel.redis_config import render_redis_card
        render_redis_card(ui)

    return ServiceTypeEntry(
        kind="redis",
        icon="memory",
        bindings=[
            Binding(
                target_id="redis",
                label="Redis（缓存 / 限流 / 异步队列）",
                blurb=(
                    "项目的 REDIS_URL —— 限流、语义缓存、ingest 异步队列、WebSocket "
                    "Hub 都会读它。未配置时相关能力降级为单机内存实现。"
                ),
                config_file="basic_settings.yaml",
                preview=_preview,
                prefill=_prefill,
                open_dialog=_open,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# PostgreSQL：双绑定 → 业务库 / 知识库向量库
# ---------------------------------------------------------------------------

def _postgresql_service_type() -> ServiceTypeEntry:
    from urllib.parse import urlparse

    # --- 绑定 1：业务数据库 --------------------------------------------------

    def _db_preview(det: DetectedService) -> BindingPreview:
        doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
        uri = str(doc.get("SQLALCHEMY_DATABASE_URI") or "").strip()
        if not uri:
            return BindingPreview(configured=False, current_summary="（未配置）")
        if uri.startswith("sqlite"):
            tail = uri.split("://", 1)[-1].lstrip("/")
            return BindingPreview(
                configured=True,
                current_summary=f"SQLite · {tail.rsplit('/', 1)[-1]}",
                warning="当前是 SQLite（本地文件），覆盖后将切到 Postgres；需填写 user/password",
            )
        try:
            u = urlparse(uri)
            backend = (u.scheme or "").split("+", 1)[0]
            if backend in ("postgresql", "postgres"):
                same = (
                    (u.hostname or "") in ("127.0.0.1", "localhost", det.host)
                    and int(u.port or 5432) == det.port
                )
                return BindingPreview(
                    configured=True,
                    current_summary=f"{backend} · {u.hostname}:{u.port or 5432}/{(u.path or '').lstrip('/')}",
                    same_as_detected=same,
                    warning="" if same else "当前指向其它 PG 实例，覆盖会更换 host/port（保留 database/user）",
                )
            return BindingPreview(
                configured=True,
                current_summary=f"{backend} · {u.hostname}:{u.port}",
                warning=f"当前是 {backend}，覆盖会切到 Postgres（需重新填 user/password）",
            )
        except Exception:  # noqa: BLE001
            return BindingPreview(configured=True, current_summary=uri[:60])

    def _db_prefill(det: DetectedService) -> List[str]:
        # 保留 database/user/password/schema（如已有 PG），只覆盖 host/port
        from chayuan.server.config_panel.db_config import (
            parse_uri, build_uri, db_type_by_key, defaults_for, DbFormValues,
        )
        doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
        old = str(doc.get("SQLALCHEMY_DATABASE_URI") or "").strip()
        if old:
            v: DbFormValues = parse_uri(old)
        else:
            pg = db_type_by_key("postgresql")
            v = defaults_for(pg) if pg else DbFormValues(db_type="postgresql")
        # 切到 postgres；保留 database/user/password/schema 字段
        if v.db_type != "postgresql":
            v.db_type = "postgresql"
            # 其它方言切过来，schema/charset 字段应该重置
            v.schema = "public"
            v.charset = ""
            if not v.database:
                v.database = "chayuan"
        v.host = det.host
        v.port = str(det.port)
        new_uri = build_uri(v)
        _path, _bak, changes = yaml_store.save_updates(
            "basic_settings.yaml", {"SQLALCHEMY_DATABASE_URI": new_uri},
        )
        return list(changes.keys())

    def _db_open(ui) -> None:
        from chayuan.server.config_panel.db_config import render_main_db_card
        render_main_db_card(ui)

    binding_db = Binding(
        target_id="db_main",
        label="业务数据库（Postgres）",
        blurb=(
            "SQLALCHEMY_DATABASE_URI —— 聊天记录、知识库元数据、用户/ACL 全都写这里。"
            "生产强烈推荐 Postgres（vs SQLite 不锁写）。"
        ),
        config_file="basic_settings.yaml",
        preview=_db_preview,
        prefill=_db_prefill,
        open_dialog=_db_open,
    )

    # --- 绑定 2：知识库向量库（pg + pgvector）---------------------------------

    def _vs_pg_preview(det: DetectedService) -> BindingPreview:
        doc = yaml_store.load_yaml("kb_settings.yaml").doc or {}
        default_vs = str(doc.get("DEFAULT_VS_TYPE") or "").lower()
        kbs_cfg = (doc.get("kbs_config") or {}) or {}
        pg_cfg = kbs_cfg.get("pg") or {}
        uri = str(pg_cfg.get("connection_uri") or "").strip()
        if not uri:
            msg = f"（kbs_config.pg.connection_uri 未配置；DEFAULT_VS_TYPE={default_vs or '未选'}）"
            return BindingPreview(configured=False, current_summary=msg)
        try:
            u = urlparse(uri)
            same = (
                (u.hostname or "") in ("127.0.0.1", "localhost", det.host)
                and int(u.port or 5432) == det.port
            )
            return BindingPreview(
                configured=True,
                current_summary=f"pg · {u.hostname}:{u.port or 5432}/{(u.path or '').lstrip('/')}",
                same_as_detected=same,
                warning=(
                    "" if same else
                    "kbs_config.pg 已指向其它 PG，覆盖会更换 host/port（保留 user/db）。"
                    "切换后记得把 DEFAULT_VS_TYPE 改成 pg 才会真正生效。"
                ),
            )
        except Exception:  # noqa: BLE001
            return BindingPreview(configured=True, current_summary=uri[:60])

    def _vs_pg_prefill(det: DetectedService) -> List[str]:
        from urllib.parse import urlparse as _up, quote as _q
        doc = yaml_store.load_yaml("kb_settings.yaml").doc or {}
        kbs_cfg = (doc.get("kbs_config") or {}) or {}
        pg_cfg = kbs_cfg.get("pg") or {}
        old = str(pg_cfg.get("connection_uri") or "").strip()

        user = "postgres"
        password = ""
        database = "chayuan_vs"
        if old:
            try:
                u = _up(old)
                user = u.username or user
                password = u.password or ""
                database = (u.path or "").lstrip("/") or database
            except Exception:  # noqa: BLE001
                pass

        userpart = _q(user, safe="")
        if password:
            userpart += ":" + _q(password, safe="")
        new_uri = f"postgresql://{userpart}@{det.host}:{det.port}/{database}"
        # 只改 kbs_config.pg.connection_uri，不自动改 DEFAULT_VS_TYPE（破坏性大）
        _path, _bak, changes = yaml_store.save_updates(
            "kb_settings.yaml", {"kbs_config.pg.connection_uri": new_uri},
        )
        return list(changes.keys())

    def _vs_pg_open(ui) -> None:
        from chayuan.server.config_panel.vs_config import render_vs_card
        render_vs_card(ui)

    binding_vs_pg = Binding(
        target_id="kb_vs_pg",
        label="知识库向量库（pgvector）",
        blurb=(
            "kbs_config.pg —— 用 PG 的 pgvector 扩展存知识库嵌入。"
            "需要服务端安装 pgvector 扩展；保存后还要在知识库配置里把 "
            "DEFAULT_VS_TYPE 改成 pg 才会启用。"
        ),
        config_file="kb_settings.yaml",
        preview=_vs_pg_preview,
        prefill=_vs_pg_prefill,
        open_dialog=_vs_pg_open,
    )

    return ServiceTypeEntry(
        kind="postgresql",
        icon="storage",
        bindings=[binding_db, binding_vs_pg],
    )


# ---------------------------------------------------------------------------
# MySQL：单绑定 → 业务库
# ---------------------------------------------------------------------------

def _mysql_service_type() -> ServiceTypeEntry:
    from urllib.parse import urlparse

    def _preview(det: DetectedService) -> BindingPreview:
        doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
        uri = str(doc.get("SQLALCHEMY_DATABASE_URI") or "").strip()
        if not uri:
            return BindingPreview(configured=False, current_summary="（未配置）")
        if uri.startswith("sqlite"):
            return BindingPreview(
                configured=True,
                current_summary=f"SQLite · {uri.split('/')[-1]}",
                warning="当前是 SQLite，覆盖会切到 MySQL（需重填 user/password）",
            )
        try:
            u = urlparse(uri)
            backend = (u.scheme or "").split("+", 1)[0]
            if backend in ("mysql", "mariadb"):
                same = (
                    (u.hostname or "") in ("127.0.0.1", "localhost", det.host)
                    and int(u.port or 3306) == det.port
                )
                return BindingPreview(
                    configured=True,
                    current_summary=f"{backend} · {u.hostname}:{u.port or 3306}/{(u.path or '').lstrip('/')}",
                    same_as_detected=same,
                )
            return BindingPreview(
                configured=True,
                current_summary=f"{backend} · {u.hostname}:{u.port}",
                warning=f"当前是 {backend}，覆盖会切到 MySQL（需重填凭据）",
            )
        except Exception:  # noqa: BLE001
            return BindingPreview(configured=True, current_summary=uri[:60])

    def _prefill(det: DetectedService) -> List[str]:
        from chayuan.server.config_panel.db_config import (
            parse_uri, build_uri, db_type_by_key, defaults_for, DbFormValues,
        )
        doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
        old = str(doc.get("SQLALCHEMY_DATABASE_URI") or "").strip()
        if old:
            v: DbFormValues = parse_uri(old)
        else:
            my = db_type_by_key("mysql")
            v = defaults_for(my) if my else DbFormValues(db_type="mysql")
        if v.db_type != "mysql":
            v.db_type = "mysql"
            v.schema = ""
            v.charset = "utf8mb4"
            if not v.database:
                v.database = "chayuan"
        v.host = det.host
        v.port = str(det.port)
        new_uri = build_uri(v)
        _path, _bak, changes = yaml_store.save_updates(
            "basic_settings.yaml", {"SQLALCHEMY_DATABASE_URI": new_uri},
        )
        return list(changes.keys())

    def _open(ui) -> None:
        from chayuan.server.config_panel.db_config import render_main_db_card
        render_main_db_card(ui)

    return ServiceTypeEntry(
        kind="mysql",
        icon="storage",
        bindings=[Binding(
            target_id="db_main",
            label="业务数据库（MySQL / MariaDB）",
            blurb=(
                "SQLALCHEMY_DATABASE_URI —— 业务元数据主库。"
                "MySQL 生态完善、单机性能足够；和 Postgres 二选一即可。"
            ),
            config_file="basic_settings.yaml",
            preview=_preview,
            prefill=_prefill,
            open_dialog=_open,
        )],
    )


# ---------------------------------------------------------------------------
# Milvus：单绑定 → 知识库向量库
# ---------------------------------------------------------------------------

def _milvus_service_type() -> ServiceTypeEntry:

    def _preview(det: DetectedService) -> BindingPreview:
        doc = yaml_store.load_yaml("kb_settings.yaml").doc or {}
        default_vs = str(doc.get("DEFAULT_VS_TYPE") or "").lower()
        kbs_cfg = (doc.get("kbs_config") or {}) or {}
        mv = kbs_cfg.get("milvus") or {}
        host = str(mv.get("host") or "").strip()
        port = str(mv.get("port") or "").strip()
        if not host:
            return BindingPreview(
                configured=False,
                current_summary=f"（未配置；DEFAULT_VS_TYPE={default_vs or '未选'}）",
            )
        same = host in ("127.0.0.1", "localhost", det.host) and str(det.port) == port
        return BindingPreview(
            configured=True,
            current_summary=f"milvus · {host}:{port or '19530'}"
                            + (f" · 默认 VS={default_vs}" if default_vs else ""),
            same_as_detected=same,
            warning=(
                "" if same else
                "kbs_config.milvus 已指向其它实例，覆盖会换 host/port（保留 user/token）。"
                "切换后记得把 DEFAULT_VS_TYPE 改成 milvus 才会生效。"
            ),
        )

    def _prefill(det: DetectedService) -> List[str]:
        # Milvus 2.6 后 langchain-milvus 走 MilvusClient(uri=...)。把探测到的 host/port
        # 直接合成 uri 写入（兼容 _normalize_connection_args 的 host/port 兜底，但 yaml
        # 里以 uri 为准更清晰）。
        updates = {
            "kbs_config.milvus.uri": f"http://{det.host}:{det.port}",
        }
        _path, _bak, changes = yaml_store.save_updates("kb_settings.yaml", updates)
        return list(changes.keys())

    def _open(ui) -> None:
        from chayuan.server.config_panel.vs_config import render_vs_card
        render_vs_card(ui)

    return ServiceTypeEntry(
        kind="milvus",
        icon="scatter_plot",
        bindings=[Binding(
            target_id="kb_vs_milvus",
            label="知识库向量库（Milvus）",
            blurb=(
                "kbs_config.milvus —— 高性能向量检索的首选。保存 host/port 后，"
                "还需要在知识库配置里把 DEFAULT_VS_TYPE 改成 milvus 才会启用。"
            ),
            config_file="kb_settings.yaml",
            preview=_preview,
            prefill=_prefill,
            open_dialog=_open,
        )],
    )


# ---------------------------------------------------------------------------
# MinIO：单绑定 → 文件存储
# ---------------------------------------------------------------------------

def _minio_service_type() -> ServiceTypeEntry:

    def _preview(det: DetectedService) -> BindingPreview:
        doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
        backend = str(doc.get("FILE_STORAGE_BACKEND") or "local").strip().lower()
        ep = str(doc.get("MINIO_ENDPOINT") or "").strip()
        if backend != "minio":
            return BindingPreview(
                configured=(backend != "local"),
                current_summary=f"当前后端：{backend}" + (f"（endpoint={ep}）" if ep else ""),
                warning=(
                    "当前文件存储后端是 local（本机磁盘）；切到 MinIO 后，"
                    "知识库 / 图像 / 临时文件都会写到对象存储里。"
                ),
            )
        if not ep:
            return BindingPreview(
                configured=False,
                current_summary="已选 MinIO 后端但 endpoint 为空",
                warning="dialog 里把 endpoint / access key / secret key 填齐即可",
            )
        same = f"{det.host}:{det.port}" in ep or ep.endswith(f":{det.port}")
        return BindingPreview(
            configured=True,
            current_summary=f"minio · {ep}",
            same_as_detected=same,
            warning="" if same else "覆盖会把 endpoint 改到本机实例（保留 access/secret key）",
        )

    def _prefill(det: DetectedService) -> List[str]:
        updates = {
            "FILE_STORAGE_BACKEND": "minio",
            "MINIO_ENDPOINT": f"{det.host}:{det.port}",
        }
        _path, _bak, changes = yaml_store.save_updates("basic_settings.yaml", updates)
        return list(changes.keys())

    def _open(ui) -> None:
        from chayuan.server.config_panel.storage_config import render_storage_card
        render_storage_card(ui, show_title=False)

    return ServiceTypeEntry(
        kind="minio",
        icon="inventory_2",
        bindings=[Binding(
            target_id="minio",
            label="文件存储（MinIO / S3 兼容）",
            blurb=(
                "FILE_STORAGE_BACKEND=minio —— 把知识库附件、图像、临时文件"
                "统一落到对象存储里，支持预签名 URL、跨副本共享。"
            ),
            config_file="basic_settings.yaml",
            preview=_preview,
            prefill=_prefill,
            open_dialog=_open,
        )],
    )


# ---------------------------------------------------------------------------
# Elasticsearch：单绑定 → 知识库向量库 / 检索
# ---------------------------------------------------------------------------

def _elasticsearch_service_type() -> ServiceTypeEntry:

    def _preview(det: DetectedService) -> BindingPreview:
        doc = yaml_store.load_yaml("kb_settings.yaml").doc or {}
        default_vs = str(doc.get("DEFAULT_VS_TYPE") or "").lower()
        kbs_cfg = (doc.get("kbs_config") or {}) or {}
        es = kbs_cfg.get("es") or {}
        host = str(es.get("host") or "").strip()
        port = str(es.get("port") or "").strip()
        if not host:
            return BindingPreview(
                configured=False,
                current_summary=f"（未配置；DEFAULT_VS_TYPE={default_vs or '未选'}）",
            )
        same = host in ("127.0.0.1", "localhost", det.host) and str(det.port) == port
        return BindingPreview(
            configured=True,
            current_summary=f"es · {host}:{port or '9200'}",
            same_as_detected=same,
            warning=(
                "" if same else
                "kbs_config.es 已指向其它实例，覆盖会换 host/port。"
                "切换后记得把 DEFAULT_VS_TYPE 改成 es 才会生效。"
            ),
        )

    def _prefill(det: DetectedService) -> List[str]:
        updates = {
            "kbs_config.es.host": det.host,
            "kbs_config.es.port": str(det.port),
            # 本机默认 http（ES 8.x 如果开了 https 用户在 dialog 里切）
            "kbs_config.es.scheme": "http",
        }
        _path, _bak, changes = yaml_store.save_updates("kb_settings.yaml", updates)
        return list(changes.keys())

    def _open(ui) -> None:
        from chayuan.server.config_panel.vs_config import render_vs_card
        render_vs_card(ui)

    return ServiceTypeEntry(
        kind="elasticsearch",
        icon="travel_explore",
        bindings=[Binding(
            target_id="kb_vs_es",
            label="知识库向量库（Elasticsearch）",
            blurb=(
                "kbs_config.es —— ES 8.x 同时承担向量检索和倒排索引；"
                "保存后记得把 DEFAULT_VS_TYPE 改成 es 才会启用。"
            ),
            config_file="kb_settings.yaml",
            preview=_preview,
            prefill=_prefill,
            open_dialog=_open,
        )],
    )


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_BUILDERS: Dict[str, Callable[[], ServiceTypeEntry]] = {
    "redis": _redis_service_type,
    "postgresql": _postgresql_service_type,
    "mysql": _mysql_service_type,
    "milvus": _milvus_service_type,
    "minio": _minio_service_type,
    "elasticsearch": _elasticsearch_service_type,
}


def get_service_type(kind: str) -> Optional[ServiceTypeEntry]:
    """按探测到的 kind 取服务目录条目；未知 kind 返回 None（UI 会画一个"仅展示"卡）。"""
    builder = _BUILDERS.get(kind)
    if builder is None:
        return None
    try:
        return builder()
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to build service type entry for kind=%s: %s", kind, e)
        return None


def all_known_kinds() -> List[str]:
    return list(_BUILDERS.keys())


__all__ = [
    "Binding",
    "BindingPreview",
    "ServiceTypeEntry",
    "get_service_type",
    "all_known_kinds",
]
