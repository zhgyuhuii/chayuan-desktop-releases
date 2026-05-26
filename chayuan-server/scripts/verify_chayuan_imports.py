#!/usr/bin/env python3
"""Smoke verification: CHAYUAN_ROOT -> 仓库内 chayuan_data，并校验 LangChain / chayuan 可加载。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    data_root = repo_root / "chayuan_data"
    os.environ.setdefault("CHAYUAN_ROOT", str(data_root))

    server_root = repo_root / "libs" / "chayuan-server"
    if str(server_root) not in sys.path:
        sys.path.insert(0, str(server_root))

    import langchain
    import langchain_core

    from chayuan.settings import Settings

    _ = Settings.basic_settings
    print(
        "verify_chayuan_imports: OK\n"
        f"  CHAYUAN_ROOT={Settings.CHAYUAN_ROOT}\n"
        f"  langchain={getattr(langchain, '__version__', '?')}\n"
        f"  langchain-core={getattr(langchain_core, '__version__', '?')}\n"
        f"  chayuan data dir exists={data_root.is_dir()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
