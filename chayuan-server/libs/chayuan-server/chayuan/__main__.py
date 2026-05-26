"""``python -m chayuan`` / PyInstaller frozen 入口。

Phase 2 引入:PyInstaller spec 优先把这里作为入口(比 ``cli.py`` 干净一点 ──
``cli.py`` 顶部有重依赖 + brandlock check,作为 frozen entry 静态分析时会拖更多
不必要的子模块进 graph)。``cli:main`` 仍是 ``poetry run chayuan`` 命令的真源。
"""
from __future__ import annotations

import sys


def _force_utf8_stdio() -> None:
    """单机版 / frozen exe 在 Windows OEM(GBK)console 里默认会把 UTF-8 输出
    渲染成乱码。这里在 cli 解析任何参数 / 加载任何模块前,先把 sys.stdout /
    sys.stderr 切到 UTF-8(errors='replace' 保底,免得偶发 surrogate 让进程崩)。

    Python 3.7+ 的 ``TextIOWrapper.reconfigure`` 是原地重配,
    不会把已 attach 的下游(loguru / logging handlers)断链。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        # detached / closed streams(daemon 模式下偶发)不动
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


_force_utf8_stdio()


from chayuan.cli import main


def _run_sidecar_if_requested() -> bool:
    """frozen 模式:SidecarRuntimeManager 用
    ``chayuan-server --sidecar-mode <cap> --model ... --host ... --port ...``
    自我转化拉起 Python sidecar —— frozen 下 sys.executable 是 chayuan-server
    本体、不是 python,无法 ``python -m``(见 process_args.resolve_image_
    embedding_args 的 frozen 分支)。

    在 Click CLI 之前拦截:命中 ``--sidecar-mode`` 就摘掉它 + 能力名,余下的
    ``--model/--host/--port`` 交给对应 sidecar 自己的 parse_serve_args,直接跑
    sidecar 的 main。返回 ``True`` 表示已作为 sidecar 跑完,调用方不应再进 cli。

    不实现它的后果:``chayuan-server --sidecar-mode ...`` 会落到 Click CLI,
    Click 不认 ``--sidecar-mode`` → 打 ``Usage: chayuan-server`` 退出 → 图像
    向量(image-embedding)sidecar 永远起不来。
    """
    argv = sys.argv
    if "--sidecar-mode" not in argv:
        return False
    i = argv.index("--sidecar-mode")
    cap = argv[i + 1] if i + 1 < len(argv) else ""
    del argv[i:i + 2]  # 摘掉 `--sidecar-mode <cap>`,余下交给 sidecar 解析
    # image-embedding sidecar 已下线 —— 它是唯一依赖 PyTorch 的 sidecar,
    # 图像功能改走文档库 OCR + 文本嵌入。要恢复:取消下面注释。
    # if cap == "image-embedding":
    #     from chayuan.server.image_source.infinity_server import (
    #         main as _sidecar_main,
    #     )
    #     _sidecar_main()
    #     return True
    print(f"[sidecar] 未知 --sidecar-mode 能力:{cap!r}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    if not _run_sidecar_if_requested():
        main()
