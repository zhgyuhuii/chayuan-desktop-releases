"""``model_registry.first_launch.run_first_launch_hooks`` 测试。"""
from __future__ import annotations

import sys
from typing import Any, Dict
from unittest import mock

import pytest

from chayuan.server.model_registry.first_launch import (
    FirstLaunchReport,
    run_first_launch_hooks,
)


@pytest.fixture
def chayuan_root_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    # local_index 单例可能在其它测试里建过,这里重置以走新根目录
    import chayuan.server.model_registry.local_index as li
    monkeypatch.setattr(li, "_SINGLETON", None)
    # 默认 mock 掉 bundled_models_dir,避免本仓库 vendor/bundled_models 被默认扫到污染断言;
    # 显式需要 seed 路径的用例自行 monkey.setenv 即可恢复真实解析(env 优先于 mock 不成立,
    # 这里用 monkey.setattr 直接覆盖函数)。
    monkeypatch.setattr(li, "bundled_models_dir", lambda: None)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    return tmp_path


def test_hook_runs_four_steps_and_reports(chayuan_root_tmp):
    r = run_first_launch_hooks()
    assert isinstance(r, FirstLaunchReport)
    # bundled 源被 mock 成 None -> seed 步骤跑过但 copied/skipped 为空
    assert r.seeded_source is None
    assert r.seeded_copied == []
    # 即使没有本地模型,scan 也应该跑过(空 delta)
    assert r.scanned is True
    # 手册总能部署成功(包内资源 + 临时 root 可写)
    assert r.manuals_md, f"errors={r.errors}"


def test_hook_runs_seed_then_scan_when_bundled_present(tmp_path, monkeypatch):
    """提供真实 bundled 源时,seed 应把文件拷到 CHAYUAN_ROOT/models/bundled,
    随后 scan_once 能扫到 'bundled/' 前缀的 relpath。"""
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    import chayuan.server.model_registry.local_index as li
    monkeypatch.setattr(li, "_SINGLETON", None)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    src = tmp_path / "src_bundled"
    (src / "chat").mkdir(parents=True)
    (src / "chat" / "tiny-chat.gguf").write_bytes(b"chat-bytes")
    monkeypatch.setenv("CHAYUAN_BUNDLED_MODELS_DIR", str(src))

    r = run_first_launch_hooks()
    assert r.seeded_source == str(src)
    assert r.seeded_target == str(tmp_path / "models" / "bundled")
    assert "bundled/chat/tiny-chat.gguf" in r.seeded_copied
    # scan 也应该跑过,且把它纳入索引
    assert r.scanned is True
    idx = li.get_local_index()
    relpaths = {e.relpath for e in idx.list_entries()}
    assert any(r2.startswith("bundled/chat/tiny-chat.gguf") for r2 in relpaths), relpaths


def test_hook_swallows_seed_failure(chayuan_root_tmp):
    with mock.patch(
        "chayuan.server.model_registry.bundled_seed.seed_bundled_models",
        side_effect=RuntimeError("seed boom"),
    ):
        r = run_first_launch_hooks()
    assert r.seeded_copied == []
    assert any("seed_bundled_models" in e for e in r.errors)
    # 后续步骤继续
    assert r.scanned is True


def test_hook_swallows_scan_failure(chayuan_root_tmp):
    with mock.patch(
        "chayuan.server.model_registry.local_index.scan_once",
        side_effect=RuntimeError("disk error"),
    ):
        r = run_first_launch_hooks()
    # scan 失败被吞,后续步骤继续
    assert r.scanned is False
    assert any("scan_once" in e for e in r.errors)
    # 但手册部署不受影响
    assert r.manuals_md or r.manuals_skipped


def test_hook_swallows_promote_failure(chayuan_root_tmp):
    with mock.patch(
        "chayuan.server.model_registry.auto_assign.promote_defaults_from_local",
        side_effect=RuntimeError("yaml error"),
    ):
        r = run_first_launch_hooks()
    assert r.promoted == {}
    assert any("promote_defaults" in e for e in r.errors)


def test_hook_swallows_manuals_failure(chayuan_root_tmp):
    with mock.patch(
        "chayuan.server.manuals.deploy.deploy_user_manuals",
        side_effect=RuntimeError("io error"),
    ):
        r = run_first_launch_hooks()
    assert r.manuals_md == []
    assert any("deploy_user_manuals" in e for e in r.errors)


def test_hook_to_dict_is_json_safe(chayuan_root_tmp):
    import json
    r = run_first_launch_hooks()
    s = json.dumps(r.to_dict())
    d = json.loads(s)
    assert "scanned" in d
    assert "promoted" in d
    assert "manuals_md" in d
    assert "errors" in d
    assert "seeded_source" in d
    assert "seeded_copied" in d
    assert "seeded_skipped" in d
    assert "preload_bootstrapped" in d


# ───────── bundled cap "装机即用" 翻 preload/auto_start 测试 ─────────


@pytest.fixture
def bootstrap_env(tmp_path, monkeypatch):
    """把 CHAYUAN_ROOT、local_index 单例、auto_start_store 文件、registry 单例
    都指向 tmp_path,且把 set_config 拦截到一个本地 dict 以便断言。"""
    monkeypatch.setattr("chayuan.settings.CHAYUAN_ROOT", tmp_path)
    import chayuan.server.model_registry.local_index as li
    monkeypatch.setattr(li, "_SINGLETON", None)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)

    # 屏蔽真实 bundled_models_dir() 默认会回溯到 <repo>/vendor/bundled_models/
    # 这种"开发态污染断言"的行为;只尊重测试设的 CHAYUAN_BUNDLED_MODELS_DIR。
    import os as _os
    from pathlib import Path as _Path

    def _stub_bundled_dir():
        env = _os.environ.get("CHAYUAN_BUNDLED_MODELS_DIR")
        if env:
            p = _Path(env).expanduser()
            if p.is_dir():
                return p
        return None

    monkeypatch.setattr(li, "bundled_models_dir", _stub_bundled_dir)

    # auto_start_store 的 settings 文件改到 tmp
    from chayuan.server.runtime import auto_start_store
    store_path = tmp_path / "sidecar_settings.json"
    monkeypatch.setattr(auto_start_store, "_settings_path", lambda: store_path)

    # 拦截 LocalRuntimeRegistry — 不真实拉 SidecarRuntimeManager 起来
    from chayuan.server.model_registry import local_runtime_registry as lrr
    monkeypatch.setattr(lrr, "_registry_singleton", None)
    saved_updates: Dict[str, Any] = {}
    # 5 个 cap 各一份模拟 _settings,bootstrap 同步逻辑会 setattr 进去
    cap_settings: Dict[str, Any] = {
        cap: type("_Settings", (), {
            "preload_on_startup": False,
            "preload_embedding": False,
            "preload_rerank": False,
            "preload_asr": False,
            "preload_image_embedding": False,
        })()
        for cap in lrr.LocalRuntimeRegistry.CAPABILITIES
    }

    class _StubManager:
        def __init__(self, cap):
            self._cap = cap

        @property
        def settings(self):
            return cap_settings[self._cap]

        def set_config(self, update):
            saved_updates.update(update)
            # 跟真实 set_config 一样,同步到 chat manager 的 _settings
            for k, v in update.items():
                if hasattr(cap_settings["chat"], k):
                    setattr(cap_settings["chat"], k, v)
            return update

    class _StubRegistry:
        def get(self, cap):
            return _StubManager(cap)

    monkeypatch.setattr(lrr, "get_registry", lambda: _StubRegistry())
    return {
        "root": tmp_path,
        "store_path": store_path,
        "saved_updates": saved_updates,
        "auto_start_store": auto_start_store,
        "cap_settings": cap_settings,
    }


def _make_bundled_models(root, *caps):
    """在 ``<root>/models/bundled/<cap>/dummy.bin`` 落几个假模型文件。"""
    dst = root / "models" / "bundled"
    for cap in caps:
        sub = dst / cap
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "dummy.bin").write_bytes(b"x" * 8)


def test_bootstrap_enables_preload_for_bundled_caps(bootstrap_env):
    """lite 安装包含 embedding + rerank + asr + ocr → 全部翻成 True。"""
    from chayuan.server.model_registry.first_launch import (
        _bootstrap_preload_from_bundled, FirstLaunchReport,
    )
    _make_bundled_models(
        bootstrap_env["root"], "embedding", "rerank", "asr", "ocr",
    )
    report = FirstLaunchReport()
    _bootstrap_preload_from_bundled(report)

    # LocalRuntimeSettings 字段 — ocr 不在 yaml,只翻 auto_start.rapidocr
    assert bootstrap_env["saved_updates"] == {
        "preload_embedding": True,
        "preload_rerank": True,
        "preload_asr": True,
    }
    # auto_start_store
    store = bootstrap_env["auto_start_store"]
    assert store.get("embedding") is True
    assert store.get("rerank") is True
    assert store.get("asr") is True
    assert store.get("rapidocr") is True
    # 所有 4 个 cap 都已 mark
    assert store.is_bootstrapped("embedding")
    assert store.is_bootstrapped("rerank")
    assert store.is_bootstrapped("asr")
    assert store.is_bootstrapped("ocr")
    # 没 bundled 内容的 cap 不该被翻
    assert "preload_on_startup" not in bootstrap_env["saved_updates"]
    assert "preload_image_embedding" not in bootstrap_env["saved_updates"]
    assert not store.is_bootstrapped("chat")
    assert not store.is_bootstrapped("image")
    # report 字段
    assert set(report.preload_bootstrapped.keys()) == {
        "embedding", "rerank", "asr", "ocr",
    }


def test_bootstrap_skips_caps_already_marked(bootstrap_env):
    """二次启动:embedding 已 mark → 这次不再翻,user 后续 set False 不被翻回。"""
    from chayuan.server.model_registry.first_launch import (
        _bootstrap_preload_from_bundled, FirstLaunchReport,
    )
    _make_bundled_models(bootstrap_env["root"], "embedding")
    store = bootstrap_env["auto_start_store"]
    store.mark_bootstrapped("embedding")
    store.set_("embedding", False)  # 用户明确关掉

    report = FirstLaunchReport()
    _bootstrap_preload_from_bundled(report)

    # 不应再翻
    assert bootstrap_env["saved_updates"] == {}
    assert store.get("embedding") is False
    assert report.preload_bootstrapped == {}


def test_bootstrap_skips_caps_without_content(bootstrap_env):
    """cap 子目录只有隐藏文件 / 不存在 → 不翻。"""
    from chayuan.server.model_registry.first_launch import (
        _bootstrap_preload_from_bundled, FirstLaunchReport,
    )
    dst = bootstrap_env["root"] / "models" / "bundled"
    (dst / "embedding").mkdir(parents=True, exist_ok=True)
    (dst / "embedding" / ".gitkeep").write_text("")  # 隐藏文件不算
    # rerank 干脆不存在

    report = FirstLaunchReport()
    _bootstrap_preload_from_bundled(report)

    assert bootstrap_env["saved_updates"] == {}
    assert report.preload_bootstrapped == {}
    store = bootstrap_env["auto_start_store"]
    assert not store.is_bootstrapped("embedding")
    assert not store.is_bootstrapped("rerank")


def test_bootstrap_image_cap_maps_to_image_embedding(bootstrap_env):
    """bundled 'image' 子目录 → preload_image_embedding + auto_start.image-embedding。"""
    from chayuan.server.model_registry.first_launch import (
        _bootstrap_preload_from_bundled, FirstLaunchReport,
    )
    _make_bundled_models(bootstrap_env["root"], "image")

    report = FirstLaunchReport()
    _bootstrap_preload_from_bundled(report)

    assert bootstrap_env["saved_updates"] == {"preload_image_embedding": True}
    store = bootstrap_env["auto_start_store"]
    assert store.get("image-embedding") is True
    assert store.is_bootstrapped("image")
    assert report.preload_bootstrapped == {"image": "preload_image_embedding"}


def test_bootstrap_source_fallback_when_dst_empty(bootstrap_env, monkeypatch):
    """seed_bundled_models 失败导致 dst 空,但 source(``vendor/bundled_models/``
    或 ``_MEIPASS/bundled_models/``)有内容 → bootstrap 仍翻。

    这是 lite 装机即用最关键的兜底:用户被杀软挡了一些文件 / 数据目录权限有
    问题导致 seed 没跑完,只要安装包本身把 cap 打进去了,服务该启的就该启。
    """
    from chayuan.server.model_registry.first_launch import (
        _bootstrap_preload_from_bundled, FirstLaunchReport,
    )
    # dst 完全空(没调 _make_bundled_models)
    # source 通过 CHAYUAN_BUNDLED_MODELS_DIR 环境变量提供
    src = bootstrap_env["root"] / "src_bundled"
    (src / "embedding").mkdir(parents=True)
    (src / "embedding" / "model.gguf").write_bytes(b"X")
    (src / "rerank").mkdir(parents=True)
    (src / "rerank" / "model.gguf").write_bytes(b"Y")
    monkeypatch.setenv("CHAYUAN_BUNDLED_MODELS_DIR", str(src))

    report = FirstLaunchReport()
    _bootstrap_preload_from_bundled(report)

    assert bootstrap_env["saved_updates"] == {
        "preload_embedding": True,
        "preload_rerank": True,
    }
    store = bootstrap_env["auto_start_store"]
    assert store.is_bootstrapped("embedding")
    assert store.is_bootstrapped("rerank")
    assert report.preload_bootstrapped == {
        "embedding": "preload_embedding",
        "rerank": "preload_rerank",
    }


def test_bootstrap_public_alias_callable(bootstrap_env):
    """``bootstrap_preload_from_bundled`` 是供 server_app 兜底用的公开 API,
    必须能从 first_launch 模块导出,行为跟私版一致。"""
    from chayuan.server.model_registry.first_launch import (
        FirstLaunchReport, bootstrap_preload_from_bundled,
    )
    _make_bundled_models(bootstrap_env["root"], "embedding")
    report = FirstLaunchReport()
    bootstrap_preload_from_bundled(report)
    assert bootstrap_env["saved_updates"] == {"preload_embedding": True}
    assert report.preload_bootstrapped == {"embedding": "preload_embedding"}


def test_bootstrap_is_idempotent_on_repeated_call(bootstrap_env):
    """server_app._auto_start_capabilities 会作为兜底再调一次 bootstrap;
    第二次调用必须是 no-op,不能把已被用户改过的 auto_start 翻回去。"""
    from chayuan.server.model_registry.first_launch import (
        FirstLaunchReport, bootstrap_preload_from_bundled,
    )
    _make_bundled_models(bootstrap_env["root"], "embedding", "rerank")

    # 第一次:翻 embedding/rerank
    bootstrap_preload_from_bundled(FirstLaunchReport())
    store = bootstrap_env["auto_start_store"]
    assert store.is_bootstrapped("embedding")
    assert store.is_bootstrapped("rerank")
    # 用户手动关掉 embedding(模拟 UI 操作)
    store.set_("embedding", False)
    bootstrap_env["saved_updates"].clear()

    # 第二次(兜底路径):不该再翻
    r2 = FirstLaunchReport()
    bootstrap_preload_from_bundled(r2)
    assert bootstrap_env["saved_updates"] == {}, (
        "兜底调用不该再写 yaml,用户已经表态过的不能被翻回"
    )
    assert store.get("embedding") is False
    assert r2.preload_bootstrapped == {}


def test_bootstrap_syncs_settings_to_all_managers(bootstrap_env):
    """翻完字段后,5 个 manager 各自的 _settings 都该看到新值,而不仅 chat 那份。

    防止"chat manager 单例已读 yaml → set_config 更新自己,但 embedding /
    rerank 等 manager 还在用 __init__ 时 load 的旧实例"的 stale singleton bug。
    """
    from chayuan.server.model_registry.first_launch import (
        _bootstrap_preload_from_bundled, FirstLaunchReport,
    )
    _make_bundled_models(bootstrap_env["root"], "embedding", "rerank", "asr")

    report = FirstLaunchReport()
    _bootstrap_preload_from_bundled(report)

    cs = bootstrap_env["cap_settings"]
    for cap in ("chat", "embedding", "rerank", "asr", "image-embedding"):
        assert cs[cap].preload_embedding is True, (
            f"{cap}.settings.preload_embedding 没同步"
        )
        assert cs[cap].preload_rerank is True, (
            f"{cap}.settings.preload_rerank 没同步"
        )
        assert cs[cap].preload_asr is True, (
            f"{cap}.settings.preload_asr 没同步"
        )
