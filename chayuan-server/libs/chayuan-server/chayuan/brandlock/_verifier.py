"""运行时校验 — 启动时调一次,业务可主动 verify_now() 重检。

设计要点
==========
* **不阻断运行** — 检测到改动只 log 记录 + 状态暴露,不让 chayuan 罢工
  (避免合法部署的"客户改了 logo"也跑不起来)
* **5min 缓存** — 校验本身要 hash 几十文件,频繁调耗时
* **canary 间接调** — 业务代码不直调 verify_now,而是调 ``_canary.run_once()``
  让校验调用看起来不集中 → 攻击者难定位
* **抛错路径** — ``assert_brand_intact()`` 给关键路径(如商业模块加载)用,
  其它正常调用走 ``is_tampered()`` 不抛错
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from chayuan.brandlock._manifest import (
    Manifest,
    load_manifest_from_json,
    verify_signature,
)

logger = logging.getLogger("chayuan.brandlock.verifier")


class BrandTamperedError(RuntimeError):
    """品牌资源被篡改 — 仅在 ``assert_brand_intact()`` 时抛。"""


@dataclass
class _State:
    last_check_at: float = 0.0
    is_tampered: bool = False
    manifest_signed: bool = False
    manifest_present: bool = False
    failed_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    fingerprint: str = ""


_STATE = _State()
_STATE_LOCK = threading.Lock()
_CHECK_TTL = 300.0  # 5 min


def _package_root() -> Path:
    """``chayuan/`` 包根路径。"""
    return Path(__file__).resolve().parent.parent


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
    except Exception:  # noqa: BLE001
        return ""
    return h.hexdigest()


def _do_verify() -> _State:
    """执行一次完整校验,返回新状态。"""
    new_state = _State(last_check_at=time.time())
    root = _package_root()

    manifest_file = root / "brandlock" / "manifest.json"
    if not manifest_file.exists():
        # manifest 不存在:开发期 / 用户删了
        # 不算 tampered(开发期允许),但记录"未保护"状态
        logger.debug("[brandlock] manifest.json not found, skip verify")
        new_state.manifest_present = False
        return new_state

    new_state.manifest_present = True
    try:
        m: Manifest = load_manifest_from_json(
            manifest_file.read_text(encoding="utf-8")
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[brandlock] manifest parse failed: %r", e)
        new_state.is_tampered = True
        new_state.failed_files = ["brandlock/manifest.json"]
        return new_state

    # 1. 验签 — 公钥嵌入,签名错 = 改过 manifest
    new_state.manifest_signed = verify_signature(m)
    if not new_state.manifest_signed:
        logger.warning(
            "[brandlock] manifest signature mismatch (modified or unsigned)",
        )
        new_state.is_tampered = True
        new_state.failed_files.append("brandlock/manifest.json (signature)")
        # 即使签名失败仍继续 hash 校验,给更详细的证据

    # 2. 逐文件 hash 校验
    for entry in m.files:
        p = root / entry.rel_path
        if not p.exists():
            new_state.missing_files.append(entry.rel_path)
            new_state.is_tampered = True
            continue
        actual = _hash_file(p)
        if actual != entry.sha256:
            new_state.failed_files.append(entry.rel_path)
            new_state.is_tampered = True

    # 3. 计算 fingerprint(用于 telemetry / 取证)
    fp_parts = sorted(set(new_state.failed_files + new_state.missing_files))
    new_state.fingerprint = hashlib.sha1(
        "\x00".join(fp_parts).encode("utf-8")
    ).hexdigest()[:12] if fp_parts else "intact"

    if new_state.is_tampered:
        logger.warning(
            "[brandlock] TAMPERED detected (fp=%s, failed=%d, missing=%d): %s",
            new_state.fingerprint,
            len(new_state.failed_files),
            len(new_state.missing_files),
            (new_state.failed_files + new_state.missing_files)[:5],
        )
    else:
        logger.info("[brandlock] verified intact (fp=intact)")
    return new_state


def verify_now(*, force: bool = False) -> bool:
    """主动重新校验。返回 True=完好,False=已篡改。

    带 5min 缓存,频繁调不重复 hash;``force=True`` 跳过缓存。
    """
    with _STATE_LOCK:
        if not force and (time.time() - _STATE.last_check_at) < _CHECK_TTL:
            return not _STATE.is_tampered
    new = _do_verify()
    with _STATE_LOCK:
        _STATE.last_check_at = new.last_check_at
        _STATE.is_tampered = new.is_tampered
        _STATE.manifest_signed = new.manifest_signed
        _STATE.manifest_present = new.manifest_present
        _STATE.failed_files = new.failed_files
        _STATE.missing_files = new.missing_files
        _STATE.fingerprint = new.fingerprint
    return not new.is_tampered


def is_tampered() -> bool:
    """返回 True = 检测到改动;False = 完好(或 manifest 不存在)。"""
    with _STATE_LOCK:
        return _STATE.is_tampered


def get_tamper_evidence() -> Dict[str, object]:
    """详细证据 — 给 API header / 状态卡 / telemetry 上报用。"""
    with _STATE_LOCK:
        return {
            "tampered": _STATE.is_tampered,
            "fingerprint": _STATE.fingerprint,
            "failed_files": list(_STATE.failed_files),
            "missing_files": list(_STATE.missing_files),
            "manifest_present": _STATE.manifest_present,
            "manifest_signed": _STATE.manifest_signed,
            "last_check_ts": _STATE.last_check_at,
        }


def assert_brand_intact() -> None:
    """完好则正常返回,否则抛 BrandTamperedError。

    适合放在"商业模块"加载入口,让侵权部署直接拒服。慎用 — 会阻断业务。
    """
    verify_now()
    if is_tampered():
        ev = get_tamper_evidence()
        raise BrandTamperedError(
            f"察元 品牌资源被篡改(fp={ev['fingerprint']});"
            f"如您是合法授权用户,请联系商务签订商标授权许可。"
            f"详情:docs/contributing/README_dev.md"
        )


# ============================================================================
# 启动期初始化
# ============================================================================
# import 本模块时立即跑一次 verify(给 cli.py / startup.py 全自动)。
# 失败也不抛,后续业务可读 is_tampered() 决定行为。
try:
    verify_now()
except Exception as e:  # noqa: BLE001
    logger.debug("[brandlock] initial verify failed: %r", e)
