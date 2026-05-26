"""``python -m chayuan_packaging`` 入口。

build.py(chayuan-server/packaging/pyinstaller/build.py:213)调用
``[sys.executable, "-m", "chayuan_packaging", sub, "--release", release]``
拉取 vendor 资源。`python -m <pkg>` 需要包里有 ``__main__`` 模块,
否则 Python 报 ``No module named chayuan_packaging.__main__; 'chayuan_packaging'
is a package and cannot be directly executed``。

本文件把入口转发给 ``pack.main`` —— click group,所有真业务在那。
"""
from chayuan_packaging.pack import main

if __name__ == "__main__":
    main()
