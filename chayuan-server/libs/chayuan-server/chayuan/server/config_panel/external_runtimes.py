"""92-1:外置 runtime endpoint 配置。

业务背景
========
ComfyUI / Infinity / vLLM 这类 docker 类 framework,用户可能选择两种部署模式:

A. **本机自动装**:由 chayuan 的【一键安装】触发 docker compose up 起容器,
   chayuan 探活自身容器(走 ``docker compose ps``)。

B. **外置已部署**:用户在自己 GPU 服务器 / 别的容器编排系统(k8s / portainer)
   里跑了 Infinity / ComfyUI,只想在 chayuan 配一个 endpoint URL。
   此时 chayuan **不需要起 docker**,只要 HTTP ping 通就算 ready。

92 题之前只支持模式 A 的状态判定。本模块给模式 B 提供一个**面向用户的、独立于
docker compose 的**外置 endpoint 注册表。

文件位置
========
``<CHAYUAN_ROOT>/external_runtimes.yaml``

格式::

    runtimes:
      infinity:
        url: http://10.0.0.5:7997
        health_path: /health   # 可省,从 _FrameworkSpec 默认拿
        enabled: true
      comfyui:
        url: http://192.168.1.100:18188
        enabled: true
      vllm:
        url: ""
        enabled: false   # 暂时停用但保留配置

API
===
* :func:`get_external_url(name)`         读取(已拼好 health 探针 url)
* :func:`set_external_url(name, url, ...)` 写入
* :func:`list_external_runtimes()`       列出所有
* :func:`probe_external(url, ...)`       同步 HTTP 探活
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from chayuan.server.config_panel import yaml_store

logger = logging.getLogger("chayuan.config_panel.external_runtimes")

# 该 yaml 的文件名(yaml_store 拼 CHAYUAN_ROOT 找路径)
_FILE = "external_runtimes.yaml"

# 92-1:CHAYUAN_ROOT 改后内存缓存清掉,强制重读
_CACHE: Dict[str, Any] = {}


def _load_doc() -> Dict[str, Any]:
    """读 external_runtimes.yaml;不存在或解析失败返 {}。"""
    try:
        result = yaml_store.load_yaml(_FILE)
        doc = result.doc if result and result.doc else {}
        if not isinstance(doc, dict):
            return {}
        return doc
    except Exception as e:  # noqa: BLE001
        logger.debug("[external_runtimes] load_yaml failed: %r", e)
        return {}


def _runtimes_block(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从 doc 拿 ``runtimes`` 段;缺则返空 dict。"""
    blk = doc.get("runtimes")
    if not isinstance(blk, dict):
        return {}
    return blk


def get_external_runtime(name: str) -> Optional[Dict[str, Any]]:
    """返回 ``{url, health_path, enabled}`` 或 None。

    None 表示用户没配 / 已禁用。enabled=false 也返 None,让上层走 docker 路径。
    """
    if not name:
        return None
    doc = _load_doc()
    item = _runtimes_block(doc).get(name)
    if not isinstance(item, dict):
        return None
    if not bool(item.get("enabled", True)):
        return None
    url = str(item.get("url") or "").strip()
    if not url:
        return None
    health_path = str(item.get("health_path") or "").strip()
    return {"url": url, "health_path": health_path, "enabled": True}


def get_external_url(name: str, default_health_path: str = "") -> str:
    """返回**已拼好探针路径**的完整 URL,无配置返空字符串。

    优先用 yaml 里 health_path;空则用调用方传的 default_health_path
    (通常来自 _FrameworkSpec.health_path)。
    """
    item = get_external_runtime(name)
    if not item:
        return ""
    base = item["url"].rstrip("/")
    health = item["health_path"] or default_health_path or ""
    if health and not health.startswith("/"):
        health = "/" + health
    return f"{base}{health}" if health else base


def normalize_url(url: str) -> str:
    """94-1:URL 自动补 schema。

    用户填 ``127.0.0.1:7997`` / ``localhost:7997`` / ``my-server.local:8000`` 这类
    省略 schema 的地址时,自动补 ``http://``。空字符串原样返。
    """
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith(("http://", "https://")):
        return url
    # 去 :// 不全的奇怪写法(如 ``http:127.0.0.1`` / ``://x``)
    if "://" in url:
        return "http://" + url.split("://", 1)[1].lstrip("/")
    return "http://" + url.lstrip("/")


def set_external_url(
    name: str, url: str, *,
    health_path: Optional[str] = None, enabled: bool = True,
) -> Tuple[bool, str]:
    """写入 / 更新一条外置 runtime 配置。返回 ``(ok, message)``。

    URL 自动补 ``http://``(94-1):用户填 ``127.0.0.1:7997`` 也能识别。
    """
    if not name:
        return False, "name required"
    url = normalize_url(url)
    item: Dict[str, Any] = {"url": url, "enabled": bool(enabled)}
    if health_path is not None:
        item["health_path"] = str(health_path).strip()
    try:
        # 读完整 doc → 修改 runtimes[name] → 整段写回(yaml_store.save_updates
        # 是 top-level merge,nested dict 会被覆盖)
        doc = _load_doc()
        runtimes = _runtimes_block(doc)
        runtimes[name] = item
        yaml_store.save_updates(_FILE, {"runtimes": runtimes})
        return True, "已保存"
    except Exception as e:  # noqa: BLE001
        logger.exception("[external_runtimes] set failed")
        return False, f"写盘失败:{type(e).__name__}: {e}"


def delete_external_runtime(name: str) -> Tuple[bool, str]:
    """删除一条配置;不存在视为成功(幂等)。"""
    if not name:
        return False, "name required"
    try:
        doc = _load_doc()
        runtimes = _runtimes_block(doc)
        if name in runtimes:
            del runtimes[name]
            yaml_store.save_updates(_FILE, {"runtimes": runtimes})
        return True, "已删除"
    except Exception as e:  # noqa: BLE001
        return False, f"删除失败:{type(e).__name__}: {e}"


def list_external_runtimes() -> List[Dict[str, Any]]:
    """API 用扁平化 list:``[{name, url, health_path, enabled}, ...]``。"""
    doc = _load_doc()
    out: List[Dict[str, Any]] = []
    for name, item in _runtimes_block(doc).items():
        if not isinstance(item, dict):
            continue
        out.append({
            "name": name,
            "url": str(item.get("url") or "").strip(),
            "health_path": str(item.get("health_path") or "").strip(),
            "enabled": bool(item.get("enabled", True)),
        })
    return out


def probe_external(url: str, *, timeout: float = 1.5) -> Tuple[bool, str]:
    """同步 HTTP 探活返回 ``(ok, detail)``。

    成功 → ``(True, "200")`` 或 ``"<status_code>"``
    失败 → ``(False, "<error>")``;不抛任何异常
    """
    if not url:
        return False, "empty url"
    try:
        import httpx  # type: ignore
    except ImportError:
        return False, "httpx 未安装"
    try:
        r = httpx.get(url, timeout=timeout)
        if r.status_code < 500:
            return True, str(r.status_code)
        return False, f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


__all__ = [
    "get_external_runtime",
    "get_external_url",
    "set_external_url",
    "delete_external_runtime",
    "list_external_runtimes",
    "probe_external",
    "normalize_url",
]
