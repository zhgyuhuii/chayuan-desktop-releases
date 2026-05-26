"""``python -m chayuan.tray`` 的入口模块。

存在的唯一理由是给安装包的启动脚本（macOS 下 ``Contents/MacOS/Chayuan``）一个
稳定的 ``python -m`` 目标。等价于 ``chayuan tray`` CLI 子命令，但不依赖
``bin/chayuan`` shebang——更适合 portable Python bundle。
"""

from __future__ import annotations

from chayuan.tray import main


if __name__ == "__main__":
    main()
