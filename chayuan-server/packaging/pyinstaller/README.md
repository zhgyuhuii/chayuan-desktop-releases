# chayuan-server PyInstaller 打包(Phase 2)

把 ``chayuan-server`` 打成单可执行,供 Tauri 桌面端通过 sidecar 嵌入(Phase 3)。

## 一次性环境

```bash
cd chayuan-server
poetry install                            # 全量依赖,与 CI 一致
poetry run pip install "pyinstaller>=6.0" # 不进 pyproject 主依赖,仅打包用
```

(可选)下载 sqlite-vec 扩展放到 ``packaging/vendor/sqlite-vec/``:

```text
packaging/vendor/sqlite-vec/
├── vec0.so          # Linux x86_64
├── vec0.dylib       # macOS(同 .dylib 适配 arm64 / x86_64)
└── vec0.dll         # Windows
```

源:[asg017/sqlite-vec releases](https://github.com/asg017/sqlite-vec/releases)。

## 打包

```bash
# 默认:打包 + 拷到 ../chayuan-client/apps/desktop/src-tauri/binaries/
poetry run python packaging/pyinstaller/build.py

# 仅打包,不拷贝
poetry run python packaging/pyinstaller/build.py --no-copy

# 直接驱动 PyInstaller(调试 spec 用)
poetry run pyinstaller packaging/pyinstaller/chayuan-server.spec --noconfirm
```

输出布局:

| 路径 | 内容 |
| :--- | :--- |
| ``dist/chayuan-server/chayuan-server[.exe]`` | 可执行入口(onedir 形态) |
| ``dist/chayuan-server/_internal/`` | 解释器 + wheels + 数据文件 + sqlite-vec |
| ``../chayuan-client/apps/desktop/src-tauri/binaries/chayuan-server-<triple>[.exe]`` | Tauri ``externalBin`` 命名约定 |

triple 表(``build.py`` ``rust_target_triple()`` 自动判断):

| OS / arch | triple |
| :--- | :--- |
| macOS arm64 | ``aarch64-apple-darwin`` |
| macOS x86_64 | ``x86_64-apple-darwin`` |
| Windows x86_64 | ``x86_64-pc-windows-msvc`` |
| Windows arm64 | ``aarch64-pc-windows-msvc`` |
| Linux x86_64 | ``x86_64-unknown-linux-gnu`` |
| Linux arm64 | ``aarch64-unknown-linux-gnu`` |

## 启动协议

Tauri sidecar 启动时:

```bash
chayuan-server start -a --single-machine
```

并通过 env 注入用户首启动向导选定的数据目录:

```text
CHAYUAN_ROOT=<选定目录>
CHAYUAN_PROFILE=single-machine          # --single-machine 自动设
CHAYUAN_AUTH=anonymous
CHAYUAN_REDIS=disabled
CHAYUAN_QUEUE=inproc
CHAYUAN_VECTOR_STORE=sqlite-vec
```

服务端默认监听 ``127.0.0.1:62581``;Phase 3 的健康探测用 ``GET /health``。

## 已知体积来源

> v0 优先工作。Phase 5 单机 profile 把以下依赖按需懒加载或剥离,目标 < 600 MB。

| 依赖 | 占比 | 单机必需? |
| :--- | :--- | :--- |
| paddleocr + paddlepaddle | ~1.2 GB | 文档 OCR;Phase 5 改为按需懒加载 |
| faiss-cpu | ~120 MB | 向量索引;Phase 4 切到 sqlite-vec |
| onnxruntime | ~70 MB | embedding;**保留** |
| transformers / sentence-transformers | ~200 MB | 可选 reranker;按需保留 |
| torch (paddle 间接) | ~600 MB | Phase 5 走 ONNX 后大概率可移除 |

预期:
- v0 单机包 ~1.8-2.2 GB
- Phase 5 后目标 < 600 MB(裁掉 paddle / torch / faiss / unstructured 大部分)

## 平台特异

- **macOS**:
  - 首次打包后 ``codesign / notarize`` 由 CI(Phase 6)处理;开发态本地运行无需。
  - 需 ``minimumSystemVersion = 10.15`` 与 Tauri ``tauri.conf.json`` 一致。
- **Windows**:
  - wix / nsis 在 Tauri bundle 阶段签名(EV 证书走 GitHub secrets)。
  - PyInstaller 输出可能被 Defender 误报;CI 跑前 ``windows-latest`` 加 EV 签名缓解。
- **Linux**:
  - ``--strip`` 默认关(paddle 偶发不兼容 strip)。
  - AppImage 嵌入时确保 ``--no-libgtk-3-bundle`` 由 Tauri 控制。

## 排除项

spec 显式 ``excludes``(单机模式不会用到):

```text
celery / kombu / billiard / amqp        # 走 asyncio.Queue
arq / redis / aioredis                  # 同上
psycopg2 / psycopg / asyncpg            # 走 sqlite
pytest / mypy / black                   # 测试 / 类型工具
```

如果未来 single-machine 需要某条被排除的,把对应 entry 从 ``excludes`` 拿掉重新打包即可。

## 故障排查

| 现象 | 原因 / 处理 |
| :--- | :--- |
| ``ModuleNotFoundError: chayuan.xxx`` | spec 已 ``collect_submodules('chayuan')``;若仍报缺,在 ``hidden_modules`` 里显式列出 |
| ``ImportError: dynamic module does not define module export function`` | 多见于 paddle.so;先 ``pip install --force-reinstall paddlepaddle`` |
| 启动时打 ``cwd: /tmp/_MEIxxx`` | 正常,PyInstaller onefile 会解压到 ``_MEIPASS``;onedir 不会,直接 dist 路径 |
| sqlite-vec ``no such function: vec_distance_l2`` | ``packaging/vendor/sqlite-vec/`` 没放对应平台扩展;打包前先放进去 |
