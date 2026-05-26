# vendor/torch_wheels/

跨平台 PyTorch + torchvision wheel 暂存目录,供 chayuan-desktop 离线打包用。

## 目录约定

```
vendor/torch_wheels/
├── win_amd64_py312_cpu/                      # Windows x64 CPU
│   ├── torch-2.6.0+cpu-cp312-cp312-win_amd64.whl       # ~250 MB
│   └── torchvision-0.21.0+cpu-cp312-cp312-win_amd64.whl  # ~7 MB
├── manylinux2014_x86_64_py312_cpu/           # Linux x64 CPU
│   ├── torch-2.6.0+cpu-cp312-cp312-linux_x86_64.whl    # ~250 MB
│   └── torchvision-0.21.0+cpu-cp312-cp312-linux_x86_64.whl
├── macosx_11_0_arm64_py312_cpu/              # macOS Apple Silicon
│   ├── torch-2.6.0-cp312-cp312-macosx_11_0_arm64.whl   # ~70 MB
│   └── torchvision-0.21.0-cp312-cp312-macosx_11_0_arm64.whl
└── macosx_10_9_x86_64_py312_cpu/             # macOS Intel
    ├── torch-2.6.0-cp312-cp312-macosx_10_9_x86_64.whl  # ~150 MB
    └── torchvision-0.21.0-cp312-cp312-macosx_10_9_x86_64.whl
```

子目录命名规则 `{platform}_py{ver}_{variant}`,详见 `chayuan-server/packaging/preflight_torch.py:WheelTarget.subdir`。

## 怎么填充

通常**不需要手动**填充。`build-desktop.ps1` 打包时会自动调 `preflight_torch.py` 把 wheel 拉到这里:

```powershell
.\build-desktop.ps1 -LiteOnly    # 默认下 4 个平台 CPU wheel (~1 GB)
```

要手动跑 / 离线机器准备:

```bash
# 默认:4 个平台 × py312 × cpu
poetry run python chayuan-server/packaging/preflight_torch.py

# 只下 Windows(其它平台不打包)
poetry run python chayuan-server/packaging/preflight_torch.py \
    --platforms win_amd64

# 加 CUDA(仅 win/linux x64 有 cu* wheel,macOS 跳过)
poetry run python chayuan-server/packaging/preflight_torch.py \
    --variants cpu,cu124
```

下载来源:`https://download.pytorch.org/whl/<variant>`(无国内镜像,海外服务器可达即可)。

## 怎么进 installer

| Wheel 落点 | 怎么过去 | 体积兜底 |
|---|---|---|
| `<install>/torch_wheels/<sub>/` (MSI 资源) | `build.py sync_torch_wheels` → `src-tauri/torch_wheels/` → Tauri MSI 打包 | 单 wheel ≤ 2GB 时走这条 |
| `<install>/torch_wheels_seed/<sub>/` (ISO 外挂,未来 CUDA) | `build.py sync_torch_wheels` 大文件分流 → `dist/torch_wheels_seed/` → ISO 介质 | 单 wheel > 2GB 时备用(目前 CPU wheel 不触发) |

## 运行时怎么用

`chayuan-server` 首启 `_model_first_launch` 钩子调用 `pytorch_installer.seed_torch_wheels()`,把 `<install>/torch_wheels/` 拷到 `<CHAYUAN_ROOT>/torch_wheels/`,然后 `auto_install_on_startup()`:

1. `nvidia-smi` 检测 GPU:有 GPU + 驱动 ≥ 525 + 对应 `cu*` wheel → 装 CUDA 版
2. 否则 → 装 CPU 版

装到 sidecar venv 内,**仅 `image-embedding` 和 `rerank` 这两个 capability 才需要**;其它 5 个 cap(chat / embedding / asr / ocr / tts)都是纯 C++/ONNX 链路,跟 PyTorch 无关。

## 不入 git

`*.whl` 已在 `.gitignore`,wheel 不入仓。`vendor/torch_wheels/` 各子目录用 `.gitkeep` 保留结构,实际 wheel 在 CI / 本地构建时按需下载。
