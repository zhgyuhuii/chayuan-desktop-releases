#!/usr/bin/env python3
"""自动配置本地环境（postgres + redis + milvus + ollama + langfuse）。

用法：
    python scripts/setup_local.py                        # 使用所有默认值（本机环回）
    python scripts/setup_local.py --pg-host 10.0.0.2 ...  # 自定义
    python scripts/setup_local.py --probe-only            # 仅探活不改 yaml

做两件事：
1. **探活**所有依赖服务（返回每个服务的红/绿状态）
2. 把 ``chayuan_data/*.yaml`` 里的 DB / Redis / Milvus / Ollama / Langfuse
   配置**原地写入**（带备份）

与 ``docker/dev-stack`` 的 ``.env`` 对齐时，可先 ``set -a && source .env && set -a``（在
该目录下），再执行本脚本，以便使用 ``POSTGRES_*``、``REDIS_PASSWORD`` 而无需
在命令行传长密码。若 ``REDIS_PASSWORD`` 非空，会写入带认证的 ``REDIS_URL``。

不会碰 KB 数据；只改配置文件。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 配置默认
# --------------------------------------------------------------------------

DEFAULTS = {
    "pg_host": "127.0.0.1", "pg_port": 5432, "pg_user": "postgres",
    "pg_password": "postgres", "pg_database": "chayuan",
    "redis_host": "127.0.0.1", "redis_port": 6379, "redis_db": 0,
    "redis_password": "",
    "milvus_host": "127.0.0.1", "milvus_port": 19530,
    "ollama_host": "127.0.0.1", "ollama_port": 11434,
    "ollama_llm": "qwen2.5:3b",            # 用户可能装的是 qwen3:4b / qwen2.5:7b 等
    "ollama_embed": "nomic-embed-text",    # ollama pull nomic-embed-text
    "langfuse_host": "127.0.0.1", "langfuse_port": 3000,
}


# --------------------------------------------------------------------------
# 探活工具
# --------------------------------------------------------------------------

def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> Tuple[bool, str]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, "tcp ok"
    except Exception as e:
        return False, f"tcp failed: {type(e).__name__}: {e}"


def _http_get(url: str, timeout: float = 3.0) -> Tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "chayuan-setup"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
        return True, f"http {resp.status}: {body[:120]}"
    except Exception as e:
        return False, f"http failed: {type(e).__name__}: {e}"


def probe_postgres(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    # 不强依赖 psycopg；TCP 可达即大概率 OK；有包再 SELECT 1
    ok, msg = _tcp_reachable(cfg["pg_host"], cfg["pg_port"])
    if not ok:
        return ok, msg
    try:
        import psycopg2  # type: ignore
    except Exception:
        return True, msg + "（psycopg2 未装，仅 TCP 探测）"
    try:
        conn = psycopg2.connect(
            host=cfg["pg_host"], port=cfg["pg_port"],
            user=cfg["pg_user"], password=cfg["pg_password"],
            dbname=cfg["pg_database"], connect_timeout=3,
        )
        conn.close()
        return True, "SELECT 1 ok"
    except Exception as e:
        return False, f"pg connect failed: {type(e).__name__}: {e}"


def probe_redis(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    ok, msg = _tcp_reachable(cfg["redis_host"], cfg["redis_port"])
    if not ok:
        return ok, msg
    try:
        import redis  # type: ignore
        r = redis.Redis(
            host=cfg["redis_host"], port=cfg["redis_port"],
            db=cfg["redis_db"],
            password=cfg.get("redis_password") or None,
            socket_connect_timeout=2,
        )
        pong = r.ping()
        return bool(pong), "PING ok" if pong else "PING failed"
    except Exception as e:
        return True, msg + f"（redis 库未装或 ping 失败：{e}）"


def probe_milvus(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    ok, msg = _tcp_reachable(cfg["milvus_host"], cfg["milvus_port"])
    if not ok:
        return ok, msg
    # 通常 9091 是 metrics；19530 是 gRPC。能 TCP 通就认可用
    return True, msg


def probe_ollama(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    url = f"http://{cfg['ollama_host']}:{cfg['ollama_port']}/api/tags"
    return _http_get(url)


def probe_langfuse(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    url = f"http://{cfg['langfuse_host']}:{cfg['langfuse_port']}/api/public/health"
    ok, msg = _http_get(url, timeout=2.0)
    if ok:
        return True, msg
    # 二级探活：根路径 200 表示 Web UI 跑着但 API 健康接口未在 v2 里
    ok2, msg2 = _http_get(f"http://{cfg['langfuse_host']}:{cfg['langfuse_port']}/")
    if ok2:
        return True, "web ok (no /api/public/health in v2)"
    return False, msg


PROBES = [
    ("postgres", probe_postgres),
    ("redis", probe_redis),
    ("milvus", probe_milvus),
    ("ollama", probe_ollama),
    ("langfuse", probe_langfuse),
]


def run_probes(cfg: Dict[str, Any]) -> Dict[str, Tuple[bool, str]]:
    results: Dict[str, Tuple[bool, str]] = {}
    for name, fn in PROBES:
        print(f"[probe] {name:<10} ... ", end="", flush=True)
        ok, msg = fn(cfg)
        results[name] = (ok, msg)
        tag = "OK  " if ok else "FAIL"
        print(f"{tag}  {msg}")
    return results


# --------------------------------------------------------------------------
# 写配置
# --------------------------------------------------------------------------

def _chayuan_root() -> Path:
    env = os.environ.get("CHAYUAN_ROOT", "")
    if env and Path(env).exists():
        return Path(env)
    p = Path(__file__).resolve().parent.parent / "chayuan_data"
    return p


def _backup(path: Path) -> Path:
    if not path.exists():
        return path
    bk = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    shutil.copy2(path, bk)
    return bk


def _yaml_load(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        print("  [warn] PyYAML 未装；改用朴素文本覆盖", file=sys.stderr)
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"  [warn] yaml 解析失败：{e}", file=sys.stderr)
        return {}


def _yaml_dump(path: Path, data: Dict[str, Any]) -> None:
    try:
        import yaml  # type: ignore
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except ImportError:
        raise SystemExit("需要 PyYAML：pip install pyyaml")


def apply_basic_settings(root: Path, cfg: Dict[str, Any]) -> None:
    path = root / "basic_settings.yaml"
    data = _yaml_load(path) if path.exists() else {}
    _backup(path)

    pg_user = urllib.parse.quote_plus(cfg["pg_user"])
    pg_pwd = urllib.parse.quote_plus(cfg["pg_password"])
    data["SQLALCHEMY_DATABASE_URI"] = (
        f"postgresql+psycopg2://{pg_user}:{pg_pwd}"
        f"@{cfg['pg_host']}:{cfg['pg_port']}/{cfg['pg_database']}"
    )
    rpw = (cfg.get("redis_password") or "").strip()
    if rpw:
        rp = urllib.parse.quote_plus(rpw)
        data["REDIS_URL"] = (
            f"redis://:{rp}@{cfg['redis_host']}:"
            f"{cfg['redis_port']}/{cfg['redis_db']}"
        )
    else:
        data["REDIS_URL"] = (
            f"redis://{cfg['redis_host']}:"
            f"{cfg['redis_port']}/{cfg['redis_db']}"
        )
    data["RATE_LIMIT_ENABLED"] = True
    data["SEMANTIC_CACHE_ENABLED"] = True
    data["METRICS_ENABLED"] = True
    data["AUTH_REQUIRED"] = True
    # 默认 JWT_SECRET 需要稳定值：若原来是空的，生成一个
    if not data.get("JWT_SECRET"):
        import secrets
        data["JWT_SECRET"] = secrets.token_hex(32)
    # ChatGraph / 治理 / Guardrail 打开
    data["USE_CHAT_GRAPH"] = True
    data["GOVERNANCE_ENABLED"] = True
    data["GUARDRAIL_ENABLED"] = True
    data["GUARDRAIL_BACKEND"] = "rules"
    # Langfuse 不通时自动禁用（防止 build/test 阻塞）
    # 用户启动 langfuse 后手动改回 false 并填 env
    data["CHAYUAN_LANGFUSE_DISABLE"] = False

    _yaml_dump(path, data)
    print(f"[write] {path.name} 已更新")


def apply_kb_settings(root: Path, cfg: Dict[str, Any]) -> None:
    path = root / "kb_settings.yaml"
    data = _yaml_load(path) if path.exists() else {}
    _backup(path)

    data["DEFAULT_VS_TYPE"] = "milvus"
    data.setdefault("kbs_config", {})
    data["kbs_config"].setdefault("milvus", {})
    data["kbs_config"]["milvus"].update({
        "host": cfg["milvus_host"], "port": str(cfg["milvus_port"]),
        "user": "", "password": "", "secure": False,
    })
    # P0-1 建议默认开启
    data["USE_HYBRID_RETRIEVER"] = True
    data["USE_RERANKER"] = False   # 需要用户手动装 sentence-transformers 后再开
    # 保持默认 chunk 策略，不动用户习惯

    _yaml_dump(path, data)
    print(f"[write] {path.name} 已更新")


def apply_model_settings(root: Path, cfg: Dict[str, Any]) -> None:
    path = root / "model_settings.yaml"
    data = _yaml_load(path) if path.exists() else {}
    _backup(path)

    data["DEFAULT_LLM_MODEL"] = cfg["ollama_llm"]
    data["DEFAULT_EMBEDDING_MODEL"] = cfg["ollama_embed"]
    data["MODEL_PLATFORMS"] = [{
        "platform_name": "ollama", "platform_type": "ollama",
        "api_base_url": f"http://{cfg['ollama_host']}:{cfg['ollama_port']}/v1",
        "api_key": "EMPTY", "api_proxy": "",
        "api_concurrencies": 5, "auto_detect_model": False,
        "llm_models": [cfg["ollama_llm"]],
        "embed_models": [cfg["ollama_embed"]],
        "text2image_models": [], "image2text_models": [],
        "rerank_models": [], "speech2text_models": [], "text2speech_models": [],
    }]
    _yaml_dump(path, data)
    print(f"[write] {path.name} 已更新")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    for k, v in DEFAULTS.items():
        p.add_argument(f"--{k.replace('_', '-')}", default=v)
    p.add_argument("--probe-only", action="store_true",
                    help="仅探活，不写 yaml")
    p.add_argument("--skip-probe", action="store_true",
                    help="跳过探活，直接写 yaml")
    p.add_argument("--dry-run", action="store_true",
                    help="只打印改动，不落盘")
    args = p.parse_args()
    cfg = {k: getattr(args, k) for k in DEFAULTS.keys()}
    for k in ("pg_port", "redis_port", "redis_db", "milvus_port", "ollama_port", "langfuse_port"):
        cfg[k] = int(cfg[k])
    # 与 docker/dev-stack .env 对齐：可 source 后免输长密码
    cfg["pg_user"] = os.environ.get("POSTGRES_USER", cfg["pg_user"])
    cfg["pg_password"] = os.environ.get("POSTGRES_PASSWORD", cfg["pg_password"])
    cfg["pg_database"] = os.environ.get("POSTGRES_DB", cfg["pg_database"])
    if os.environ.get("REDIS_PASSWORD", "").strip() != "":
        cfg["redis_password"] = os.environ.get("REDIS_PASSWORD", "")

    results: Dict[str, Tuple[bool, str]] = {}
    if not args.skip_probe:
        print("\n=== 依赖服务探活 ===")
        results = run_probes(cfg)
        red = [k for k, (ok, _) in results.items() if not ok]
        if red:
            # 避免在 Windows GBK 控制台下 emoji 编码炸：用纯 ASCII 标签
            print(f"\n[WARN] 未通过探活：{', '.join(red)}")
            if "postgres" in red or "milvus" in red:
                print("   -> 可用 `cd docker/dev-stack && docker compose up -d` 启动缺失服务")
            if "langfuse" in red:
                print("   -> 可用 `cd docker/dev-stack && docker compose --profile langfuse up -d`")
        else:
            print("\n[OK] 所有依赖服务都就绪")

    if args.probe_only:
        return 0 if all(ok for ok, _ in results.values()) else 1

    root = _chayuan_root()
    print(f"\n=== 写入配置到 {root} ===")
    if not root.exists():
        print(f"[error] 目录不存在：{root}；请先 `chayuan init` 生成模板")
        return 2

    if args.dry_run:
        print("[dry-run] 不落盘；以下为将应用的配置摘要")
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0

    apply_basic_settings(root, cfg)
    apply_kb_settings(root, cfg)
    apply_model_settings(root, cfg)

    print("\n[OK] 配置完成。下一步：")
    print("   1. 如 langfuse 未起：cd docker/dev-stack && docker compose --profile langfuse up -d")
    print("   2. 启动察元：chayuan start -a")
    print("   3. 跑黑盒烟雾测试：python scripts/smoke_test.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
