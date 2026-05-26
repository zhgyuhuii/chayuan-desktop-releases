"""文件存储子系统测试（LocalStorage 优先；MinIO 仅 mock）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from chayuan.server.file_storage import NS, get_storage, reset_cache
from chayuan.server.file_storage.base import StorageError
from chayuan.server.file_storage.local import LocalStorage, verify_local_signature


# ---------------------------------------------------------------------------
# LocalStorage
# ---------------------------------------------------------------------------

def test_local_storage_put_get_roundtrip(tmp_path):
    s = LocalStorage(root_dir=str(tmp_path))
    meta = s.put(NS.KB_CONTENT, "samples/a.txt", b"hello world")
    assert meta.size == 11
    assert s.exists(NS.KB_CONTENT, "samples/a.txt")
    assert s.get(NS.KB_CONTENT, "samples/a.txt") == b"hello world"


def test_local_storage_list(tmp_path):
    s = LocalStorage(root_dir=str(tmp_path))
    s.put(NS.KB_CONTENT, "k1/a.txt", b"aa")
    s.put(NS.KB_CONTENT, "k1/b.txt", b"bbb")
    s.put(NS.KB_CONTENT, "k2/c.txt", b"cccc")
    all_obj = s.list(NS.KB_CONTENT)
    assert len(all_obj) == 3
    k1_only = s.list(NS.KB_CONTENT, prefix="k1")
    assert len(k1_only) == 2


def test_local_storage_delete(tmp_path):
    s = LocalStorage(root_dir=str(tmp_path))
    s.put(NS.MISC, "x.bin", b"01")
    assert s.delete(NS.MISC, "x.bin") is True
    assert s.delete(NS.MISC, "nonexistent") is False


def test_local_storage_stat(tmp_path):
    s = LocalStorage(root_dir=str(tmp_path))
    s.put(NS.KB_CONTENT, "kb1/file.bin", b"12345")
    st = s.stat(NS.KB_CONTENT, "kb1/file.bin")
    assert st is not None and st.size == 5
    assert s.stat(NS.KB_CONTENT, "nonexistent") is None


def test_local_storage_rejects_path_traversal(tmp_path):
    s = LocalStorage(root_dir=str(tmp_path))
    # ../ 被过滤
    s.put(NS.KB_CONTENT, "../../etc/passwd", b"x")
    # 文件实际落在 namespace 下的 "etc/passwd"
    assert s.exists(NS.KB_CONTENT, "etc/passwd")
    # 没跑出 tmp_path
    assert not (Path(tmp_path).parent / "etc").exists() or \
           not (Path(tmp_path).parent / "etc" / "passwd").exists()


def test_local_presigned_url_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_OVERRIDE", "test-secret")
    s = LocalStorage(root_dir=str(tmp_path))
    s.put(NS.KB_CONTENT, "k/p.txt", b"hi")
    url = s.presigned_url(NS.KB_CONTENT, "k/p.txt", expires_sec=120)
    assert url.startswith("/storage/stream?")
    # 解析 token / exp 验签
    import urllib.parse
    q = urllib.parse.parse_qs(url.split("?", 1)[1])
    assert verify_local_signature(
        q["ns"][0], q["key"][0], int(q["exp"][0]), q["token"][0],
    ) is True
    # 篡改 token 应失败
    assert verify_local_signature(
        q["ns"][0], q["key"][0], int(q["exp"][0]), "badtoken",
    ) is False


def test_local_storage_open_read_stream(tmp_path):
    s = LocalStorage(root_dir=str(tmp_path))
    s.put(NS.MISC, "stream.bin", b"0123456789" * 100)
    reader = s.open_read(NS.MISC, "stream.bin")
    with reader as r:
        chunks = []
        while True:
            c = r.read(128)
            if not c:
                break
            chunks.append(c)
    assert b"".join(chunks) == b"0123456789" * 100


def test_local_storage_backend_info(tmp_path):
    s = LocalStorage(root_dir=str(tmp_path))
    s.put(NS.KB_CONTENT, "a/b.txt", b"hi")
    info = s.backend_info()
    assert info["type"] == "local"
    assert info["healthy"] is True
    assert info["namespaces"][NS.KB_CONTENT]["objects"] == 1


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def test_factory_default_local(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    reset_cache()
    from chayuan.settings import Settings
    monkeypatch.setattr(Settings.basic_settings, "FILE_STORAGE_BACKEND", "local",
                         raising=False)
    monkeypatch.setattr(Settings.basic_settings, "FILE_STORAGE_LOCAL_ROOT",
                         str(tmp_path / "store"), raising=False)
    st = get_storage()
    assert st.name == "local"
    reset_cache()


def test_factory_minio_missing_pkg_falls_back(monkeypatch, tmp_path):
    """配成 minio 但 minio 包未装 / 凭据无效 → 自动回退 local。"""
    monkeypatch.setenv("CHAYUAN_ROOT", str(tmp_path))
    reset_cache()
    from chayuan.settings import Settings
    monkeypatch.setattr(Settings.basic_settings, "FILE_STORAGE_BACKEND", "minio",
                         raising=False)
    # 无 endpoint → MinIO 初始化失败 → factory 降级
    monkeypatch.setattr(Settings.basic_settings, "MINIO_ENDPOINT", "",
                         raising=False)
    st = get_storage()
    assert st.name == "local"
    reset_cache()


# ---------------------------------------------------------------------------
# 导出 + 命名空间常量
# ---------------------------------------------------------------------------

def test_ns_constants():
    assert NS.KB_CONTENT == "kb_content"
    assert NS.CHAT_TEMP == "chat_temp"
    assert NS.IMAGE_FILES == "image_files"
    assert NS.MISC == "misc"
    assert set(NS.all()) == {"kb_content", "chat_temp", "image_files", "misc"}


# ---------------------------------------------------------------------------
# per-KB storage_backend: repository + factory.get_storage_for_kb
# ---------------------------------------------------------------------------

def _ensure_kb_table(engine):
    """``ks_db`` 只跑 migrations；``knowledge_base`` 表由 ``create_tables()`` 建，
    这里补一下（测试独立 SQLite，无开销）。"""
    from chayuan.server.db.models.knowledge_base_model import KnowledgeBaseModel
    KnowledgeBaseModel.__table__.create(bind=engine, checkfirst=True)


def test_kb_repository_storage_backend_roundtrip(ks_db):
    """repository 层：add / get / set / 非法值校验。"""
    _ensure_kb_table(ks_db)
    from chayuan.server.db.repository.knowledge_base_repository import (
        add_kb_to_db, delete_kb_from_db, get_kb_storage_backend,
        set_kb_storage_backend,
    )

    name = "t_storage_kb"
    delete_kb_from_db(name)
    # 新建：默认 storage_backend=NULL（跟随全局）
    add_kb_to_db(name, "info", "faiss", "bge")
    assert get_kb_storage_backend(name) == ""

    # 显式设 local
    assert set_kb_storage_backend(name, "local") is True
    assert get_kb_storage_backend(name) == "local"

    # 改 minio
    assert set_kb_storage_backend(name, "minio") is True
    assert get_kb_storage_backend(name) == "minio"

    # 清空 → 恢复跟随全局
    assert set_kb_storage_backend(name, "") is True
    assert get_kb_storage_backend(name) == ""

    # 非法值
    with pytest.raises(ValueError):
        set_kb_storage_backend(name, "gdrive")

    # 不存在的 KB：set 返回 False
    assert set_kb_storage_backend("nonexistent_kb_xyz", "local") is False

    delete_kb_from_db(name)


def test_get_storage_for_kb_follows_override(ks_db, monkeypatch):
    """``get_storage_for_kb`` 优先用 KB 记录里的 storage_backend；不可用后端自动降级。"""
    _ensure_kb_table(ks_db)
    reset_cache()
    from chayuan.server.db.repository.knowledge_base_repository import (
        add_kb_to_db, delete_kb_from_db, set_kb_storage_backend,
    )
    from chayuan.server.file_storage import get_storage_for_kb
    from chayuan.settings import Settings

    name = "t_override_kb"
    delete_kb_from_db(name)
    add_kb_to_db(name, "info", "faiss", "bge")
    monkeypatch.setattr(
        Settings.basic_settings, "FILE_STORAGE_BACKEND", "local", raising=False
    )

    # override=空 → 跟全局 local
    assert get_storage_for_kb(name).name == "local"

    # override=local → 还是 local
    set_kb_storage_backend(name, "local")
    reset_cache()
    assert get_storage_for_kb(name).name == "local"

    # override=minio but 环境里没 MINIO_ENDPOINT → 工厂降级回 local（不 crash）
    set_kb_storage_backend(name, "minio")
    monkeypatch.setattr(Settings.basic_settings, "MINIO_ENDPOINT", "",
                         raising=False)
    reset_cache()
    assert get_storage_for_kb(name).name == "local"

    set_kb_storage_backend(name, "")
    delete_kb_from_db(name)
    reset_cache()


def test_get_storage_for_kb_empty_name_falls_back(ks_db, monkeypatch):
    reset_cache()
    from chayuan.server.file_storage import get_storage, get_storage_for_kb
    # 空 kb_name → 回落 get_storage()
    assert get_storage_for_kb("") is get_storage()
    reset_cache()
