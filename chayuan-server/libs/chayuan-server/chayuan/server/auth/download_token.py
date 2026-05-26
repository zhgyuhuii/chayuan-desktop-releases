"""下载短期 token（plan v1.3 §4.5）。

设计要点：
* JWT payload：``{u, k, f, aud, scope:"kb_download", exp:30m}``
  - ``u`` 主体 id：人类用户为 ``int(user_id)``，应用账号为 ``"app:<app_id>"``
  - ``k`` knowledge_base_name
  - ``f`` file_name
  - ``aud`` 与 ``u`` 一致（冗余存放便于审计 + 防 token 跨域复用）
  - ``scope`` 固定 ``"kb_download"``，不允许复用其它 token 类型
* 复用 ``auth/tokens.py`` 的签名密钥（_get_secret），不引入新 secret 管理。
* ``/knowledge_base/download_doc`` 已经在 ``AuthMiddleware._QUERY_TOKEN_ALLOWED_PATHS``
  白名单内，浏览器 ``<a download href="...?token=<dl_token>">`` 直接放行。
* 但白名单只校验"是 access JWT"，downloadtoken 是不同 type，所以下载端点要在 endpoint
  里再调 ``verify_download(token, current_subject)`` 严格匹配 aud。

使用流程：
  search_batch 服务端 → for each chunk, attach download_token = sign_download(user, kb, file)
  前端拼 URL：/knowledge_base/download_doc?token=<dl_token>
                        ↑ AuthMiddleware 看到 ?token=...，但因为不是 access JWT
                          会被 decode_token(expected_type="access") 拒绝 → state.user=None
                        ↓ endpoint 再调 verify_download(token) → 返回 dict 或 raise
                        ↓ 用 dict.u 校验，签发完原样下载
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

from chayuan.server.auth.tokens import TokenError, create_token, decode_token

logger = logging.getLogger("chayuan.auth.download_token")

DOWNLOAD_TOKEN_TYPE = "kb_download"
DEFAULT_TTL_SECONDS = 30 * 60  # 30 分钟


def _subject_id(user_or_app_id: Union[Dict[str, Any], Any, str, int]) -> str:
    """统一把"主体"压成字符串 id（人类 = 数字字符串；App = 'app:xxx'）。"""
    if isinstance(user_or_app_id, str):
        return user_or_app_id
    if isinstance(user_or_app_id, int):
        return str(user_or_app_id)
    if isinstance(user_or_app_id, dict):
        uid = user_or_app_id.get("id")
        if isinstance(uid, str):
            return uid
        if isinstance(uid, int):
            return str(uid)
        # AppSpec 经合成后 uid 一定是 'app:xxx' 字符串；这里兜底
        app_id = user_or_app_id.get("app_id")
        if app_id:
            return f"app:{app_id}"
    # AppSpec / 其它对象
    aid = getattr(user_or_app_id, "app_id", None)
    if aid:
        return f"app:{aid}"
    raise ValueError("cannot derive subject id from %r" % (user_or_app_id,))


def sign_download(
    user_or_app_id: Union[Dict[str, Any], Any, str, int],
    kb_name: str,
    file_name: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """签发一个下载 token；30 分钟 TTL。

    callsite 例子：
        token = sign_download(user, "合同模板库", "买卖协议-v3.docx")
        url = f"/knowledge_base/download_doc?knowledge_base_name=...&file_name=...&token={token}"
    """
    if not kb_name or not file_name:
        raise ValueError("kb_name and file_name are required")
    sub = _subject_id(user_or_app_id)
    return create_token(
        {
            "sub": sub,
            "u": sub,        # 与 sub 重复但语义更明确（u=user/owner of token）
            "aud": sub,      # 防跨主体复用
            "k": kb_name,
            "f": file_name,
            "scope": DOWNLOAD_TOKEN_TYPE,
        },
        ttl_seconds=int(ttl_seconds),
        token_type=DOWNLOAD_TOKEN_TYPE,
    )


def verify_download(
    token: str,
    *,
    expected_subject: Union[Dict[str, Any], Any, str, int, None] = None,
    expected_kb: Optional[str] = None,
    expected_file: Optional[str] = None,
) -> Dict[str, Any]:
    """校验 token 并可选地强制匹配 subject/kb/file；返回 payload dict。

    抛 ``TokenError``（来自 ``auth/tokens.decode_token``）：
      - 签名错 / 过期 / 类型不对（不是 ``kb_download``）
      - aud 不匹配 expected_subject
      - kb/file 不匹配 expected_kb / expected_file
    """
    decoded = decode_token(token, expected_type=DOWNLOAD_TOKEN_TYPE)
    payload = decoded.payload or {}

    aud = str(payload.get("aud") or payload.get("u") or "")
    kb = str(payload.get("k") or "")
    fn = str(payload.get("f") or "")

    if expected_subject is not None:
        want = _subject_id(expected_subject)
        if aud != want:
            raise TokenError(f"download token aud mismatch: token={aud!r} expected={want!r}")

    if expected_kb is not None and kb != str(expected_kb):
        raise TokenError(f"download token kb mismatch: token={kb!r} expected={expected_kb!r}")

    if expected_file is not None and fn != str(expected_file):
        raise TokenError(f"download token file mismatch: token={fn!r} expected={expected_file!r}")

    return {"u": aud, "aud": aud, "k": kb, "f": fn, "scope": DOWNLOAD_TOKEN_TYPE,
            "exp": payload.get("exp"), "iat": payload.get("iat")}


def is_download_token(token: str) -> bool:
    """轻量判断；用于 endpoint 区分 ``?token=`` 是 access JWT 还是 download token。"""
    if not token or token.count(".") != 2:
        return False
    try:
        decode_token(token, expected_type=DOWNLOAD_TOKEN_TYPE)
        return True
    except TokenError:
        return False
