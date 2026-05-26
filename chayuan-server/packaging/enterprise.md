# 察元 AI 助手 企业版 打包说明（M7）

本文件介绍企业版（enterprise edition）如何与个人版（personal edition）分叉打包，以及企业版相对个人版的技术差异。

## TL;DR

```bash
# 个人版（默认）
bash packaging/mac/build_mac.sh dist --edition=personal
# 企业版
bash packaging/mac/build_mac.sh dist --edition=enterprise
```

产物互不覆盖：
- `build/dist-personal/Chayuan-<v>-macos-arm64-personal-dist.dmg`
- `build/dist-enterprise/Chayuan-<v>-macos-arm64-enterprise-dist.dmg`

## 企业版 vs 个人版功能对比

| 维度 | 个人版 | 企业版 |
|---|---|---|
| 默认数据库 | SQLite（`~/.chayuan/data/data/knowledge_base/info.db`） | Postgres（URL 见下） |
| 默认向量库 | FAISS（本地 index 文件） | Milvus（`DEFAULT_VS_TYPE=milvus`） |
| 共享状态 / 限流 / 语义缓存 | **关闭**（单机无 Redis） | 开启（`REDIS_URL=redis://redis:6379/0`） |
| 鉴权 | 关闭（`AUTH_REQUIRED=false`） | 强制开启（`AUTH_REQUIRED=true`） |
| 注册开放 | 允许（`AUTH_ALLOW_REGISTRATION=true`） | 禁止（`AUTH_ALLOW_REGISTRATION=false`） |
| Uvicorn workers | 1 | 4 |
| DEPLOYMENT_MODE | `dev` | `prod`（面板 lint 严格模式） |
| 异步入库 / 语义缓存 | 关 | 开（`INGEST_ASYNC_ENABLED=true`、`SEMANTIC_CACHE_ENABLED=true`） |
| JWT_SECRET | 空（auth 未开启） | 随机 48 字节（`secrets.token_urlsafe`） |
| Dock 名称 / Bundle ID | Chayuan / cn.bjzlnkj.chayuan | Chayuan Enterprise / cn.bjzlnkj.chayuan.enterprise |

## 底层实现：一条 edition 开关线

整个差异链路只有一条，便于审计：

```
build_mac.sh --edition=enterprise
  ├─ 写 Resources/.chayuan_edition = "enterprise"
  ├─ Info.plist CFBundleName / BundleID 切成企业版品牌
  └─ DMG 文件名加 -enterprise 标识
           │
           ▼
launcher.sh 启动时
  ├─ 读 .chayuan_edition
  └─ export CHAYUAN_EDITION=enterprise
     export CHAYUAN_INIT_PROFILE=prod      # 默认值，用户已设则保留
     export CHAYUAN_ROOT_IGNORE_STATE=1    # 忽略 CLI state.json，固定 ~/.chayuan/data
           │
           ▼
tray.common.ensure_bootstrapped()
  └─ 看到 CHAYUAN_EDITION=enterprise → 用 --profile prod 跑 chayuan init -q
           │
           ▼
chayuan.cli.init(profile="prod")
  ├─ Settings.createl_all_templates()    # 先写 sqlite+faiss 默认模板
  └─ init_prod_profile.apply_prod_profile()  # 覆盖为 postgres+milvus+redis+auth
```

CLI 也接受 `--profile enterprise`（内部映射到 `prod`），这样开发环境里 `chayuan init --profile enterprise` 和打包出来的企业版行为一致。

## M7.5 ✅ — 内嵌 Postgres / Redis / Milvus-Lite 已交付

自 M7.5 起，企业版 DMG **完全自包含**：双击就能同时拉起 Postgres 17.9 + Redis 8.6.2 + Milvus-Lite，用户不需要自备任何后端服务。

### 交付的架构

```
Chayuan Enterprise.app/
├── Contents/
│   ├── MacOS/Chayuan           # launcher.sh
│   ├── Resources/
│   │   ├── .chayuan_edition    # "enterprise"
│   │   ├── src/chayuan-server/
│   │   └── dist/
│   │       ├── python-runtime.tar.gz       # 19 MB — pbs 3.11
│   │       ├── wheels/                      # 368 MB — 220+ 运行时 whl
│   │       ├── services-runtime.tar.gz     # 42 MB — PG + Redis 二进制（含全部依赖 lib）
│   │       ├── first_run.sh                # 解压以上全部到 ~/.chayuan/
│   │       └── requirements-runtime.txt

~/.chayuan/
├── python/                     # pbs Python（首次运行解压）
├── services/                   # first_run 解包的服务 env
│   ├── bin/
│   │   ├── postgres, pg_ctl, initdb, psql
│   │   ├── redis-server, redis-cli
│   ├── lib/                    # libpq / openssl / libicu / ...（@loader_path/../lib/ 相对 rpath）
│   ├── postgres/
│   │   ├── data/               # initdb 输出，superuser=chayuan，scram-sha-256
│   │   ├── socket/             # unix socket（socket_dir）—— SQLAlchemy host= 指向这里
│   │   ├── .pgpass             # 随机密码（600 权限）
│   │   └── postgres.log
│   ├── redis/
│   │   ├── data/               # RDB
│   │   ├── redis.conf          # listen 127.0.0.1:auto_port
│   │   └── redis.log
│   └── state.json              # 当前跑起来的 URI / 端口，排障用
├── logs/ data/ .installed
```

### 生命周期

```
双击 Chayuan Enterprise.app
  └── launcher.sh
      ├── 首次：first_run.sh 解包 python-runtime + wheels + services-runtime
      └── 启动 python -m chayuan.tray.entry
          ├── ensure_bootstrapped → chayuan init --profile prod
          └── Backend.start()
              ├── services.manager.ensure_up()
              │   ├── postgres.ensure_initdb()     # 幂等；首次 initdb -D data/ -U chayuan --pwfile
              │   ├── postgres.start()             # pg_ctl start，unix socket only
              │   ├── postgres.ensure_database()   # CREATE DATABASE chayuan
              │   └── redis.start()                # port 探活 6379→16379→26379；bind 127.0.0.1
              ├── services.manager.apply_to_settings_yaml()
              │   ├── 把 basic_settings.yaml 里的 @postgres:5432 → @/chayuan?host=<socket_dir>
              │   └── 把 kb_settings.yaml 里的 milvus.host=milvus → milvus-lite.db 路径
              └── exec chayuan.cli start -a
                  ├── API :62581 （psycopg2 连 unix socket 的 PG）
                  ├── WebUI Streamlit :8501
                  └── 配置面板 NiceGUI :8502
退出 tray (⌘Q)
  └── Backend.stop()
      ├── kill chayuan start -a 进程组
      └── services.manager.shutdown()
          ├── redis.stop()       # SIGTERM + pidfile 清理
          └── postgres.stop()    # pg_ctl stop -m fast
```

### 技术关键点

| 问题 | 解决方案 |
|---|---|
| PG / Redis 二进制源 | conda-forge osx-arm64：`postgresql=17 redis-server`，构建机打包 42MB tarball。`@loader_path/../lib/` 相对 rpath，搬到任意路径都跑 |
| 端口冲突（5432 / 6379 被占） | PG 只用 unix socket，0 TCP 端口；Redis 自动探活 6379→16379→26379→36379 |
| 路径里有空格（`Chayuan Enterprise.app`） | conda 安装期忌讳空格 → 我们用 tarball 解包到用户目录；运行时 binary 的 rpath 相对，任意路径都能跑 |
| 幂等 / 重装 | first_run.sh 解包时 `--exclude 'postgres/data' --exclude 'redis/data'`，用户数据库不丢 |
| initdb 密码管理 | 首次 initdb 时 `secrets.token_urlsafe(24)`，落到 `.pgpass`（600 权限）；SQLAlchemy URI 从这里读取 |
| 超级账户权限 | 首次启动生成 random pwd → 用户对 PG 里"别的 DB / 别的 role"没有可预测的攻击面；所有配置用 socket，`listen_addresses = ''` 完全关 TCP |

### 实测数据（build 本机）

| 指标 | 数值 |
|---|---|
| Enterprise dist DMG 大小 | **451 MB**（personal dist 389 MB + 62 MB services） |
| Personal dist DMG 大小（参照） | 389 MB |
| 双击到全服务就绪时间 | **~35 s**（含解压 pbs + pip install 220 whl + initdb + pg_ctl start + redis start + `chayuan start -a` 全栈） |
| 二次启动时间（python/services 已就位） | ~8 s |
| PG `SELECT version()` 实连 | PostgreSQL 17.9 on aarch64-apple-darwin20.0.0 ✅ |
| Redis SET/GET 往返 | ✅ |

### 用户自备外部服务仍然支持

企业用户已经有自己的 PG / Redis / Milvus 集群的，仍可在首次启动前设置环境变量覆盖 embedded 默认（注意：**必须在 .app 启动前设好**，例如写进 `~/Library/LaunchAgents/` 或 `/etc/launchd.conf`）：

```bash
# 关掉 embedded 服务，用外部地址
export CHAYUAN_EMBEDDED_SERVICES=0
export CHAYUAN_DB_URI="postgresql+psycopg2://user:pass@pg.internal:5432/chayuan"
export CHAYUAN_REDIS_URL="redis://redis.internal:6379/0"
# Milvus：首次启动后到配置面板改 kb_settings.yaml 里的 kbs_config.milvus.host/port
```

`CHAYUAN_EMBEDDED_SERVICES=0` 时 `tray.services.should_enable()` 返回 False，整个 embedded 栈不会启动，`init_prod_profile` 的原始 prod URL 被保留（`@postgres:5432` 这种 docker-compose 服务名），外部由用户负责解析。

## 测试命令参考

```bash
# ---- 个人版 ----
rm -rf ~/.chayuan
bash packaging/mac/build_mac.sh dev --edition=personal
open "packaging/build/dev-personal/Chayuan.app"
sleep 10
grep -E "AUTH_REQUIRED|DEPLOYMENT_MODE|DEFAULT_VS_TYPE" \
    ~/.chayuan/data/basic_settings.yaml ~/.chayuan/data/kb_settings.yaml
# 期望：AUTH_REQUIRED=false / DEPLOYMENT_MODE=dev / DEFAULT_VS_TYPE=faiss

# ---- 企业版 ----
rm -rf ~/.chayuan
bash packaging/mac/build_mac.sh dev --edition=enterprise
open "packaging/build/dev-enterprise/Chayuan Enterprise.app"
sleep 15
grep -E "AUTH_REQUIRED|DEPLOYMENT_MODE|SQLALCHEMY_DATABASE_URI|DEFAULT_VS_TYPE|REDIS_URL" \
    ~/.chayuan/data/basic_settings.yaml ~/.chayuan/data/kb_settings.yaml
# 期望：AUTH_REQUIRED=true / DEPLOYMENT_MODE=prod / URI 指向 postgres / DEFAULT_VS_TYPE=milvus
```

## 已知约束

- **Windows / Linux 的 enterprise 打包** 需要在 `build_win.ps1` / `build_linux.sh` 里对称加 `--edition` 支持以及 `vendor/services/chayuan-services-<platform>.tar.gz`（用 conda-forge 对应平台 repodata 构建）。架构完全相同，只是要分别在目标机器上打一次 services tarball。
- **个人版用户升级到企业版** 现在只能"重装 DMG + 清 ~/.chayuan"；future work：企业版首次启动时探测已有 personal 的 SQLite 数据，一键迁移到 Postgres（`pgloader` 或自写 SQLAlchemy 迁移脚本）。
- **DMG 签名 + 公证（M8）**。当前 enterprise DMG 也是未签名状态；Gatekeeper 拦截的问题在 M8 统一解决。
- **企业版多实例启动**。如果用户双击了两次（LSUIElement 允许），第二个 tray 会发现 `postmaster.pid` 已存在而跳过 initdb，但可能会尝试 start 一个已 running 的 PG，导致报错。建议加单实例锁（`fcntl.flock` 或 `NSApplication.activateIgnoringOtherApps`）——M7.6 里做。
