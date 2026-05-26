# Chayuan Server Compose 模板

每个文件是一个**独立的 docker-compose.yaml**,只含 1 个 service。

## 工作机制

1. `chayuan init` / 首次启动时,本目录的 yaml 复制到
   `<CHAYUAN_ROOT>/compose/<service>.yaml`
2. 配置面板"运行时与服务"页扫描 `<CHAYUAN_ROOT>/compose/*.yaml`,
   **有几个 yaml 就显示几个卡片**(用户可自加)
3. 点击卡片 → 弹出"安装与配置"
   - **▶ 运行**:`docker compose -f <CHAYUAN_ROOT>/compose/<x>.yaml up -d`
   - **⏹ 停止**:`docker compose -f <...> stop`
   - **配置 tab**:显示该 yaml 内容(yaml 语法高亮),保存即写盘
   - **日志 tab**:显示真实 docker 命令的 stdout/stderr

## 如何加入自定义服务

放一个新 yaml 到 `<CHAYUAN_ROOT>/compose/`,例如
`<CHAYUAN_ROOT>/compose/qdrant.yaml`,UI 自动出现卡片,无需改代码。

## 运行时与服务的探测

* 已 `docker ps` 看到容器健康 → 显示"运行中"
* 没运行但有 yaml → 显示"未启动"(点 ▶ 运行)
* 没 yaml 也没容器 → 不显示

## 容器服务自动注册

容器 healthy 后,chayuan 自动调 `register_after_healthy(<service>)`:
* 拉 service 端口的 `/v1/models`
* 写到 `model_settings.yaml.MODEL_PLATFORMS` 加新 platform
* 设默认模型(若 capability 还无默认)

无需用户手动配置。
