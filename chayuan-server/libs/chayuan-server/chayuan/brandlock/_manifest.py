"""Manifest 生成 + 加载。

打包流程
==========
1. 维护者(发版人)有一对 Ed25519 密钥(私钥保密,公钥嵌入代码)
2. 打包时跑 ``python -m chayuan.brandlock._manifest --gen --priv key.pem``:
   - 扫描 ``PROTECTED_PATHS`` 下所有 .py / .png / .svg / 文案文件
   - 计算每个文件的 SHA256
   - 收集成 ``manifest.json``
   - 用私钥对 manifest.json 整体签名 → ``manifest.sig``
3. 把 ``manifest.json`` + ``manifest.sig`` 打包进 wheel

运行流程
==========
1. 启动时读 ``manifest.json`` + ``manifest.sig``
2. 用嵌入的 PUBLIC_KEY 验签
3. 验签通过 → 逐个文件 hash 校验
4. 任一不匹配 → ``tampered=True``

为什么不能直接跑 ``hashlib.sha256(open(__file__).read())``
==============================================================
* 用户改 ``manifest.json``(改 hash 值)→ 验签失败
* 用户改 ``manifest.sig``(伪造签名)→ 没私钥,签不出来
* 用户改 ``_verifier.py`` 让它返回 ``not tampered`` → ``_verifier.py`` 自身在
  protected list,触发自身校验失败 → tampered
* 用户改 ``_manifest.py`` 里的 PUBLIC_KEY → 同上,自身在 protected list

唯一能完美绕过的方法:把 PUBLIC_KEY 改成攻击者自己生成的对(对应改 manifest +
重签)— 但需要用户能编辑 site-packages 里的字节码,改完只对 1 个客户有效,改完
还会触发 5 个 canary。综合成本远高于守法。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("chayuan.brandlock.manifest")


# ============================================================================
# 公钥(分散嵌入,运行时拼接 — 简单防 sed-replace 攻击)
# ============================================================================
# 用 Ed25519 公钥;部分字节按位置散布,启动时 ``_assemble_public_key()`` 拼回
# (用户用 grep "BEGIN PUBLIC KEY" 找不到完整连续字符串)
#
# 真实生产环境:
#   1. 维护者生成 Ed25519 密钥对(``cryptography.hazmat.primitives.asymmetric.ed25519``)
#   2. 私钥保密(打包机器 + 离线备份)
#   3. 公钥(32 字节)按下面顺序嵌入,绝不暴露明文 PEM
#
# 当前是占位 — 用户需替换成自己生成的真实公钥的 32 字节
_PK_PARTS: Dict[str, bytes] = {
    "a": b"\x00" * 8,   # 占位 — 生产替换
    "b": b"\x00" * 8,
    "c": b"\x00" * 8,
    "d": b"\x00" * 8,
}


def _assemble_public_key() -> bytes:
    """拼接公钥;顺序固定 a+b+c+d。"""
    return _PK_PARTS["a"] + _PK_PARTS["b"] + _PK_PARTS["c"] + _PK_PARTS["d"]


# ============================================================================
# 受保护资源清单(打包时扫描这些路径)
# ============================================================================
# 路径相对于 ``chayuan/`` 包根
PROTECTED_PATHS: List[str] = [
    # 图标 / 商标 — 改了一定是侵权
    "img/logo.png",
    "img/logo_name.png",

    # 关键文案位置(含 "察元 / Chayuan" 字面量)
    "init_wizard.py",
    "startup.py",
    "cli.py",

    # brandlock 自身 — 改任意一个都会让 verify 失败
    "brandlock/__init__.py",
    "brandlock/_verifier.py",
    "brandlock/_canary.py",
    "brandlock/_evidence.py",
    "brandlock/_manifest.py",
]


# ============================================================================
# Manifest 数据结构
# ============================================================================

@dataclass
class FileEntry:
    """单个受保护文件的 hash 记录。"""
    rel_path: str
    sha256: str
    size: int


@dataclass
class Manifest:
    """完整 manifest — 待签名后嵌入 wheel。"""
    version: str = "1.0"
    files: List[FileEntry] = field(default_factory=list)
    issued_at: str = ""           # ISO 时间戳
    product: str = "chayuan"
    signature_hex: str = ""        # Ed25519 签名(对 files+issued_at+product 序列化后签)


def _hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    sz = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
            sz += len(chunk)
    return h.hexdigest(), sz


def _serialize_for_sign(m: Manifest) -> bytes:
    """打包/签名时,稳定序列化 manifest(不含 signature_hex)。"""
    body = {
        "version": m.version,
        "product": m.product,
        "issued_at": m.issued_at,
        "files": [
            {"rel_path": e.rel_path, "sha256": e.sha256, "size": e.size}
            for e in m.files
        ],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def generate_manifest(
    package_root: Path,
    *,
    private_key_path: Optional[Path] = None,
) -> Manifest:
    """打包工具:扫描 ``PROTECTED_PATHS``,生成 manifest 并签名。

    Args:
        package_root: ``chayuan/`` 包根路径
        private_key_path: Ed25519 私钥(PEM)路径;不给时只生成无签名 manifest
    """
    import datetime

    m = Manifest(issued_at=datetime.datetime.utcnow().isoformat() + "Z")
    for rel in PROTECTED_PATHS:
        p = package_root / rel
        if not p.exists():
            logger.warning("[manifest gen] missing: %s", rel)
            continue
        sha, sz = _hash_file(p)
        m.files.append(FileEntry(rel_path=rel, sha256=sha, size=sz))

    if private_key_path:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
            from cryptography.hazmat.primitives import serialization as _ser
            with open(private_key_path, "rb") as f:
                priv = _ser.load_pem_private_key(f.read(), password=None)
            assert isinstance(priv, Ed25519PrivateKey)
            sig = priv.sign(_serialize_for_sign(m))
            m.signature_hex = sig.hex()
        except Exception as e:  # noqa: BLE001
            logger.error("[manifest gen] sign failed: %r", e)
    return m


def manifest_to_json(m: Manifest) -> str:
    """转 JSON(含 signature)— 打包时写到 ``brandlock/manifest.json``。"""
    return json.dumps(
        {
            "version": m.version,
            "product": m.product,
            "issued_at": m.issued_at,
            "files": [
                {"rel_path": e.rel_path, "sha256": e.sha256, "size": e.size}
                for e in m.files
            ],
            "signature_hex": m.signature_hex,
        },
        sort_keys=True, indent=2, ensure_ascii=False,
    )


def load_manifest_from_json(text: str) -> Manifest:
    """运行时读 manifest.json。"""
    data = json.loads(text)
    return Manifest(
        version=data.get("version", "1.0"),
        product=data.get("product", "chayuan"),
        issued_at=data.get("issued_at", ""),
        files=[
            FileEntry(
                rel_path=f["rel_path"],
                sha256=f["sha256"],
                size=int(f.get("size", 0)),
            )
            for f in data.get("files", [])
        ],
        signature_hex=data.get("signature_hex", ""),
    )


def verify_signature(m: Manifest) -> bool:
    """用嵌入的公钥验 manifest 签名;占位公钥时永远返 True(开发期)。"""
    pk = _assemble_public_key()
    if pk == b"\x00" * 32:
        # 占位公钥 — 开发期跳过签名校验
        logger.debug("[manifest verify] placeholder public key, skip sig check")
        return True
    if not m.signature_hex:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
        public = Ed25519PublicKey.from_public_bytes(pk)
        public.verify(bytes.fromhex(m.signature_hex), _serialize_for_sign(m))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[manifest verify] signature verify failed: %r", e)
        return False


# ============================================================================
# CLI(打包时用)
# ============================================================================

def _cli_main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Brandlock manifest 工具")
    p.add_argument("--gen", action="store_true", help="生成 manifest 并写入 brandlock/manifest.json")
    p.add_argument("--priv", help="Ed25519 私钥 PEM 路径")
    p.add_argument("--pkg-root", default=None,
                   help="包根路径;默认自动定位到 chayuan/")
    args = p.parse_args()

    if args.gen:
        if args.pkg_root:
            root = Path(args.pkg_root)
        else:
            root = Path(__file__).resolve().parent.parent
        m = generate_manifest(
            root,
            private_key_path=Path(args.priv) if args.priv else None,
        )
        out = root / "brandlock" / "manifest.json"
        out.write_text(manifest_to_json(m), encoding="utf-8")
        print(f"✓ generated: {out}")
        print(f"  protected files: {len(m.files)}")
        print(f"  signed: {bool(m.signature_hex)}")


if __name__ == "__main__":
    _cli_main()
