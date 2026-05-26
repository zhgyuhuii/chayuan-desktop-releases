"""`python -m chayuan.server.mcp_server ...` 入口。"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    p = argparse.ArgumentParser(prog="chayuan.mcp")
    sub = p.add_subparsers(dest="transport", required=True)
    sub.add_parser("stdio", help="stdio 传输（Claude Desktop / Cursor 标准）")
    sse_p = sub.add_parser("sse", help="SSE 传输（HTTP）")
    sse_p.add_argument("--host", default="127.0.0.1")
    sse_p.add_argument("--port", type=int, default=7862)
    args = p.parse_args()

    try:
        if args.transport == "stdio":
            from chayuan.server.mcp_server import run_stdio
            run_stdio()
        else:
            from chayuan.server.mcp_server import run_sse
            run_sse(host=args.host, port=args.port)
    except ImportError as e:
        sys.stderr.write(
            f"[chayuan.mcp] 缺少依赖 `mcp`，请 `pip install 'mcp>=1.4,<2'`。错误：{e}\n"
        )
        return 2
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[chayuan.mcp] 启动失败：{e}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
