"""统一文件存储后端配置卡（local / minio 二选一）。

设计目的
========
把 ``FILE_STORAGE_BACKEND`` + ``KB_ROOT_PATH`` + ``FILE_STORAGE_LOCAL_ROOT`` +
``MINIO_*``（7 个字段）合成一个**可视化表单**，供配置面板复用。
dialog 两处共用同一个 renderer。

和项目里已有的 ``redis_config.py`` / ``db_config.py`` / ``vs_config.py`` 结构一致：
- ``render_storage_card(ui)``：入口，调用方传入 NiceGUI ``ui`` 模块即可；
- 所有字段都落到 ``basic_settings.yaml``，与 ``file_storage/factory.py::_build()``
  读取的键完全对齐；
- 「验证连通」直接在本进程里新建一个临时 ``MinioStorage`` 调 ``backend_info()``
  （内含 ``list_buckets``），能验证凭据+region+secure；
- 「保存」写 yaml → 调 ``file_storage.factory.reset_cache()`` 清单例 →
  调用方传入的 ``mark_restart_needed`` 给顶部标记黄条。

业务侧无需任何改动：
- ``server/knowledge_base/kb_doc_api.py::upload_docs`` 已经 ``get_storage().put(...)``；
- ``server/api_server/image_routes.py`` 已经在 minio 后端返回 302 presigned URL；
- ``server/api_server/storage_routes.py::/storage/stream`` 是 local 后端的
  服务端代理路径，供 ``LocalStorage.presigned_url`` 用。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from chayuan.server.config_panel import yaml_store

logger = logging.getLogger("chayuan.config_panel.storage_config")


# ---------------------------------------------------------------------------
# 常量 & 辅助
# ---------------------------------------------------------------------------

# basic_settings.yaml 里这一组字段，放在同一张卡里管理
STORAGE_FIELDS = (
    "FILE_STORAGE_BACKEND",
    "KB_ROOT_PATH",
    "FILE_STORAGE_LOCAL_ROOT",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "MINIO_SECURE",
    "MINIO_REGION",
    "MINIO_BUCKET_PREFIX",
)

_BACKEND_OPTIONS = {
    "local": "local（本机磁盘；单机默认；适合开发 / 小规模）",
    "minio": "minio（S3 兼容对象存储；推荐多副本 / 云部署 / 需预签名 URL）",
}


def _read_current() -> Dict[str, Any]:
    """从 basic_settings.yaml 读当前值；缺省一律用与 file_storage/factory 相同的默认值。"""
    doc = yaml_store.load_yaml("basic_settings.yaml").doc or {}
    backend = str(doc.get("FILE_STORAGE_BACKEND") or "local").strip().lower()
    if backend not in ("local", "minio"):
        backend = "local"
    return {
        "FILE_STORAGE_BACKEND": backend,
        "KB_ROOT_PATH": str(doc.get("KB_ROOT_PATH") or ""),
        "FILE_STORAGE_LOCAL_ROOT": str(doc.get("FILE_STORAGE_LOCAL_ROOT") or ""),
        "MINIO_ENDPOINT": str(doc.get("MINIO_ENDPOINT") or ""),
        "MINIO_ACCESS_KEY": str(doc.get("MINIO_ACCESS_KEY") or ""),
        "MINIO_SECRET_KEY": str(doc.get("MINIO_SECRET_KEY") or ""),
        "MINIO_SECURE": bool(doc.get("MINIO_SECURE") or False),
        "MINIO_REGION": str(doc.get("MINIO_REGION") or "us-east-1"),
        "MINIO_BUCKET_PREFIX": str(doc.get("MINIO_BUCKET_PREFIX") or "chayuan"),
    }


def _kb_root_effective() -> str:
    """展示"当前生效的 KB_ROOT_PATH"（yaml 里为空时给派生默认值）。"""
    try:
        from chayuan.settings import Settings
        return str(getattr(Settings.basic_settings, "KB_ROOT_PATH", "") or "") or "<跟随 CHAYUAN_ROOT>"
    except Exception:  # noqa: BLE001
        return "<跟随 CHAYUAN_ROOT>"


def _local_root_effective() -> str:
    """LocalStorage 实际落盘根；调 _default_root 以和运行时一致。"""
    try:
        from chayuan.server.file_storage.local import _default_root  # type: ignore
        return str(_default_root())
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# 验证（进程内，不依赖 HTTP）
# ---------------------------------------------------------------------------

def validate_minio(values: Dict[str, Any], *, timeout: float = 3.0) -> Dict[str, Any]:
    """用 form 里的值新建一个临时 ``MinioStorage`` 并 ``backend_info()``；
    返回 ``{ok, message, detail}``，UI 直接展示。

    注意：用"临时 prefix"避免误触发一组空 bucket 创建；我们只想验证凭据+可达性。
    """
    ep = str(values.get("MINIO_ENDPOINT") or "").strip()
    if not ep:
        return {"ok": False, "message": "MINIO_ENDPOINT 不能为空", "detail": ""}
    ak = str(values.get("MINIO_ACCESS_KEY") or "").strip()
    sk = str(values.get("MINIO_SECRET_KEY") or "")
    if not ak or not sk:
        return {"ok": False, "message": "Access Key / Secret Key 不能为空", "detail": ""}

    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("minio", "minio>=7.2,<8.0")
        from chayuan.server.file_storage.minio import MinioStorage
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "message": f"未安装 minio 客户端（自动安装失败）：{e}",
            "detail": "可手动执行 `pip install 'minio>=7.2,<8.0'` 后重试",
        }

    try:
        storage = MinioStorage(
            endpoint=ep,
            access_key=ak,
            secret_key=sk,
            secure=bool(values.get("MINIO_SECURE", False)),
            region=str(values.get("MINIO_REGION") or "us-east-1"),
            # 重要：用一个"只读"前缀，backend_info 不会创建 bucket
            bucket_prefix=str(values.get("MINIO_BUCKET_PREFIX") or "chayuan"),
        )
        info = storage.backend_info()
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "message": f"{type(e).__name__}: {e}",
            "detail": ep,
        }
    if not info.get("healthy"):
        return {
            "ok": False,
            "message": info.get("error") or "连接失败",
            "detail": ep,
        }
    buckets = info.get("existing_buckets") or []
    parts = [f"连接 OK · endpoint={info.get('endpoint') or ep}"]
    if buckets:
        sample = ", ".join(buckets[:5]) + ("…" if len(buckets) > 5 else "")
        parts.append(f"可见 {len(buckets)} 个 bucket（{sample}）")
    return {
        "ok": True,
        "message": "；".join(parts),
        "detail": ep,
    }


# ---------------------------------------------------------------------------
# 摘要（给卡头/节点 tooltip 用）
# ---------------------------------------------------------------------------

def backend_summary() -> Tuple[str, str]:
    """返回 ``(简短模式标签, 一行 endpoint/path 描述)``，不做探活。"""
    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        backend = str(getattr(bs, "FILE_STORAGE_BACKEND", "local") or "local").strip().lower()
    except Exception:  # noqa: BLE001
        backend = "local"
    if backend == "minio":
        try:
            ep = str(getattr(Settings.basic_settings, "MINIO_ENDPOINT", "") or "")
        except Exception:  # noqa: BLE001
            ep = ""
        return "MinIO", ep or "—"
    return "Local", _local_root_effective() or "—"


# ---------------------------------------------------------------------------
# UI —— 唯一对外 API
# ---------------------------------------------------------------------------

def render_storage_card(
    ui,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
    title: str = "文件存储 / 知识库路径",
    show_title: bool = True,
) -> None:
    """在当前 NiceGUI 容器里渲染一张统一的存储后端配置卡。

    ``mark_restart_needed``：当 backend / endpoint 等关键字段变化、保存成功后调用，
    供调用方在页面顶部挂"需要重启"黄条。
    """
    current = _read_current()
    inputs: Dict[str, Any] = {}

    # --- 统一的"读 form"工具 ---
    def _v(name: str, default: Any = "") -> Any:
        el = inputs.get(name)
        if el is None:
            return default
        try:
            return el.value
        except Exception:  # noqa: BLE001
            return default

    def _collect() -> Dict[str, Any]:
        backend = str(_v("FILE_STORAGE_BACKEND", "local") or "local").strip().lower()
        if backend not in ("local", "minio"):
            backend = "local"
        return {
            "FILE_STORAGE_BACKEND": backend,
            "KB_ROOT_PATH": str(_v("KB_ROOT_PATH", "") or "").strip(),
            "FILE_STORAGE_LOCAL_ROOT": str(_v("FILE_STORAGE_LOCAL_ROOT", "") or "").strip(),
            "MINIO_ENDPOINT": str(_v("MINIO_ENDPOINT", "") or "").strip(),
            "MINIO_ACCESS_KEY": str(_v("MINIO_ACCESS_KEY", "") or "").strip(),
            "MINIO_SECRET_KEY": str(_v("MINIO_SECRET_KEY", "") or ""),
            "MINIO_SECURE": bool(_v("MINIO_SECURE", False)),
            "MINIO_REGION": str(_v("MINIO_REGION", "us-east-1") or "us-east-1").strip() or "us-east-1",
            "MINIO_BUCKET_PREFIX": str(_v("MINIO_BUCKET_PREFIX", "chayuan") or "chayuan").strip() or "chayuan",
        }

    # --- 卡片 ---
    with ui.card().classes("w-full q-mb-md q-pa-md"):
        if show_title:
            with ui.row().classes("items-center w-full no-wrap q-mb-sm").style("gap:12px"):
                ui.label(title).classes("text-base font-semibold")
                ui.space()
                header_badge = ui.label("").classes("text-xs font-mono text-grey-7")

            def _refresh_header() -> None:
                mode, detail = backend_summary()
                try:
                    header_badge.set_text(f"当前：{mode} · {detail}")
                except Exception:  # noqa: BLE001
                    pass
            _refresh_header()
        else:
            header_badge = None

            def _refresh_header() -> None:
                pass

        # 一段最佳实践，放在选择器上方，避免反复解释
        ui.label(
            "统一管理知识库 / 图像 / 对话上传文件的存储位置。\n"
            "  • local：写本机磁盘，零依赖；默认路径跟随 CHAYUAN_ROOT，可覆盖。\n"
            "  • minio：写 MinIO / S3 兼容对象存储；支持预签名直链、跨节点共享。\n"
            "⚠️ 切换后端或 endpoint 后需要重启 chayuan 进程；旧数据不会自动迁移，"
            "可在 /storage/migrate 或「知识库管理」页手动迁移。"
        ).classes("text-xs text-grey-7 q-mb-sm").style("white-space: pre-line")

        # --- 行 1：backend 下拉 ---
        backend_el = (
            ui.select(
                _BACKEND_OPTIONS,
                label="FILE_STORAGE_BACKEND",
                value=current["FILE_STORAGE_BACKEND"],
            )
            .props("outlined dense")
            .classes("w-full")
        )
        backend_el.tooltip(
            "local → 只需填下方「本地路径」；"
            "minio → 额外需要 endpoint / access_key / secret_key"
        )
        inputs["FILE_STORAGE_BACKEND"] = backend_el

        # ================================================================
        # 分区 A：local 字段组
        # ================================================================
        local_box = ui.column().classes("w-full q-mt-sm q-gutter-sm")
        with local_box:
            ui.label("本地路径").classes("text-sm font-semibold")
            ui.label(
                "KB_ROOT_PATH：知识库文件根目录（留空 = 跟随 CHAYUAN_ROOT/data/knowledge_base）。\n"
                "FILE_STORAGE_LOCAL_ROOT：LocalStorage 对象根（留空 = 与 KB_ROOT_PATH 同父级下的 storage/）。\n"
                f"当前生效：KB_ROOT_PATH = {_kb_root_effective()}；"
                f"local root = {_local_root_effective() or '—'}"
            ).classes("text-xs text-grey-7").style("white-space: pre-line")

            kb_el = (
                ui.input(
                    label="KB_ROOT_PATH（可留空）",
                    value=current["KB_ROOT_PATH"],
                    placeholder=_kb_root_effective(),
                )
                .props("outlined dense")
                .classes("w-full")
            )
            kb_el.tooltip(
                "知识库文件存放目录。留空即跟随 $CHAYUAN_ROOT/data/knowledge_base；"
                "填写绝对路径后则固定。"
            )
            inputs["KB_ROOT_PATH"] = kb_el

            lroot_el = (
                ui.input(
                    label="FILE_STORAGE_LOCAL_ROOT（可留空）",
                    value=current["FILE_STORAGE_LOCAL_ROOT"],
                    placeholder=_local_root_effective(),
                )
                .props("outlined dense")
                .classes("w-full")
            )
            lroot_el.tooltip(
                "LocalStorage 的对象根目录（不同于 KB 目录）；"
                "默认在 KB_ROOT_PATH 的父级下以 storage/ 作为 bucket 根。"
            )
            inputs["FILE_STORAGE_LOCAL_ROOT"] = lroot_el

        # ================================================================
        # 分区 B：MinIO 字段组
        # ================================================================
        minio_box = ui.column().classes("w-full q-mt-sm q-gutter-sm")
        with minio_box:
            ui.label("MinIO / S3 连接").classes("text-sm font-semibold")
            ui.label(
                "docker/dev-stack 默认：endpoint=127.0.0.1:9000，账号 minioadmin/minioadmin，secure=关。\n"
                "生产建议：用独立最小权限账号，endpoint 走 HTTPS，region 填真实值。\n"
                "首次上传会自动按 namespace 建 bucket（chayuan-kb-content / chat-temp / "
                "image-files / misc），无需手动建桶。"
            ).classes("text-xs text-grey-7").style("white-space: pre-line")

            with ui.grid(columns=2).classes("w-full q-gutter-sm"):
                ep_el = (
                    ui.input(
                        label="MINIO_ENDPOINT",
                        value=current["MINIO_ENDPOINT"],
                        placeholder="127.0.0.1:9000 或 https://s3.amazonaws.com",
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                ep_el.tooltip(
                    "host:port 形式；带 http:// / https:// 会自动拆分并覆盖 MINIO_SECURE。"
                )
                inputs["MINIO_ENDPOINT"] = ep_el

                region_el = (
                    ui.input(
                        label="MINIO_REGION",
                        value=current["MINIO_REGION"],
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                region_el.tooltip(
                    "MinIO 自托管常填 us-east-1；阿里云 OSS / AWS S3 填真实 region。"
                )
                inputs["MINIO_REGION"] = region_el

                ak_el = (
                    ui.input(
                        label="MINIO_ACCESS_KEY",
                        value=current["MINIO_ACCESS_KEY"],
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                ak_el.tooltip("Access Key（或 Root User）。")
                inputs["MINIO_ACCESS_KEY"] = ak_el

                sk_el = (
                    ui.input(
                        label="MINIO_SECRET_KEY",
                        value=current["MINIO_SECRET_KEY"],
                        password=True,
                        password_toggle_button=True,
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                sk_el.tooltip(
                    "Secret；保存时以明文落盘到 basic_settings.yaml，"
                    "生产场景请收敛 yaml 读权限。"
                )
                inputs["MINIO_SECRET_KEY"] = sk_el

                prefix_el = (
                    ui.input(
                        label="MINIO_BUCKET_PREFIX",
                        value=current["MINIO_BUCKET_PREFIX"],
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                prefix_el.tooltip(
                    "bucket 名 = {prefix}-{namespace}；namespace 固定 4 个："
                    "kb-content / chat-temp / image-files / misc。"
                )
                inputs["MINIO_BUCKET_PREFIX"] = prefix_el

                secure_el = ui.switch(
                    "MINIO_SECURE（使用 HTTPS）",
                    value=current["MINIO_SECURE"],
                ).props("dense")
                secure_el.tooltip(
                    "对外 TLS 时打开；endpoint 里若已带 http(s):// 前缀则以前缀为准。"
                )
                inputs["MINIO_SECURE"] = secure_el

        # --- 条件显隐：切 backend 时 ---
        def _apply_visibility() -> None:
            backend = str(backend_el.value or "local").strip().lower()
            is_minio = backend == "minio"
            try:
                local_box.visible = not is_minio
                minio_box.visible = is_minio
            except Exception:  # noqa: BLE001
                pass

        _apply_visibility()
        try:
            backend_el.on("update:model-value", lambda _=None: _apply_visibility())
        except Exception:  # noqa: BLE001
            pass

        # ================================================================
        # 结果行 + 按钮行
        # ================================================================
        result_row = ui.row().classes("items-center q-mt-sm").style(
            "gap:6px; min-width:0"
        )
        result_row.visible = False
        with result_row:
            result_icon = ui.icon("info").style("font-size: 20px")
            with ui.column().classes("q-gutter-none").style("min-width: 0"):
                result_text = ui.label("").classes("text-sm")
                result_detail = ui.label("").classes(
                    "text-xs text-grey-7 font-mono"
                ).style("word-break: break-all")
                result_detail.visible = False

        def _show_busy(msg: str, detail: str = "") -> None:
            result_row.visible = True
            result_icon.props("name=hourglass_top color=primary")
            result_text.set_text(msg)
            result_detail.set_text(detail)
            result_detail.visible = bool(detail)

        def _show_result(ok: bool, msg: str, detail: str = "") -> None:
            result_row.visible = True
            result_icon.props(
                "name=check_circle color=positive" if ok
                else "name=error color=negative"
            )
            result_text.set_text(msg)
            result_detail.set_text(detail)
            result_detail.visible = bool(detail)

        def _on_validate() -> None:
            v = _collect()
            if v["FILE_STORAGE_BACKEND"] != "minio":
                _show_result(
                    True,
                    "当前为 local 后端；本机磁盘一般无需验证。",
                    _local_root_effective() or "—",
                )
                return
            _show_busy("正在测试 MinIO 连接…", v["MINIO_ENDPOINT"])
            res = validate_minio(v)
            _show_result(
                bool(res.get("ok")),
                str(res.get("message") or ""),
                str(res.get("detail") or ""),
            )

        def _on_save() -> None:
            v = _collect()
            # backend=minio 时必填校验
            if v["FILE_STORAGE_BACKEND"] == "minio":
                for k in ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
                    if not str(v.get(k, "")).strip():
                        ui.notify(
                            f"{k} 不能为空（backend=minio 时必填）",
                            color="negative",
                        )
                        return
            try:
                _path, _bak, changes = yaml_store.save_updates(
                    "basic_settings.yaml", v,
                )
            except Exception as e:  # noqa: BLE001
                ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
                return

            # 清文件存储工厂的单例，让下一次 get_storage() 用新值
            try:
                from chayuan.server.file_storage.factory import reset_cache
                reset_cache()
            except Exception:  # noqa: BLE001
                pass

            if changes:
                ui.notify(
                    f"已保存到 basic_settings.yaml（{len(changes)} 项）；"
                    "切换 backend 或修改 endpoint 后建议重启进程以让所有模块采用新配置。",
                    color="positive", timeout=6000,
                )
                if mark_restart_needed is not None:
                    try:
                        mark_restart_needed()
                    except Exception:  # noqa: BLE001
                        pass
            else:
                ui.notify("配置未变化", color="info", timeout=3000)

            # 保存后顺带探活一次，给闭环反馈
            _refresh_header()
            if v["FILE_STORAGE_BACKEND"] == "minio":
                res = validate_minio(v)
                _show_result(
                    bool(res.get("ok")),
                    ("保存成功 · " if bool(res.get("ok")) else "保存成功（连接异常）· ")
                    + str(res.get("message") or ""),
                    str(res.get("detail") or ""),
                )
            else:
                _show_result(
                    True,
                    "保存成功（local 后端，无需在线验证）。",
                    _local_root_effective() or "—",
                )

        with ui.row().classes("w-full justify-end q-mt-sm").style("gap:8px"):
            ui.button("验证连通", icon="bolt", on_click=_on_validate).props(
                "color=secondary dense"
            ).tooltip(
                "用当前表单字段在本进程里建 MinIO 客户端调 list_buckets；"
                "能验证 endpoint+凭据+region+secure。local 后端无需验证。"
            )
            ui.button("保存", icon="save", on_click=_on_save).props(
                "color=primary dense"
            ).tooltip(
                "写入 basic_settings.yaml 并清 file_storage 单例缓存；"
                "backend / endpoint 变动需重启 chayuan 进程。"
            )


__all__ = [
    "STORAGE_FIELDS",
    "render_storage_card",
    "validate_minio",
    "backend_summary",
]
