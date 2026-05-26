# 察元自启诊断报告

- **生成时间**: 2026-05-18 16:49:16 +08:00
- **Host**: ZYH
- **User**: zhgyu
- **OS**: Microsoft Windows NT 10.0.26200.0
- **PSVersion**: 5.1.26100.8115

## 1. 探测安装目录

**自动探测失败** — 没在以下路径找到 chayuan-server.exe:
  - C:\Users\zhgyu\AppData\Local\Programs\chayuan-desktop
  - C:\Program Files\chayuan-desktop
  - C:\Program Files\Chayuan Desktop
  - C:\Program Files (x86)\chayuan-desktop
  - C:\Program Files (x86)\Chayuan Desktop

请重跑:`.\diagnose-auto-start.ps1 -InstallDir 'C:\path\to\chayuan-desktop'`
- **InstallDir**: (未找到)

## 2. 探测数据目录

- **desktop.json 路径**: C:\Users\zhgyu\AppData\Roaming\chayuan\desktop.json
- **desktop.json data_dir**: D:\chayuan_test
- **desktop.json version**: 2
- **desktop.json linked_install_id**: exe-f9bbd495c47a8700
- **DataDir(实际生效)**: D:\chayuan_test
- **CHAYUAN_ROOT env**: (未设)
- **CHAYUAN_BUNDLED_MODELS_DIR env**: (未设)

## 3. bundled_models 布局

**目标(data dir 已 seed)**:不存在 `D:\chayuan_test\models\bundled`

## 4. 关键配置文件


### sidecar_settings.json
**path**: D:\chayuan_test\data\sidecar_settings.json
**(文件不存在)**

### local_runtime.yaml
**path**: D:\chayuan_test\model_registry\local_runtime.yaml
**(文件不存在)**

### runtime.json
**path**: D:\chayuan_test\runtime.json
**(文件不存在)**

### local_models.json (前 80 行)
**path**: D:\chayuan_test\model_registry\local_models.json
**(文件不存在)**

## 5. 进程 / 端口

相关进程(进程名 + 命令行匹配):
  - pdfpreview.exe         pid=12544  cmd="C:\Users\zhgyu\AppData\Local\sogoupdf\pdfpreview.exe" "D:\code\chayuan\chayuan-desktop-own\chayuan-server\...
  - python.exe             pid=84688  cmd=D:\soft\conda_envs\py312\python.exe -m chayuan.server.modality.rapidocr_server --host 127.0.0.1 --port 18380
  - python.exe             pid=78916  cmd="D:\soft\conda_envs\py312\python.exe" -m chayuan start -a --single-machine 
  - python.exe             pid=85592  cmd=D:\soft\conda_envs\py312\python.exe -m chayuan.server.image_source.infinity_server --model models/bundled/i...
  - WindowsTerminal.exe    pid=82060  cmd="C:\Program Files\WindowsApps\Microsoft.WindowsTerminal_1.24.11321.0_x64__8wekyb3d8bbwe\WindowsTerminal.exe...

监听端口(自启相关):
```
  TCP    127.0.0.1:18380        0.0.0.0:0              LISTENING       84688
  TCP    127.0.0.1:62581        0.0.0.0:0              LISTENING       81728
  TCP    127.0.0.1:62586        0.0.0.0:0              LISTENING       85592
```

端口 → 进程映射:
  - :18380 pid=84688  name=python
  - :62581 pid=81728  name=python
  - :62586 pid=85592  name=python

## 6. HTTP 探针 (port=62586)


### GET /runtime/llama/registry — 5 capability registry 状态
**失败**:远程服务器返回错误: (404) 未找到。

### GET /runtime/diagnose — runtime/diagnose 综合健康
**失败**:远程服务器返回错误: (404) 未找到。

### GET /runtime/llama/chat/status — chat status
**失败**:远程服务器返回错误: (404) 未找到。

### GET /runtime/llama/embedding/status — embedding status
**失败**:远程服务器返回错误: (404) 未找到。

### GET /runtime/llama/rerank/status — rerank status
**失败**:远程服务器返回错误: (404) 未找到。

### GET /runtime/llama/asr/status — asr status
**失败**:远程服务器返回错误: (404) 未找到。

### GET /runtime/llama/image-embedding/status — image-embedding status
**失败**:远程服务器返回错误: (404) 未找到。

### GET /modality/sidecar/ocr/status — RapidOCR sidecar status
**失败**:远程服务器返回错误: (404) 未找到。

## 7. chayuan-server 日志 tail

**日志目录不存在**:`D:\chayuan_test\logs`

## 8. sidecar 完整 stdout 抓取(30 秒)

**InstallDir 未知,跳过 stdout 抓取**
