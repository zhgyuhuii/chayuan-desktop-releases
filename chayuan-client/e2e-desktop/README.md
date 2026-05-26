# 桌面 e2e（tauri-driver + WDIO）

驱动 Tauri 二进制的真实窗口，验证：登录 / 流式 / 反馈 / 工具批准等链路。

## 一次性安装

```bash
# 1. 安装 tauri-driver（系统级）
cargo install tauri-driver --locked

# 2. （Linux）确保有 webkit2gtk-driver / msedgedriver / safaridriver 之一
# Linux:   sudo apt-get install webkit2gtk-driver
# Windows: tauri-driver 自带，无需额外
# macOS:   sudo safaridriver --enable

# 3. 安装 npm 包
pnpm --filter @chayuan/e2e-desktop install
```

## 跑测

```bash
# 1. 构建桌面 release
pnpm --filter @chayuan/desktop build

# 2. 启 tauri-driver（独立终端，监听 4444）
tauri-driver

# 3. 跑 wdio
pnpm --filter @chayuan/e2e-desktop test
```

## 工作原理

```
┌────────────────┐    WebDriver    ┌──────────────┐
│  WDIO test     │ ──────────────▶ │ tauri-driver │
└────────────────┘                  └──────┬───────┘
                                            │ spawn
                                            ▼
┌────────────────┐  HTTP (7891)   ┌──────────────┐
│  mock-server   │ ◀────────────── │ Tauri 应用    │
│  (Node)        │                 │ (chayuan)    │
└────────────────┘                 └──────────────┘
```

- `wdio.conf.ts.onPrepare` 起 mock-server（端口 7891）
- Tauri 二进制需用 `VITE_API_BASE=http://127.0.0.1:7891` 重新构建一次（CI 脚本里加）
- mock-server 暴露 `/__mock__/state` 让用例读取已捕获的 feedback / resume，做断言

## 与 web e2e 的关系

夹具与 SSE 流构造统一在 `e2e-shared/fixtures.ts`；桌面的 mock-server
内联了同样的常量。修改后两边同时生效。
