"""Redis 连接配置卡片。

设计要点
--------
- 项目用 ``basic_settings.yaml`` 里的 ``REDIS_URL`` 驱动：
  限流（resilience）、语义缓存、异步入库队列（arq）、/readyz 探活都会读它；
  见 ``chayuan/server/shared/__init__.py``、``chayuan/server/resilience.py``。
- 之前性能体检页只有只读的 summary，用户想改就得去 ``basic_settings.yaml`` 手敲；
  这里把 URL **解析成字段表单**（scheme/host/port/db/username/password），
  既能看清密码格式也能避免把特殊字符漏 URL-encode。
- 「验证连接」直接走 ``redis.Redis.from_url(url, socket_connect_timeout=…).ping()``，
  能 PING 通才算成功；还会拿一下 server info 展示版本号。
- 「保存」把组装好的 URL 写回 ``basic_settings.yaml:REDIS_URL``；大部分依赖
  Redis 的功能在进程启动时读取 URL，所以保存后会提示需要重启。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote, unquote, urlparse, urlunparse

from chayuan.server.config_panel import yaml_store


# ---------------------------------------------------------------------------
# URL <-> Form 转换
# ---------------------------------------------------------------------------

@dataclass
class RedisFormValues:
    scheme: str = "redis"      # redis / rediss
    host: str = "127.0.0.1"
    port: str = "6379"
    db: str = "0"
    username: str = ""          # ACL 6.0+；留空则用默认 default 用户
    password: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "db": self.db,
            "username": self.username,
            "password": self.password,
        }


def parse_url(url: str) -> RedisFormValues:
    """把 ``redis://user:pass@host:port/db`` 解回 form values。

    - 空字符串 / 非 redis 协议时返回一份默认值；
    - userinfo 里的 ``user`` / ``pass`` 会 URL-decode 回明文，方便展示。
    """
    default = RedisFormValues()
    if not url:
        return default
    try:
        p = urlparse(url)
    except Exception:
        return default

    scheme = (p.scheme or "redis").lower()
    if scheme not in ("redis", "rediss"):
        scheme = "redis"

    host = p.hostname or "127.0.0.1"
    port = str(p.port or 6379)
    # path 是 "/0"；去掉前导 /
    db = (p.path or "").lstrip("/") or "0"
    username = unquote(p.username or "") if p.username else ""
    password = unquote(p.password or "") if p.password else ""

    return RedisFormValues(
        scheme=scheme,
        host=host,
        port=port,
        db=db,
        username=username,
        password=password,
    )


def build_url(v: RedisFormValues) -> str:
    """把 form values 组装回 URL。密码里的特殊字符会 URL-encode。"""
    scheme = v.scheme or "redis"
    host = (v.host or "").strip() or "127.0.0.1"
    port = (str(v.port) or "6379").strip() or "6379"
    db = (str(v.db) or "0").strip() or "0"

    userinfo = ""
    if v.username or v.password:
        u = quote(v.username or "", safe="")
        if v.password:
            userinfo = f"{u}:{quote(v.password, safe='')}@"
        else:
            userinfo = f"{u}@"

    return f"{scheme}://{userinfo}{host}:{port}/{db}"


def masked_url(v: RedisFormValues) -> str:
    """展示给用户看的 URL——密码位置替换成 ***。"""
    scheme = v.scheme or "redis"
    host = (v.host or "").strip() or "127.0.0.1"
    port = (str(v.port) or "6379").strip() or "6379"
    db = (str(v.db) or "0").strip() or "0"

    userinfo = ""
    if v.username or v.password:
        u = v.username or ""
        userinfo = f"{u}:***@" if v.password else f"{u}@"
    return f"{scheme}://{userinfo}{host}:{port}/{db}"


def _strip_password_from_url(url: str) -> str:
    try:
        p = urlparse(url)
        if p.password:
            netloc = p.netloc.replace(f":{p.password}", ":***")
            return urlunparse(p._replace(netloc=netloc))
    except Exception:
        pass
    return url


# ---------------------------------------------------------------------------
# 验证
# ---------------------------------------------------------------------------

def validate_connection(url: str, timeout: float = 3.0) -> Dict[str, Any]:
    if not url:
        return {"ok": False, "message": "URL 不能为空", "detail": ""}
    try:
        from chayuan.server.shared.deps import ensure_pkg
        ensure_pkg("redis", "redis>=5.0,<6.0")
        import redis  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "message": (
                "未安装 redis 包，且自动安装失败：" + str(e) +
                "。可手动执行 `pip install 'redis>=5,<6'` 后重试。"
            ),
            "detail": "",
        }

    client = None
    try:
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            decode_responses=True,
        )
        if not client.ping():
            return {
                "ok": False,
                "message": "PING 返回 false",
                "detail": _strip_password_from_url(url),
            }
        version = ""
        try:
            info = client.info("server") or {}
            version = str(info.get("redis_version") or "")
        except Exception:
            # info 权限被收了也没关系，ping 通就足够
            pass
        mode = ""
        try:
            info_all = client.info("replication") or {}
            mode = str(info_all.get("role") or "")
        except Exception:
            pass
        msg_parts = ["PING 成功"]
        if version:
            msg_parts.append(f"Redis {version}")
        if mode:
            msg_parts.append(f"role={mode}")
        return {
            "ok": True,
            "message": "，".join(msg_parts),
            "detail": _strip_password_from_url(url),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "message": f"{type(e).__name__}: {e}",
            "detail": _strip_password_from_url(url),
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render_redis_card(ui, *, mark_restart_needed: Optional[Callable[[], None]] = None) -> None:
    load_res = yaml_store.load_yaml("basic_settings.yaml")
    current_url = str((load_res.doc or {}).get("REDIS_URL") or "").strip()
    values = parse_url(current_url)

    inputs: Dict[str, Any] = {}

    def _collect() -> RedisFormValues:
        def _v(name: str, default: str = "") -> str:
            el = inputs.get(name)
            if el is None:
                return default
            try:
                val = el.value
            except Exception:
                return default
            return "" if val is None else str(val)

        return RedisFormValues(
            scheme=_v("scheme", "redis") or "redis",
            host=_v("host", "127.0.0.1"),
            port=_v("port", "6379"),
            db=_v("db", "0"),
            username=_v("username", ""),
            password=_v("password", ""),
        )

    def _refresh_summary(_=None) -> None:
        try:
            url_label.text = "URL：" + masked_url(_collect())
        except Exception:
            pass

    def _clear_result() -> None:
        if result_row is not None:
            result_row.visible = False

    def _show_busy(message: str, detail: str = "") -> None:
        result_row.visible = True
        result_icon.props("name=hourglass_top color=primary")
        result_text.text = message
        result_detail.text = detail or ""
        result_detail.visible = bool(detail)

    def _show_result(ok: bool, message: str, detail: str = "") -> None:
        result_row.visible = True
        if ok:
            result_icon.props("name=check_circle color=positive")
        else:
            result_icon.props("name=error color=negative")
        result_text.text = message
        result_detail.text = detail or ""
        result_detail.visible = bool(detail)

    def _on_validate() -> None:
        v = _collect()
        url = build_url(v)
        _show_busy("正在 PING Redis……", _strip_password_from_url(url))
        res = validate_connection(url, timeout=3.0)
        _show_result(bool(res.get("ok")), str(res.get("message", "")), str(res.get("detail", "")))

    def _on_save() -> None:
        v = _collect()
        url = build_url(v)
        try:
            _path, _bak, changes = yaml_store.save_updates(
                "basic_settings.yaml", {"REDIS_URL": url}
            )
        except Exception as e:  # noqa: BLE001
            ui.notify(f"保存失败：{type(e).__name__}: {e}", color="negative")
            return

        if changes:
            ui.notify(
                "已保存到 basic_settings.yaml；REDIS_URL 会在重启后被限流 / 队列 / "
                "缓存等模块读取。",
                color="positive", timeout=6000,
            )
            if mark_restart_needed is not None:
                try:
                    mark_restart_needed()
                except Exception:
                    pass
        else:
            ui.notify("URL 未变化", color="info", timeout=3000)

        # 让 /readyz 等后续探活立即走新地址（shared.redis_health 里的 60s 缓存）
        try:
            from chayuan.server.shared.redis_health import invalidate_probe_cache
            invalidate_probe_cache()
        except Exception:
            pass

        # 保存完顺带 ping 一次，给闭环反馈
        res = validate_connection(url, timeout=3.0)
        _show_result(bool(res.get("ok")), str(res.get("message", "")), str(res.get("detail", "")))

    def _on_clear() -> None:
        """清空密码 / username / 或一键清空整条 URL（由按钮调用决定）。"""
        try:
            _path, _bak, changes = yaml_store.save_updates(
                "basic_settings.yaml", {"REDIS_URL": ""}
            )
        except Exception as e:  # noqa: BLE001
            ui.notify(f"清空失败：{type(e).__name__}: {e}", color="negative")
            return
        ui.notify(
            "已清空 REDIS_URL；限流、缓存、队列将退化为单机内存模式。",
            color="warning", timeout=5000,
        )
        if mark_restart_needed is not None:
            try:
                mark_restart_needed()
            except Exception:
                pass
        try:
            from chayuan.server.shared.redis_health import invalidate_probe_cache
            invalidate_probe_cache()
        except Exception:
            pass
        _show_result(False, "已清空", "")

    # ---- 布局 -----------------------------------------------------------

    with ui.card().classes("w-full q-mb-sm q-pa-md"):
        with ui.row().classes("items-center w-full no-wrap q-mb-sm").style("gap:12px"):
            ui.label("Redis 连接").classes("text-base font-semibold")
            ui.space()
            url_label = ui.label("URL：" + masked_url(values)).classes(
                "text-xs text-grey-7 font-mono"
            ).style("word-break: break-all; max-width: 420px; text-align: right")

        # 一句话最佳实践，点进字段表单前就能看到核心建议
        ui.label(
            "最佳实践：公网或跨主机走 rediss（TLS）；同主机部署用 redis://127.0.0.1:6379；"
            "Redis 6.0+ 强烈建议使用 ACL 用户名而非仅 requirepass，便于审计；"
            "多个服务共用同一实例时用不同 db 编号隔离。"
        ).classes("text-xs text-grey-7 q-mb-sm").style("white-space: pre-line")

        with ui.grid(columns=3).classes("w-full q-gutter-sm"):
            scheme_el = (
                ui.select(
                    ["redis", "rediss"],
                    label="协议 scheme",
                    value=values.scheme if values.scheme in ("redis", "rediss") else "redis",
                )
                .props("outlined dense")
            )
            scheme_el.tooltip(
                "redis = 明文 TCP；rediss = TLS 加密。公网/云服务、或走反代时用 rediss。"
            )
            inputs["scheme"] = scheme_el

            host_el = (
                ui.input(label="主机 IP / host", value=values.host or "127.0.0.1")
                .props("outlined dense")
            )
            host_el.tooltip(
                "Redis Server 的 IP 或域名；本机部署填 127.0.0.1，"
                "Docker 里跨容器访问时填 service 名。"
            )
            inputs["host"] = host_el

            port_el = (
                ui.input(label="端口 port", value=values.port or "6379")
                .props("outlined dense")
            )
            port_el.tooltip("默认 6379。云厂商托管版有时是 6380（rediss）。")
            inputs["port"] = port_el

            db_el = (
                ui.input(label="数据库 database（db 编号）", value=values.db or "0")
                .props("outlined dense")
            )
            db_el.tooltip(
                "0-15（未改 databases 配置时）。多个服务共用同一实例时建议"
                "各占一个 db 编号做隔离，避免 key 冲突。"
            )
            inputs["db"] = db_el

            user_el = (
                ui.input(label="用户名 username（可选）", value=values.username or "")
                .props("outlined dense")
            )
            user_el.tooltip(
                "Redis 6.0+ ACL 用户名；未开启 ACL / 仅使用 requirepass 时留空，"
                "实际连接会被当作 default 用户。"
            )
            inputs["username"] = user_el

            pwd_el = (
                ui.input(
                    label="密码 password（可选）",
                    value=values.password or "",
                    password=True,
                    password_toggle_button=True,
                )
                .props("outlined dense")
            )
            pwd_el.tooltip(
                "密码中的特殊字符会自动 URL-encode，无需手动转义；"
                "点眼睛图标可临时查看明文便于核对，保存到 yaml 时以 URL 形式落盘。"
            )
            inputs["password"] = pwd_el

        for el in inputs.values():
            el.classes("w-full")
            try:
                el.on("update:model-value", _refresh_summary)
            except Exception:
                pass

        with ui.row().classes("items-center w-full no-wrap q-mt-sm").style("gap:8px"):
            result_row = ui.row().classes("items-center").style(
                "flex: 1; min-width: 0; gap: 6px;"
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

            ui.space()
            ui.button("清空 URL", icon="delete", on_click=_on_clear).props(
                "flat dense color=grey-7"
            ).tooltip("把 REDIS_URL 置空；限流 / 缓存 / 队列会退化到单机降级模式。")
            ui.button("验证连接", icon="bolt", on_click=_on_validate).props(
                "color=secondary dense"
            ).tooltip("实际发起一次 PING，默认 3 秒超时；成功会显示 Redis 版本与角色。")
            ui.button("保存", icon="save", on_click=_on_save).props(
                "color=primary dense"
            ).tooltip("把组装好的 URL 写入 basic_settings.yaml:REDIS_URL，多数模块需重启生效。")

        # 进页时按当前已保存的 URL 跑一次轻量探活（用 shared.redis_health 的 60s 缓存），
        # 让用户一眼看到"当前是否已连通"，再决定改不改。
        if current_url:
            try:
                from chayuan.server.shared.redis_health import sync_probe
                ok, reason = sync_probe(timeout=2.0)
                _show_result(
                    ok,
                    ("当前已连通：" if ok else "当前不可用：") + reason,
                    _strip_password_from_url(current_url),
                )
            except Exception:  # noqa: BLE001
                pass
        else:
            _show_result(
                False,
                "REDIS_URL 尚未配置",
                "未配置 Redis 时限流 / 缓存 / 队列会退化为单机内存模式",
            )
