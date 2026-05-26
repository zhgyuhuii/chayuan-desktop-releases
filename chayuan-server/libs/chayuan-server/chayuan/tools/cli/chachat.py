"""chachat —— 察元服务端的终端对话客户端。

使用：
    $ chachat                          # 走默认 http://127.0.0.1:8000
    $ chachat --host http://foo:9000   # 指定服务端
    $ chachat --model gpt-4            # 预选模型
    $ chachat --kb my_docs             # 预选知识库

对话中斜杠命令：
    /model          切换 LLM（从服务端 /api/models 拉取当前可用列表）
    /kb             切换知识库（/knowledge_base/list_knowledge_bases）
    /new            开一段新对话（重置 conversation_id）
    /history        打印当前会话历史
    /whoami         打印当前登录身份
    /login          (手动) 重新触发 device code 登录
    /logout         删除本地 token
    /help           打印帮助
    /exit, /quit    退出

鉴权：
- 未登录且服务端 ``AUTH_REQUIRED=false`` → 游客模式直聊；
- 需要登录时自动走 device code：打印链接 → 浏览器完成 → CLI 轮询拿 token；
- token 落在 ``$XDG_CONFIG_HOME/chayuan/cli_credentials.json``（或 ``~/.config/chayuan/...``）
  文件权限 chmod 600。

依赖：
- 必需：``httpx``（缺失时启动报错并提示 ``pip install httpx``）；
- 可选：``rich``（美化输出，缺失降级纯文本）。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    __version__ = "0.1.0"
except Exception:  # noqa: BLE001
    __version__ = "0.0.0"


# ---------------------------------------------------------------------------
# 本地凭证存储：XDG_CONFIG_HOME，chmod 600
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    d = Path(base) / "chayuan"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cred_path() -> Path:
    return _config_dir() / "cli_credentials.json"


def load_credentials(host: str) -> Dict[str, Any]:
    p = _cred_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return {}
    # 每 host 一份（支持切服务端）
    return (data.get(host) or {}) if isinstance(data, dict) else {}


def save_credentials(host: str, creds: Dict[str, Any]) -> None:
    p = _cred_path()
    data: Dict[str, Any] = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8") or "{}") or {}
        except Exception:  # noqa: BLE001
            data = {}
    data[host] = creds
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except Exception:  # noqa: BLE001
        pass


def clear_credentials(host: str) -> None:
    p = _cred_path()
    if not p.exists():
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}") or {}
    except Exception:  # noqa: BLE001
        data = {}
    data.pop(host, None)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# HTTP 客户端薄包装
# ---------------------------------------------------------------------------

def _require_httpx():
    try:
        import httpx  # noqa: F401
        return httpx
    except ImportError:
        sys.stderr.write(
            "chachat 需要 httpx。请执行：pip install httpx\n"
            "（或 `pip install 'chayuan-server[cli]'` 获取完整 CLI 依赖组合）\n"
        )
        raise SystemExit(2)


class Client:
    def __init__(self, host: str, access_token: Optional[str] = None):
        self.host = host.rstrip("/")
        self.access_token = access_token or ""
        httpx = _require_httpx()
        self._httpx = httpx
        self._c = httpx.Client(timeout=60.0)

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:  # noqa: BLE001
            pass

    # ----- 鉴权 -----

    def device_start(self) -> Dict[str, Any]:
        r = self._c.post(f"{self.host}/cli/device/start", headers=self._hdrs())
        r.raise_for_status()
        return r.json()

    def device_token(self, device_code: str) -> Dict[str, Any]:
        r = self._c.post(
            f"{self.host}/cli/device/token",
            json={"device_code": device_code},
            headers=self._hdrs(),
        )
        # 202 / 410 均非 200，需要区分
        return {"status_code": r.status_code, "body": r.json() if r.content else {}}

    # ----- 资源 -----

    def list_models(self) -> List[str]:
        try:
            r = self._c.get(f"{self.host}/api/models", headers=self._hdrs())
            r.raise_for_status()
            data = r.json() or {}
            # 兼容多种返回结构
            if isinstance(data, list):
                return [str(x) for x in data]
            if isinstance(data, dict):
                for key in ("data", "models", "items"):
                    v = data.get(key)
                    if isinstance(v, list):
                        return [
                            (x.get("id") or x.get("name") or str(x))
                            if isinstance(x, dict) else str(x)
                            for x in v
                        ]
            return []
        except Exception:  # noqa: BLE001
            return []

    def list_kbs(self) -> List[str]:
        # 这个接口在老版本路由是 GET /knowledge_base/list_knowledge_bases
        try:
            r = self._c.get(
                f"{self.host}/knowledge_base/list_knowledge_bases",
                headers=self._hdrs(),
            )
            r.raise_for_status()
            body = r.json() or {}
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, list):
                return [str(x) for x in data]
            return []
        except Exception:  # noqa: BLE001
            return []

    # ----- 对话（流式） -----

    def stream_chat(self, payload: Dict[str, Any]):
        """SSE 流式 yield (event, data_json)。"""
        headers = self._hdrs()
        headers["X-Client"] = f"chachat/{__version__}"
        headers["Accept"] = "text/event-stream"
        with self._c.stream(
            "POST", f"{self.host}/chat/v2/chat",
            json=payload, headers=headers, timeout=None,
        ) as r:
            if r.status_code >= 400:
                try:
                    err = r.read().decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    err = f"HTTP {r.status_code}"
                yield ("error", err)
                return
            for line in r.iter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        yield ("data", json.loads(raw))
                    except Exception:
                        yield ("data", {"text": raw})

    def _hdrs(self) -> Dict[str, str]:
        h: Dict[str, str] = {"X-Client": f"chachat/{__version__}"}
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h


# ---------------------------------------------------------------------------
# 鉴权流
# ---------------------------------------------------------------------------

def _maybe_login_flow(client: Client) -> Dict[str, Any]:
    """device code 流：开链接 → 轮询 → 落凭证。"""
    start = client.device_start()
    uri = start.get("verification_uri", "")
    user_code = start.get("user_code", "")
    device_code = start.get("device_code", "")
    interval = int(start.get("interval") or 3)
    expires_in = int(start.get("expires_in") or 900)

    print("未登录，请在浏览器打开以下链接完成登录：\n")
    print(f"  {uri}\n")
    print(f"验证码：{user_code}")
    print(f"CLI 每 {interval}s 轮询中... (Ctrl-C 退出)\n")

    t0 = time.time()
    while time.time() - t0 < expires_in:
        try:
            res = client.device_token(device_code)
        except KeyboardInterrupt:
            print("已取消登录。")
            return {}
        except Exception as e:  # noqa: BLE001
            print(f"轮询异常（{e}），{interval}s 后再试", file=sys.stderr)
            time.sleep(interval)
            continue
        sc = res.get("status_code")
        body = res.get("body") or {}
        if sc == 200 and body.get("access_token"):
            print("✔ 登录成功。")
            return body
        if sc in (202,) and (body.get("error") == "authorization_pending"):
            time.sleep(interval)
            continue
        if sc == 410 or body.get("error") == "expired_token":
            print("设备码过期，请重试 `chachat /login`。", file=sys.stderr)
            return {}
        time.sleep(interval)
    print("登录超时。", file=sys.stderr)
    return {}


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

HELP_TEXT = """\
斜杠命令：
  /model [id]     切换 LLM；不带参数列可用；留空回到默认
  /kb [name]      切换知识库；不带参数列可用；留空关掉 kb 检索
  /new            开一段新 conversation
  /history        打印当前会话历史
  /whoami         打印登录态
  /login          重新登录
  /logout         清本地 token
  /help, /?       显示此帮助
  /exit, /quit    退出
"""


def _print_banner(host: str, mode: str, model: str, kb: str) -> None:
    kb_str = kb if kb else "none"
    model_str = model if model else "default"
    print(f"chachat v{__version__} · {mode} · host={host} · model={model_str} · kb={kb_str}")
    print("输入 /help 查看命令，Ctrl-C 中断本轮流式（不退出），/exit 退出。\n")


def _extract_text(payload: Any) -> str:
    """/chat/v2/chat 的 SSE chunk 结构容忍多种形状：
    - {"type":"message","text":"..."}
    - {"chunk":"..."}
    - {"delta":{"content":"..."}}
    - {"content":"..."}
    - {"data":{"text":"..."}}
    """
    if not isinstance(payload, dict):
        return str(payload)
    for key in ("text", "chunk", "content"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
    delta = payload.get("delta")
    if isinstance(delta, dict):
        v = delta.get("content") or delta.get("text")
        if isinstance(v, str) and v:
            return v
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("text", "chunk", "content"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
    return ""


class Repl:
    def __init__(self, client: Client, host: str, model: str, kb: str, user: Dict[str, Any]):
        self.client = client
        self.host = host
        self.model = model or ""
        self.kb = kb or ""
        self.user = user or {}
        self.history: List[Dict[str, str]] = []
        self.conversation_id = ""
        self._interrupt = False

        signal.signal(signal.SIGINT, self._on_sigint)

    def _on_sigint(self, *_):
        # Ctrl-C：只中断当前流式，不退出
        self._interrupt = True
        print("\n[已中断当前回答]")

    def run(self) -> None:
        while True:
            try:
                raw = input("> ")
            except (EOFError, KeyboardInterrupt):
                print()
                return
            line = raw.strip()
            if not line:
                continue
            if line.startswith("/"):
                if self._handle_slash(line):
                    return
                continue
            self._handle_chat(line)

    # ----- slash -----

    def _handle_slash(self, line: str) -> bool:
        """返回 True 表示退出 REPL。"""
        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("/exit", "/quit"):
            return True
        if cmd in ("/help", "/?"):
            print(HELP_TEXT)
            return False
        if cmd == "/new":
            self.history.clear()
            self.conversation_id = ""
            print("已开启新会话。")
            return False
        if cmd == "/history":
            for h in self.history:
                role = h.get("role", "user")
                print(f"[{role}] {h.get('content','')}")
            return False
        if cmd == "/whoami":
            print(json.dumps(self.user or {"mode": "guest"}, ensure_ascii=False, indent=2))
            return False
        if cmd == "/logout":
            clear_credentials(self.host)
            self.user = {}
            self.client.access_token = ""
            print("已清除本地凭证。下一次 /login 或对话若需鉴权会重新走 device code。")
            return False
        if cmd == "/login":
            tok = _maybe_login_flow(self.client)
            if tok.get("access_token"):
                self.client.access_token = tok["access_token"]
                self.user = tok.get("user") or {}
                save_credentials(self.host, {
                    "access_token": tok["access_token"],
                    "refresh_token": tok.get("refresh_token") or "",
                    "user": self.user,
                })
            return False
        if cmd == "/model":
            if not arg:
                mods = self.client.list_models()
                if not mods:
                    print("服务端未返回模型列表；可手工 /model <id>。")
                else:
                    print("可用模型：")
                    for m in mods:
                        print(f"  - {m}")
                return False
            self.model = arg
            print(f"已切到模型：{arg}")
            return False
        if cmd == "/kb":
            if not arg:
                kbs = self.client.list_kbs()
                if not kbs:
                    print("服务端未返回知识库列表；可手工 /kb <name>。")
                else:
                    print("可用知识库：")
                    for k in kbs:
                        print(f"  - {k}")
                return False
            if arg.lower() in ("off", "none", "-"):
                self.kb = ""
                print("已关闭知识库。")
            else:
                self.kb = arg
                print(f"已切到知识库：{arg}")
            return False
        print(f"未知命令：{cmd}；/help 查看全部。")
        return False

    # ----- chat -----

    def _handle_chat(self, query: str) -> None:
        self._interrupt = False
        self.history.append({"role": "user", "content": query})
        payload: Dict[str, Any] = {
            "query": query,
            "history": self.history[:-1],  # 不重复带当前这条
            "stream": True,
            "conversation_id": self.conversation_id,
        }
        if self.model:
            payload["model"] = self.model
        if self.kb:
            payload["mode"] = "kb"
            payload["kb_name"] = self.kb
        # 流式打印
        full_answer: List[str] = []
        try:
            for event, data in self.client.stream_chat(payload):
                if self._interrupt:
                    break
                if event == "error":
                    print(f"\n[服务端错误] {data}", file=sys.stderr)
                    return
                txt = _extract_text(data)
                if txt:
                    sys.stdout.write(txt)
                    sys.stdout.flush()
                    full_answer.append(txt)
                if isinstance(data, dict) and data.get("conversation_id"):
                    self.conversation_id = str(data["conversation_id"])
        except Exception as e:  # noqa: BLE001
            print(f"\n[传输异常] {e}", file=sys.stderr)
            return
        print()  # 换行结束本轮
        if full_answer:
            self.history.append({"role": "assistant", "content": "".join(full_answer)})


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="chachat", description="察元察元察元 CLI 对话客户端",
    )
    p.add_argument(
        "--host", default=os.environ.get("CHACHAT_HOST") or "http://127.0.0.1:8000",
        help="chayuan 服务端地址（默认 http://127.0.0.1:8000）",
    )
    p.add_argument("--model", default="", help="预选 LLM id")
    p.add_argument("--kb", default="", help="预选知识库名")
    p.add_argument("--no-auth", action="store_true",
                   help="跳过 device code 登录，走游客模式（服务端需 AUTH_REQUIRED=false）")
    p.add_argument("--logout", action="store_true", help="清除本地凭证后退出")
    p.add_argument("-v", "--version", action="store_true", help="打印版本后退出")
    return p.parse_args(argv)


def _needs_auth(client: Client) -> bool:
    """拿 /auth/me 探测；200 且 access_token 有效 → 已登录；401 → 需要登录；其它 → 游客可能。"""
    try:
        httpx = _require_httpx()
        r = client._c.get(f"{client.host}/auth/me", headers=client._hdrs())
        if r.status_code == 200:
            return False  # token 仍然有效
        if r.status_code == 401:
            # 试一下未鉴权访问 /api/models：若服务端完全不要登录，会返回 200
            try:
                probe = client._c.get(f"{client.host}/api/models")
                return probe.status_code == 401
            except Exception:  # noqa: BLE001
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if args.version:
        print(f"chachat {__version__}")
        return 0
    if args.logout:
        clear_credentials(args.host)
        print(f"已清除 {args.host} 的本地凭证。")
        return 0

    host = args.host.rstrip("/")
    _require_httpx()
    creds = load_credentials(host)
    client = Client(host, access_token=creds.get("access_token") or "")

    user: Dict[str, Any] = creds.get("user") or {}
    mode = "guest"
    if args.no_auth:
        mode = "guest (forced)"
    else:
        # 若本地有 token 先试一下；否则走 device code
        if not client.access_token:
            if _needs_auth(client):
                tok = _maybe_login_flow(client)
                if not tok.get("access_token"):
                    print("未获取到有效 token，以游客模式继续（仅当服务端允许匿名）。")
                else:
                    client.access_token = tok["access_token"]
                    user = tok.get("user") or {}
                    save_credentials(host, {
                        "access_token": tok["access_token"],
                        "refresh_token": tok.get("refresh_token") or "",
                        "user": user,
                    })
                    mode = f"user={user.get('username','?')}"
            else:
                mode = "guest"
        else:
            # 已有 token，试着 /auth/me 刷一下 user；失败就走登录
            try:
                r = client._c.get(f"{host}/auth/me", headers=client._hdrs())
                if r.status_code == 200:
                    user = r.json() or user
                    mode = f"user={user.get('username','?')}"
                elif r.status_code == 401:
                    print("本地 token 已失效，重新登录。")
                    tok = _maybe_login_flow(client)
                    if tok.get("access_token"):
                        client.access_token = tok["access_token"]
                        user = tok.get("user") or {}
                        save_credentials(host, {
                            "access_token": tok["access_token"],
                            "refresh_token": tok.get("refresh_token") or "",
                            "user": user,
                        })
                        mode = f"user={user.get('username','?')}"
            except Exception:  # noqa: BLE001
                pass

    _print_banner(host, mode, args.model, args.kb)
    try:
        Repl(client, host, args.model, args.kb, user).run()
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
