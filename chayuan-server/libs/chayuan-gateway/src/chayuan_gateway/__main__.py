from __future__ import annotations

import argparse

from chayuan_core import load_config


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser(prog="chayuan-gateway")
    ap.add_argument("--host", default=cfg.gateway.host)
    ap.add_argument("--port", type=int, default=cfg.gateway.port)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()

    import uvicorn

    uvicorn.run("chayuan_gateway.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":  # pragma: no cover
    main()
