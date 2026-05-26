from __future__ import annotations

import os
import re as _re
from pathlib import Path
import sys
import typing as t

# 40 题 P2:不再 ``import nltk`` — 整套 nltk 加载耗 ~0.8 秒,但本模块只用它
# 注册一次自定义 ``nltk_data`` 路径。改用 ``NLTK_DATA`` 环境变量(nltk 在自身
# import 时会自动 read 此 env 加入 ``nltk.data.path``)。延迟到模块末尾 set,
# 那时 ``Settings`` 已实例化,能拿到 NLTK_DATA_PATH。
from pydantic import field_validator

from chayuan import __version__
from chayuan.pydantic_settings_file import *


# 察元AI助手数据目录：
#   1) 环境变量 $CHAYUAN_ROOT（显式）
#   2) $XDG_DATA_HOME/chayuan（XDG 显式时）
#   3) OS 默认：macOS→~/Library/Application Support/chayuan；Windows→%APPDATA%\chayuan；
#      Linux/其它→~/.local/share/chayuan
# 解析逻辑集中在 chayuan.paths，避免 cwd 作为 fallback 带来的意外。
from chayuan.paths import resolve_chayuan_root as _resolve_root, ensure_root as _ensure_root, describe as _describe_root

_CHAYUAN_ROOT_INFO = _ensure_root(_resolve_root())
CHAYUAN_ROOT = _CHAYUAN_ROOT_INFO.path
# 仅当命中了非 env 来源时额外打一行 stderr，方便用户立刻发现 chayuan 把数据放哪儿了。
# 这里不用 loguru 以免在 settings import 时反向引入循环依赖。
if _CHAYUAN_ROOT_INFO.is_default:
    sys.stderr.write(f"[chayuan] {_describe_root(_CHAYUAN_ROOT_INFO)}\n")

# ---------------------------------------------------------------------------
# 路径字段 fallback helpers
#
# 目的：让 KB_ROOT_PATH / DB_ROOT_PATH / SQLALCHEMY_DATABASE_URI 这三个字段具备
# 「留空或写坏时自动跟随 CHAYUAN_ROOT」的能力，避免：
#   1. 从 Langchain-Chatchat 等老项目迁移过来的 yaml 里写死了 `D:\\…` 这种
#      完全不属于当前 OS 的绝对路径，程序直接落不到任何真实目录；
#   2. 用户自行 `chayuan init --profile prod` 后把 CHAYUAN_ROOT 换了，这几个
#      字段却还指着旧路径，出现「配置面板看到的目录」与「实际数据所在目录」
#      不同步的奇怪体验。
#
# 仅在**明显异常**时回退，不会覆盖用户显式写入的合法路径：
#   - 空字符串 / None → 回退
#   - `[A-Za-z]:\\…` 形式但当前 OS 非 Windows → 回退并打 warning
#   - `/foo/bar` 形式但当前 OS 是 Windows → 回退并打 warning
# 其它情况（含非标准但合法的路径）一律尊重用户输入，由其他层报错。
# ---------------------------------------------------------------------------


def _looks_windows_abspath(s: str) -> bool:
    return bool(_re.match(r"^[A-Za-z]:[\\/]", s)) or s.startswith("\\\\")


def _foreign_os_path(s: str) -> bool:
    if not s:
        return False
    if sys.platform == "win32":
        # Unix abs path on Windows.
        return s.startswith("/") and not s.startswith("//")
    return _looks_windows_abspath(s)


def _default_kb_root_path() -> str:
    return str(CHAYUAN_ROOT / "data" / "knowledge_base")


def _default_db_root_path() -> str:
    return str(CHAYUAN_ROOT / "data" / "knowledge_base" / "info.db")


def _default_sqlalchemy_uri() -> str:
    # sqlite URL：三个斜杠代表绝对路径；Windows 下 driver 自己能处理反斜杠。
    return "sqlite:///" + _default_db_root_path()


def _warn_path_fallback(field: str, original: str, fallback: str) -> None:
    sys.stderr.write(
        f"[chayuan][settings] 配置项 {field}={original!r} 与当前 OS 不兼容 / 为空，"
        f"已自动回退到 {fallback!r}。如需保留原值，请检查 basic_settings.yaml。\n"
    )


XF_MODELS_TYPES = {
    "text2image": {"model_family": ["stable_diffusion"]},
    "image2image": {"model_family": ["stable_diffusion"]},
    "speech2text": {"model_family": ["whisper"]},
    "text2speech": {"model_family": ["ChatTTS"]},
}


class BasicSettings(BaseFileSettings):
    """
    服务器基本配置信息
    除 log_verbose/HTTPX_DEFAULT_TIMEOUT 修改后即时生效
    其它配置项修改后都需要重启服务器才能生效，服务运行期间请勿修改
    """

    model_config = SettingsConfigDict(yaml_file=CHAYUAN_ROOT / "basic_settings.yaml")

    version: str = __version__
    """生成该配置模板的项目代码版本，如这里的值与程序实际版本不一致，建议重建配置文件模板"""

    log_verbose: bool = False
    """是否开启日志详细信息"""

    HTTPX_DEFAULT_TIMEOUT: float = 300
    """httpx 请求默认超时时间（秒）。如果加载模型或对话较慢，出现超时错误，可以适当加大该值。"""

    # @computed_field
    @cached_property
    def PACKAGE_ROOT(self) -> Path:
        """代码根目录"""
        return Path(__file__).parent

    # @computed_field
    @cached_property
    def DATA_PATH(self) -> Path:
        """用户数据根目录"""
        p = CHAYUAN_ROOT / "data"
        return p

    # @computed_field
    @cached_property
    def IMG_DIR(self) -> Path:
        """项目相关图片目录"""
        p = self.PACKAGE_ROOT / "img"
        return p

    # @computed_field
    @cached_property
    def NLTK_DATA_PATH(self) -> Path:
        """nltk 模型存储路径"""
        p = self.PACKAGE_ROOT / "data/nltk_data"
        return p

    # @computed_field
    @cached_property
    def LOG_PATH(self) -> Path:
        """日志存储路径"""
        p = self.DATA_PATH / "logs"
        return p

    # @computed_field
    @cached_property
    def MEDIA_PATH(self) -> Path:
        """模型生成内容（图片、视频、音频等）保存位置"""
        p = self.DATA_PATH / "media"
        return p

    # @computed_field
    @cached_property
    def BASE_TEMP_DIR(self) -> Path:
        """临时文件目录，主要用于文件对话"""
        p = self.DATA_PATH / "temp"
        (p / "openai_files").mkdir(parents=True, exist_ok=True)
        return p

    KB_ROOT_PATH: str = ""
    """知识库默认存储路径。留空时自动派生为 ``$CHAYUAN_ROOT/data/knowledge_base``；
    若显式写入绝对路径则优先使用该路径。自 v1 起不再写死模板里的绝对路径，避免迁移
    不同机器时仍然指向上一台机器的目录。"""

    DB_ROOT_PATH: str = ""
    """数据库默认存储路径。留空时自动派生为 ``$CHAYUAN_ROOT/data/knowledge_base/info.db``。
    如果使用 sqlite，可以直接修改 DB_ROOT_PATH；如果使用其它数据库，请直接修改
    SQLALCHEMY_DATABASE_URI。"""

    SQLALCHEMY_DATABASE_URI: str = ""
    """知识库 / 业务元数据的 SQLAlchemy 连接 URI。留空时默认 sqlite，指向
    DB_ROOT_PATH；生产环境建议改成 ``postgresql+psycopg2://user:pw@host:5432/chayuan``
    或 MySQL。写 sqlite 绝对路径时需使用三斜杠，例如
    ``sqlite:////abs/path/info.db``。"""

    # -- 路径字段容错：空值 / 异构 OS 路径自动回退 ---------------------------

    @field_validator("KB_ROOT_PATH", mode="before")
    @classmethod
    def _fallback_kb_root_path(cls, v):
        s = "" if v is None else str(v).strip()
        if not s:
            return _default_kb_root_path()
        if _foreign_os_path(s):
            _warn_path_fallback("KB_ROOT_PATH", s, _default_kb_root_path())
            return _default_kb_root_path()
        return s

    @field_validator("DB_ROOT_PATH", mode="before")
    @classmethod
    def _fallback_db_root_path(cls, v):
        s = "" if v is None else str(v).strip()
        if not s:
            return _default_db_root_path()
        if _foreign_os_path(s):
            _warn_path_fallback("DB_ROOT_PATH", s, _default_db_root_path())
            return _default_db_root_path()
        return s

    @field_validator("SQLALCHEMY_DATABASE_URI", mode="before")
    @classmethod
    def _fallback_sqlalchemy_uri(cls, v):
        s = "" if v is None else str(v).strip()
        if not s:
            return _default_sqlalchemy_uri()
        # 只对 sqlite:/// 前缀做 OS 兼容校验，远程 URI（postgresql / mysql 等）直接放行。
        low = s.lower()
        if low.startswith("sqlite:///") or low.startswith("sqlite:////"):
            # 裁掉 scheme，拿真实文件路径参与 OS 校验。
            path_part = s.split("sqlite:///", 1)[-1].lstrip("/")
            if _foreign_os_path(path_part) or _foreign_os_path("/" + path_part):
                _warn_path_fallback("SQLALCHEMY_DATABASE_URI", s, _default_sqlalchemy_uri())
                return _default_sqlalchemy_uri()
        return s

    # -- DEPLOYMENT_MODE 容错：空值 / 未知值 / 大小写混用 自动回退到 'dev' ------
    # 下游（health.py / scalability.py / db/base.py）有些路径是直接
    # ``bs.DEPLOYMENT_MODE == "prod"``，所以这里必须在 pydantic 校验阶段就把
    # 非法值规整成合法的 Literal 值，否则用户在 YAML 里写成 ``DEPLOYMENT_MODE: ''``
    # 或误写成 ``PROD`` 就会把整个 webui 进程启动时打挂。
    @field_validator("DEPLOYMENT_MODE", mode="before")
    @classmethod
    def _fallback_deployment_mode(cls, v):
        s = "" if v is None else str(v).strip().lower()
        if s in ("dev", "prod"):
            return s
        if not s:
            # 配置留白属于用户常见写法（尤其是从旧版 yaml 升级过来的），静默回退。
            return "dev"
        sys.stderr.write(
            f"[chayuan][settings] 配置项 DEPLOYMENT_MODE={v!r} 非法（只接受 'dev' / 'prod'），"
            f"已自动回退到 'dev'。\n"
        )
        return "dev"

    OPEN_CROSS_DOMAIN: bool = True
    """API 是否开启跨域"""

    DEFAULT_BIND_HOST: str = "0.0.0.0" if sys.platform != "win32" else "127.0.0.1"
    """
    各服务器默认绑定host。如改为"0.0.0.0"需要修改下方所有XX_SERVER的host
    Windows 下助手界面自动弹出浏览器时，如果地址为 "0.0.0.0" 是无法访问的，需要手动修改地址栏
    """

    API_SERVER: dict = {"host": DEFAULT_BIND_HOST, "port": 62581, "public_host": "127.0.0.1", "public_port": 62581}
    """API 服务器地址。其中 public_host 用于生成云服务公网访问链接（如知识库文档链接）"""

    CONFIG_SERVER: dict = {"host": DEFAULT_BIND_HOST, "port": 8502}
    """察元AI助手配置面板（NiceGUI）监听地址。通过 `chayuan start -c` 独立启动或 `-a` 联启。"""

    # ---- 运行时端口分配 / vendor 服务（T-runtime） -----------------------
    # 旧字段（API_SERVER / CONFIG_SERVER）只声明"想监听哪个端口"；下面这一组字段
    # 让 vendor 二进制 / docker 起的 PostgreSQL / Redis / MinIO / Milvus / Ollama
    # 也能走统一的 PortAllocator：
    #   - PORT_PREFER_DEFAULT=True    优先尝试 yaml 里写的端口；占用则在范围内自动 bump
    #   - PORT_RANGE                  动态分配的候选范围（默认 40000-60999）
    #   - VENDOR_PREFERRED_PORTS      每个 vendor 服务的"开箱默认大端口"（避开
    #                                 各家上游的标准端口，防止与开发机自带的服务冲突）
    # 这些字段不直接控制运行——真正的分配发生在 chayuan.server.runtime.PortAllocator，
    # 由 startup.py / chayuan service 子命令调用；本配置只提供"用户偏好"。

    PORT_PREFER_DEFAULT: bool = True
    """端口分配时是否优先尝试 yaml 中写的偏好端口；冲突时再让 PortAllocator 自动让位。
    设 False 时直接走 PortAllocator 范围内首个可用端口（适合 CI / 容器场景）。"""

    PORT_RANGE: t.Tuple[int, int] = (40000, 60999)
    """PortAllocator 的动态候选区间（两端含）。刻意避开 < 32768 的常见服务端口段，
    也避开 Linux 临时端口段 32768-60999 的低段，留出余量。"""

    VENDOR_PREFERRED_PORTS: t.Dict[str, int] = {
        # 开箱默认全部 +30000：postgres 5432→35432、redis 6379→36379、minio 9000→39000……
        # 任意被占用都会被 PortAllocator 自动改成下一个空闲端口，并写到 runtime.json。
        "api":          62581,
        "config_panel": 8502,
        "postgres":     35432,
        "redis":        36379,
        "minio":        39000,
        "minio_console":39001,
        "milvus":       39530,
        "milvus_metrics": 39091,
        "elasticsearch":39200,
        "onlyoffice":   38080,
        "ollama":       31434,
        "llama_cpp":    38081,
        "vllm":         38000,
        "whisper_cpp":  38010,
        "funasr":       38020,
        "piper":        38030,
        "cosyvoice":    38040,
        "rapidocr":     38050,
        "paddleocr":    38060,
        "comfyui":      38188,
        "infinity":     37997,
    }
    """每个 vendor 服务的"开箱默认大端口"。yaml 里可单独覆盖某项；
    `chayuan service info` 会展示最终生效的端口、地址、用户名/密码。"""

    VENDOR_AUTOSTART: t.List[str] = []
    """启动 chayuan 时自动起来的 vendor 服务列表（按 vendor/ 子目录名）。
    例如 ['ollama', 'redis']。需要 vendor/services/<name>/ 或 vendor/runtimes/<name>/
    下提前放好二进制或 docker-compose.yml；否则该项被跳过并打 warning。"""

    PANEL_USERNAME: str = ""
    """配置面板登录用户名；与 PANEL_PASSWORD_HASH 均非空时才允许登录，可通过 `chayuan update username` 设置。"""

    PANEL_PASSWORD_HASH: str = ""
    """配置面板登录密码的 PBKDF2-SHA256 加盐散列；格式 `pbkdf2_sha256$iterations$salt_hex$hash_hex`。请勿手写，使用 `chayuan update password` 生成。"""

    PANEL_SESSION_SECRET: str = ""
    """配置面板 NiceGUI 会话密钥；留空则启动时自动生成并写回 `basic_settings.yaml`。"""

    PANEL_LOGIN_PATH: str = ""
    """配置面板登录页随机路径段（形如 `xkjmwpof`，不带前导 `/`）。
    留空时将在首次启动或执行 `chayuan update path` 时自动生成 8 位小写字母；
    作用类似访问口令——没有正确路径的访问者会直接看到 404。
    允许字符：`^[a-z0-9_-]{3,32}$`。保留路径 `dashboard/logout/static/_nicegui` 不可复用。"""

    # ------------------------------------------------------------------
    # 性能 / 可扩展性（面向 5000+ 并发场景；详见 docs/scalability.md）
    # ------------------------------------------------------------------

    DEPLOYMENT_MODE: t.Literal["dev", "prod"] = "dev"
    """部署模式。`prod` 时配置面板会对 SQLite/FAISS/单 worker 等不达标项亮红；
    `dev` 仅作提示。切换本字段不会自动改其它配置，只影响校验严格程度。"""

    UVICORN_WORKERS: int = 1
    """API 进程 worker 数。单 worker 约支持数百 RPS；生产建议 `2 * CPU 核心数 + 1`。
    > 1 时程序走多进程启动路径（uvicorn --workers），要求通过应用工厂而非已实例化的 app。"""

    DB_POOL_SIZE: int = 10
    """SQLAlchemy 连接池常驻连接数。生产 Postgres 建议 ≥20，按 `workers × pool_size`
    估算真实数据库侧总连接。SQLite 下本值无意义，程序会自动降级。"""

    DB_MAX_OVERFLOW: int = 20
    """SQLAlchemy 连接池额外可扩展连接数。峰值连接 = DB_POOL_SIZE + DB_MAX_OVERFLOW。"""

    DB_POOL_RECYCLE: int = 3600
    """连接回收时间（秒）。Postgres/MySQL 在 1h 空闲后常会断，设 3600 即可。
    -1 表示不回收。"""

    DB_POOL_PRE_PING: bool = True
    """每次从池取连接时先 `SELECT 1` 探活，避免拿到被 DB/网关关掉的死连接。
    开销很小，生产**强烈建议打开**。"""

    DB_POOL_TIMEOUT: int = 30
    """获取连接的最长等待秒数，超时抛异常；默认 30。"""

    DB_MIGRATION_MODE: str = "create_all"
    """启动期 schema 管理策略（T7）：
    - ``alembic``    ：运行 ``alembic upgrade head``（多实例 advisory lock 串行），**生产**
    - ``create_all`` ：旧行为，``Base.metadata.create_all``；dev 或单副本部署
    - ``off``        ：完全跳过（K8s init-container / CI 迁移场景）
    """

    ASYNC_DATABASE_URI: str = ""
    """异步 DB 连接串（T7）。留空时自动把 SQLALCHEMY_DATABASE_URI 中的驱动前缀替换为
    异步驱动（mysql+pymysql → mysql+asyncmy 等）。该 URI 用于 async repository。"""

    # ---- T9 多租户 ------------------------------------------------------------

    RLS_ENABLED: bool = False
    """是否启用 Postgres Row Level Security（T9）。
    - True + Postgres：session 开启时 ``SET LOCAL app.tenant_id``，配合 0003_enable_rls 策略
    - False：完全 no-op，等价旧行为
    - 非 Postgres：设 True 也不报错，等价 no-op（上层 AST 注入仍能工作）"""

    TENANT_HEADER: str = "X-Tenant-Id"
    """TenantContextMiddleware 读取 tenant_id 的 HTTP 头名字（T9）。
    优先级：Header > JWT user.tenant_id > role。网关对接时用自定义名可改这里。"""

    REDIS_URL: str = ""
    """Redis 连接串（例如 `redis://127.0.0.1:6379/0`）。用于限流 / 会话 / 流式
    buffer / 异步队列等共享状态；留空表示禁用，此时多副本无法保证一致性。"""

    RATE_LIMIT_ENABLED: bool = False
    """是否开启 API 侧限流中间件；启用需配置 REDIS_URL。"""

    RATE_LIMIT_PER_MINUTE: int = 120
    """每 IP / 每用户每分钟允许的请求数上限；仅 RATE_LIMIT_ENABLED=true 生效。"""

    REQUEST_ID_HEADER: str = "X-Request-ID"
    """请求 ID 头；缺失时自动生成 UUID，用于日志串联。"""

    # ---- ChatGraph / 治理 / Guardrail 顶层开关（P1-6 / P1-7 / P1-9） ----
    USE_CHAT_GRAPH: bool = False
    """是否把 /chat/kb_chat、/chat/file_chat 等老入口路由到新 ChatGraph（LangGraph + 治理）。
    默认 False 保持兼容；确认验证通过后建议改为 True。/chat/v2/chat 永远走 ChatGraph。"""

    GOVERNANCE_ENABLED: bool = True
    """是否启用数据治理（PII / 脱敏 / 血缘 / 配额）。生产强烈建议 True。"""

    GUARDRAIL_ENABLED: bool = False
    """是否启用 Guardrail（提示词注入 / 输出毒性检测）。依赖 ``GUARDRAIL_BACKEND``。"""

    GUARDRAIL_BACKEND: str = "rules"
    """Guardrail 后端：rules（内置规则 / 默认） / nemo / llama_guard / disabled。"""

    GUARDRAIL_LLAMA_MODEL: str = ""
    """Llama Guard 后端使用的 LLM 模型名（需在 model_settings 里配置）。"""

    GUARDRAIL_NEMO_CONFIG: str = ""
    """NeMo Guardrails 的 rails 配置目录路径；GUARDRAIL_BACKEND=nemo 时必填。"""

    CHAYUAN_LANGFUSE_DISABLE: bool = False
    """一键禁用 Langfuse；等价环境变量 CHAYUAN_LANGFUSE_DISABLE=1；生产应急开关。
    无论是否配置 LANGFUSE_* 凭据，此开关 True 时全流程跳过 Langfuse 调用。"""

    LANGFUSE_HOST: str = ""
    """Langfuse 服务端地址（自托管：http://127.0.0.1:3000；SaaS：https://cloud.langfuse.com）。
    优先级：环境变量 $LANGFUSE_HOST > 本字段。两者都为空即视为未启用。"""

    LANGFUSE_PUBLIC_KEY: str = ""
    """Langfuse Public Key（pk-lf-...）。生产建议走 env，避免写入 yaml；
    yaml 字段仅作为"未设 env 时的默认值"，方便单机试跑。"""

    LANGFUSE_SECRET_KEY: str = ""
    """Langfuse Secret Key（sk-lf-...）。密码型字段；面板展示脱敏。"""

    # ------------------------------------------------------------------
    # 可观测性 / 弹性（P2；需要 `prometheus_client` / `opentelemetry-*` / `tenacity`
    # 等依赖，缺包时各能力会静默降级——详见 docs/scalability.md）
    # ------------------------------------------------------------------

    JSON_LOGS: bool = False
    """是否把 logging 输出切为 JSON 格式（Loki / ELK 友好）。
    开启后每条日志自带 `request_id` 字段，便于与 nginx / 前端日志串联。"""

    VERBOSE_LOGS: bool = False
    """是否打印第三方库的详细日志(transformers / urllib3 / httpx / langchain /
    pymilvus / sqlalchemy 引擎 SQL 等)。

    默认 False:
      - 把上述噪声大户全部限制到 WARNING 及以上,过滤 transformers 的
        `Accessing __path__ from ...` / `Examining the path of ... raised: ...` 这类
        deprecation/probe 噪声(冷启动每加载一个 image_processor 都打一行,
        非常吵但对运维没有信息量)
      - TRANSFORMERS_VERBOSITY 同步设为 error

    设为 True(配置面板里勾上"详细日志"):
      - 解除限制,所有第三方日志按各自默认级别输出,便于排查启动期问题。
    """

    METRICS_ENABLED: bool = True
    """是否开启 Prometheus 埋点（QPS、延迟、状态码、in-flight）。
    开启后 `/metrics` 暴露 Prometheus exposition；缺 `prometheus_client` 时仍可降级。"""

    OTEL_ENABLED: bool = False
    """是否启用 OpenTelemetry 链路追踪。启用后会按标准 `OTEL_EXPORTER_OTLP_ENDPOINT`
    等环境变量上报 spans；缺 `opentelemetry-*` 依赖时跳过，不影响业务。"""

    OTEL_SERVICE_NAME: str = "chayuan-api"
    """OTEL 服务名，默认 `chayuan-api`。同一服务的多副本共享同一名称，靠实例属性区分。"""

    LLM_RETRY_ATTEMPTS: int = 3
    """LLM 调用失败重试次数（不含第一次）。0 表示关闭重试。"""

    LLM_RETRY_WAIT_MAX: float = 8.0
    """LLM 重试指数退避的最大等待秒数。初始 0.5s，按 2 倍递增，到此封顶。"""

    LLM_TIMEOUT_SECONDS: float = 120.0
    """单次 LLM 调用的超时（秒）。超过即算失败，参与重试计数。"""

    LLM_BREAKER_FAILURE_THRESHOLD: int = 5
    """P2-7：LLM provider 连续失败 N 次打开熔断。阈值越低越保守、越容易短路掉
    抖动流量；生产建议 5~10 之间。"""

    LLM_BREAKER_COOLDOWN_SECONDS: float = 60.0
    """P2-7：熔断冷却秒数。超过后进入半开放行探针；探针失败则再度打开，成功则关闭。"""

    HIVE_INTROSPECT_MAX_DBS: int = 10
    """P2-14-c：Hive 多库 introspect 上限。逐库 SHOW TABLES 代价随库数线性增长；
    绝大多数仓库前 10 库覆盖 90% 业务表。需要更多请按需调大。"""

    VS_AUTO_FANOUT_MAX_COLLECTIONS: int = 20
    """用户未指定 allowed_collections 时，ExternalVsConnector 会现采后端全部 collection
    做 fan-out 检索。这里是兜底上限：超出部分按名字序保留前 N 个。防止意外把
    拥有几百个 collection 的大集群一次扫爆。默认 20，生产按自己业务调整。"""

    # ------------------------------------------------------------------
    # 多用户鉴权（P3）
    # ------------------------------------------------------------------

    AUTH_REQUIRED: bool = False
    """是否强制要求所有业务接口登录。开启后 /chat/* /knowledge_base/* /v1/* 等
    缺失合法 Token 时返回 401。`/healthz /readyz /metrics /auth/*` 以及 `/docs /img /media`
    永远放行。
    单机调试建议关；多用户 / 生产必须开。"""

    JWT_SECRET: str = ""
    """JWT 签名密钥（HS256）。必须足够长且保密；留空时进程会自动生成一次性随机密钥
    并打 warning，**所有已签发的 token 在重启后全部作废**，仅开发方便。"""

    JWT_ALGORITHM: t.Literal["HS256", "HS384", "HS512"] = "HS256"
    """HMAC 算法；需要 RS256 时请在代码中显式扩展，避免配置误用。"""

    JWT_ACCESS_TTL_SECONDS: int = 3600
    """access token 默认 1 小时。超过需要用 refresh token 换。"""

    JWT_REFRESH_TTL_SECONDS: int = 7 * 24 * 3600
    """refresh token 默认 7 天。"""

    AUTH_DEFAULT_ADMIN_USERNAME: str = "admin"
    """首次启动若没有任何用户，按这个用户名自动建一个 admin。"""

    AUTH_DEFAULT_ADMIN_PASSWORD: str = ""
    """默认管理员密码。留空则启动时**随机生成并打印一次**（务必保存）。
    生产请显式设置并立即改密。"""

    AUTH_ALLOW_REGISTRATION: bool = True
    """是否开放自助注册。企业内网场景建议 False，仅由管理员在「用户管理」页新增。"""

    # ------------------------------------------------------------------
    # 异步入库队列（P3 / Arq）
    # ------------------------------------------------------------------

    INGEST_ASYNC_ENABLED: bool = False
    """是否开启异步入库（Arq）。关：upload_docs / recreate_vector_store 同步阻塞；
    开：同请求返回 task_id，由 `chayuan worker` 进程后台完成。
    依赖 REDIS_URL 与 `pip install arq`。"""

    ARQ_QUEUE_NAME: str = "chayuan:ingest"
    """Arq 队列名；多环境共用 Redis 时用来区分。"""

    ARQ_QUEUE_INGEST: str = "chayuan:queue:ingest"
    """文件上传、OCR、解析、普通知识库入库队列。"""

    ARQ_QUEUE_EMBEDDING: str = "chayuan:queue:embedding"
    """批量 embedding / 重建索引队列；可独立扩容 embedding worker。"""

    ARQ_QUEUE_OFFICE_VECTORIZE: str = "chayuan:queue:office_vectorize"
    """Office 文档向量化队列，避免与普通入库互相抢 worker。"""

    ARQ_QUEUE_ANNOTATION: str = "chayuan:queue:annotation"
    """数据标注导入、采样、评测等低优先级任务队列。"""

    ARQ_MAX_JOBS: int = 10
    """每个 worker 进程最大并发任务；按 CPU + 向量模型吞吐调。"""

    ARQ_TASK_MAX_TRIES: int = 2
    """Arq 任务默认最大尝试次数；不同任务类型可在队列 profile 中覆盖。"""

    ARQ_IDEMPOTENCY_TTL_SECONDS: int = 3600
    """任务幂等锁保留时间；同一文件 hash / 同一文档版本重复提交会返回已有任务。"""

    ARQ_DEAD_LETTER_TTL_SECONDS: int = 7 * 24 * 3600
    """失败任务死信保留时间，供运维排查和手工重放。"""

    # ------------------------------------------------------------------
    # 统一文件存储（MinIO / 本地磁盘）
    # ------------------------------------------------------------------

    FILE_STORAGE_BACKEND: t.Literal["local", "minio"] = "local"
    """文件存储后端。local：落 FILE_STORAGE_LOCAL_ROOT（与 KB_ROOT_PATH 同父级）。
    minio：推到 MinIO / 任意 S3-compatible；启用需 `pip install minio`。
    **切换时历史文件不会自动迁移**；请先用管理 API 做数据迁移。"""

    FILE_STORAGE_LOCAL_ROOT: str = ""
    """仅 FILE_STORAGE_BACKEND=local 生效；空则自动 = KB_ROOT_PATH 的父目录下 `storage/`。"""

    MINIO_ENDPOINT: str = ""
    """形如 `127.0.0.1:9000` 或 `https://s3.cn-north-1.amazonaws.com`。"""

    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_SECURE: bool = False
    MINIO_REGION: str = "us-east-1"
    MINIO_BUCKET_PREFIX: str = "chayuan"
    """Bucket 名 = `{prefix}-{namespace}`（默认 chayuan-kb-content 等）。"""

    MINIO_BUCKETS: t.Dict[str, str] = {}
    """可选 namespace → bucket 精确覆盖；示例：{"kb_content": "my-kb-bucket"}。"""

    # ------------------------------------------------------------------
    # 语义缓存（P3）
    # ------------------------------------------------------------------

    SEMANTIC_CACHE_ENABLED: bool = False
    """是否启用 LLM 回答语义缓存。默认关，避免时效/权限数据被错误复用。"""

    SEMANTIC_CACHE_TTL_SECONDS: int = 3600
    """缓存 TTL（秒）。太长容易返回"过期答案"。"""

    SEMANTIC_CACHE_NAMESPACE: str = "chayuan:semcache"
    """Redis key 前缀。"""

    SEMANTIC_CACHE_MIN_LEN: int = 5
    """问题字符数下限；太短的问句（如"你好"）一般不值得缓存。"""

    def make_dirs(self):
        '''创建所有数据目录'''
        for p in [
            self.DATA_PATH,
            self.MEDIA_PATH,
            self.LOG_PATH,
            self.BASE_TEMP_DIR,
        ]:
            p.mkdir(parents=True, exist_ok=True)
        for n in ["image", "audio", "video"]:
            (self.MEDIA_PATH / n).mkdir(parents=True, exist_ok=True)
        Path(self.KB_ROOT_PATH).mkdir(parents=True, exist_ok=True)


class KBSettings(BaseFileSettings):
    """知识库相关配置"""

    model_config = SettingsConfigDict(yaml_file=CHAYUAN_ROOT / "kb_settings.yaml")

    DEFAULT_KNOWLEDGE_BASE: str = "samples"
    """默认使用的知识库"""

    DEFAULT_VS_TYPE: t.Literal["faiss", "milvus", "zilliz", "pg", "es", "relyt", "chromadb"] = "faiss"
    """默认向量库/全文检索引擎类型"""

    KKFILEVIEW_URL: str = ""
    """kkFileView 旁车服务地址（如 ``http://127.0.0.1:8012``）。

    配置后客户端**所有文件预览**都会走 kkFileView,覆盖 100+ 格式
    (Office / WPS / Open/LibreOffice / OFD / 3D / CAD / PSD / 视频转码 等)。
    留空则前端走内置 renderer(pdf / docx / xlsx / pptx / 图片 / 视频 / md / txt)。

    部署:`docker run -d -p 8012:8012 keking/kkfileview` —— Docker 镜像已自带
    LibreOffice + ffmpeg + 3D loaders;客户端浏览器要能直连这个地址,
    kkFileView 后端也要能访问到 chayuan 服务的文件下载 URL(同机部署时
    通常配 ``http://host.docker.internal:62581``)。"""

    CACHED_VS_NUM: int = 1
    """缓存向量库数量（针对FAISS）"""

    CACHED_MEMO_VS_NUM: int = 10
    """缓存临时向量库数量（针对FAISS），用于文件对话"""

    CHUNK_SIZE: int = 750
    """知识库中单段文本长度(不适用MarkdownHeaderTextSplitter)"""

    OVERLAP_SIZE: int = 150
    """知识库中相邻文本重合长度(不适用MarkdownHeaderTextSplitter)"""

    VECTOR_SEARCH_TOP_K: int = 5 # TODO: 与 tool 配置项重复
    """最终返给 LLM / UI 的命中数;行业默认 5(retrieve 50 → rerank 取 top-5)。
    历史值是 3,2026-04 调整为 5(Cohere/Superlinked benchmark:rerank top-5 是 ROI 拐点)。"""

    SCORE_THRESHOLD: float = 0.3
    """知识库匹配相关度阈值。

    兼容两种语义(由 MilvusRetriever._filter_and_warn 自动识别):
    - threshold ∈ [0, 1]:归一化相关度,值越大越相关(默认 0.3,过滤掉明显无关的)
      0 不筛选;0.5 起码"中等相关";0.7+ 通常太严
    - threshold > 1.0:旧 L2 距离语义(向后兼容),值越小越相关,2.0 相当于不筛选

    旧配置 2.0 仍可用,继续走 L2 旁路。新部署建议保留默认 0.3。"""

    DEFAULT_SEARCH_ENGINE: t.Literal["bing", "duckduckgo", "metaphor", "searx"] = "duckduckgo"
    """默认搜索引擎"""

    SEARCH_ENGINE_TOP_K: int = 3
    """搜索引擎匹配结题数量"""

    ZH_TITLE_ENHANCE: bool = True
    """是否开启中文标题加强，以及标题增强的相关配置。

    默认 True：中文文档(尤其是 PDF / docx 转出来的扁平段落)往往没有结构化
    headings，开启后切分阶段会把"看起来像标题"的短行单独成 chunk 并加
    metadata.title，向量召回质量明显提升。代价：切分阶段多一次正则扫描,
    几乎不增加耗时。"""

    PDF_OCR_THRESHOLD: t.Tuple[float, float] = (0.6, 0.6)
    """
    PDF OCR 控制：只对宽高超过页面一定比例（图片宽/页面宽，图片高/页面高）的图片进行 OCR。
    这样可以避免 PDF 中一些小图片的干扰，提高非扫描版 PDF 处理速度
    """

    KB_INFO: t.Dict[str, str] = {"samples": "关于本项目issue的解答"} # TODO: 都存在数据库了，这个配置项还有必要吗？
    """每个知识库的初始化介绍，用于在初始化知识库时显示和Agent调用，没写则没有介绍，不会被Agent调用。"""

    kbs_config: t.Dict[str, t.Dict] = {
            "faiss": {},
            "milvus": {
                # Milvus 2.6 起 langchain-milvus 走 MilvusClient(uri=...);host/port
                # 仍兼容(底层 connections.connect 会拼 uri),但 uri 是头等公民。
                # 老配置(host/port)会被 milvus_kb_service._load_milvus 自动归一为 uri。
                "uri": "http://127.0.0.1:19530",
                "user": "",
                "password": "",
                "secure": False
            },
            "zilliz": {
                "host": "in01-a7ce524e41e3935.ali-cn-hangzhou.vectordb.zilliz.com.cn",
                "port": "19530",
                "user": "",
                "password": "",
                "secure": True
            },
            "pg": {
                "connection_uri": "postgresql://postgres:postgres@127.0.0.1:5432/chayuan"
            },
            "relyt": {
                "connection_uri": "postgresql+psycopg2://postgres:postgres@127.0.0.1:7000/chayuan"
            },
            "es": {
                "scheme": "http",
                "host": "127.0.0.1",
                "port": "9200",
                "index_name": "test_index",
                "user": "",
                "password": "",
                "verify_certs": True,
                "ca_certs": None,
                "client_cert": None,
                "client_key": None
            },
            "milvus_kwargs": {
                # Milvus 2.6 推荐路径:AUTOINDEX(server 端按数据规模自动挑 HNSW/DISKANN 等)
                # + COSINE(对 OpenAI / BGE / sentence-transformers 等已 L2-normalized 的
                # 文本/图像 embedding 都是最优默认;COSINE = IP for normalized vectors)。
                # 历史踩坑:之前默认 {metric_type:L2, index_type:HNSW} 漏了 params:{M,efConstruction},
                # 导致建库时索引创建失败 → col 没 load → 上传/查询拿到 ConnectionNotExist 风格报错。
                # 如显式指定 HNSW,milvus_kb_service 会自动补 M / efConstruction 默认值。
                "search_params": {
                    "metric_type": "COSINE",
                    "params": {}
                },
                "index_params": {
                    "metric_type": "COSINE",
                    "index_type": "AUTOINDEX",
                    "params": {}
                }
            },
            "chromadb": {}
        }
    """可选向量库类型及对应配置"""

    text_splitter_dict: t.Dict[str, t.Dict[str, t.Any]] = {
            "ChineseRecursiveTextSplitter": {
                "source": "",
                "tokenizer_name_or_path": "",
            },
            "ChineseTextSplitter": {
                "source": "",
                "tokenizer_name_or_path": "",
            },
            "AliTextSplitter": {
                "source": "",
                "tokenizer_name_or_path": "",
            },
            "SpacyTextSplitter": {
                "source": "huggingface",
                "tokenizer_name_or_path": "gpt2",
            },
            "RecursiveCharacterTextSplitter": {
                "source": "tiktoken",
                "tokenizer_name_or_path": "cl100k_base",
            },
            "MarkdownHeaderTextSplitter": {
                "headers_to_split_on": [
                    ("#", "head1"),
                    ("##", "head2"),
                    ("###", "head3"),
                    ("####", "head4"),
                ]
            },
        }
    """
    TextSplitter配置项，如果你不明白其中的含义，就不要修改。
    source 如果选择tiktoken则使用openai的方法 "huggingface"
    """

    TEXT_SPLITTER_NAME: str = "ChineseRecursiveTextSplitter"
    """TEXT_SPLITTER 名称"""

    EMBEDDING_KEYWORD_FILE: str = "embedding_keywords.txt"
    """Embedding模型定制词语的词表文件"""

    # ---- RAG 检索质量增强(P0-1 / 2026-04 P1-1 默认 ON)----
    USE_HYBRID_RETRIEVER: bool = True
    """是否启用 BM25 + Vector 的混合检索(EnsembleRetriever)。
    中文场景下开启可带来 15~30% 命中率提升;首次检索会额外构建 BM25 索引(毫秒级)。
    Cohere/Superlinked benchmark 显示 hybrid 是生产 RAG 的事实默认架构。
    fail-open:BM25 / jieba 不可用时静默降级到纯向量,不阻塞主路径。"""

    HYBRID_BM25_WEIGHT: float = 0.4
    """混合检索中 BM25 的权重;Vector 权重自动取 1 - HYBRID_BM25_WEIGHT。
    经验:中文 0.4,英文 0.3,代码/关键字密集型场景可提高到 0.5。"""

    HYBRID_CANDIDATE_TOP_K: int = 50
    """混合召回阶段的 Top-K(rerank 前的候选池大小)。
    2026-04:从 20 调到 50 — 行业共识"retrieve 50 → rerank 取 top-5"是召回精度的拐点
    (Cohere benchmark:+17.2 pp MRR@3, +12.1 pp Recall@5)。"""

    USE_RERANKER: bool = True
    """是否启用 CrossEncoder rerank。开启后精排,命中率 +10~20%,但要加载模型,
    首次调用约 1-3 秒,之后毫秒级。
    fail-open:sentence-transformers 未装 / 模型下载失败时静默透传,不阻塞主路径。"""

    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    """rerank 模型。默认 BGE-reranker-v2-m3（中英双语 SOTA，2024 开源）。
    替代选项：BAAI/bge-reranker-large / jina-reranker / Cohere rerank-multilingual-v3（需 API key）。
    """

    RERANKER_LOCAL_FILES_ONLY: bool = True
    """rerank 仅使用本地模型 / HuggingFace 缓存。
    国内/内网环境默认不应在用户检索时阻塞联网下载;需要在线自动下载时可显式改为 False。
    """

    RERANKER_DEVICE: str = "cpu"
    """rerank 模型 device：cpu / cuda / mps。生产建议 cuda，单卡承载 20~50 QPS。"""

    RERANKER_MAX_LENGTH: int = 512
    """CrossEncoder 输入的最大 token 数；chunk 长的场景可调到 1024。"""

    USE_CONTEXT_EXPANSION: bool = False
    """是否开启"邻居 chunk 上下文扩展"（轻量版 Parent-Child）。
    检索到的每个 chunk 会附带其前后 N 个兄弟 chunk 合并后给 LLM，减少语义断裂。
    """

    CONTEXT_EXPANSION_NEIGHBORS: int = 1
    """上下文扩展时向前/向后各取几条邻居 chunk。1 表示前后各 1 条（共 3 条合并）。"""

    # ---- ColBERT late-interaction 检索（N-1）----
    USE_COLBERT: bool = False
    """是否启用 ColBERT late-interaction 检索插件；需 ``pip install ragatouille``。
    长 query 场景相比纯 dense+BM25 再 +5~12%；索引体积 1-2x，首次 build 较慢。"""

    COLBERT_MODEL: str = "colbert-ir/colbertv2.0"
    """ColBERT 基础模型；默认 ColBERTv2。可切 jinaai/jina-colbert-v2 等多语言模型。"""

    COLBERT_WEIGHT: float = 0.3
    """ColBERT 在候选池融合时的默认权重；rerank 时会被 CrossEncoder 覆盖。"""

    # ---- ColPali 图像-文本检索（N-11，多模态 PDF）----
    USE_COLPALI: bool = False
    """是否启用 ColPali 视觉检索插件（PDF 整页作为图像 embedding，绕过 OCR 损耗）。
    需额外安装 ``pip install colpali-engine transformers``。"""

    COLPALI_WEIGHT: float = 0.2
    """ColPali 候选池融合权重（默认）。"""

    # ---- RAPTOR（层次化摘要树）----
    USE_RAPTOR: bool = False
    """是否启用 RAPTOR 层次摘要检索。开启后 kb 上传后的 chunks 会经过 GMM 聚类 +
    LLM 摘要形成 2~3 层摘要树；检索时与原 chunks 同池召回。
    需要先对目标 KB 执行一次 build（/knowledge_base/{kb}/build_raptor）。"""

    RAPTOR_MAX_LEVELS: int = 3
    """RAPTOR 递归最大层数；3 层已覆盖 10k 以内 chunks 的绝大部分场景。"""

    RAPTOR_CLUSTER_SIZE: int = 5
    """每个聚类期望大小；越小树越高、摘要越细；越大越宏观。"""

    # ---- GraphRAG（实体 + 社区摘要）----
    USE_GRAPHRAG: bool = False
    """是否启用 GraphRAG。开启后 kb build 阶段会额外跑 LLM 抽 entity/relation。
    build 成本高（O(N 个 chunk) 次 LLM 调用），查询时零额外 LLM 成本。"""

    GRAPHRAG_COMMUNITY_MIN_SIZE: int = 2
    """社区检测后过滤阈值：少于此数量成员的社区不生成摘要。"""

    GRAPHRAG_LOCAL_NEIGHBOR_HOPS: int = 1
    """Local search 从种子 entity 出发的遍历跳数。1 够用；超过易引入噪声。"""

    # ---- 图像知识源 ----
    IMAGE_EMBEDDER: str = "google/siglip2-base-patch16-224"
    """默认图像向量化模型（HF 仓库名）。支持：
    - google/siglip2-base-patch16-224（默认，400MB 多语言）
    - google/siglip2-large-patch16-384（1.4GB 多语言）
    - OFA-Sys/chinese-clip-vit-base-patch16（400MB 中文最佳）
    - OFA-Sys/chinese-clip-vit-large-patch14-336px（1.4GB 中文高精）
    - jinaai/jina-clip-v2（1GB 89 语言 + 长文）
    - openai/clip-vit-large-patch14（1.7GB 英文经典）
    未下载时：在 WebUI「模型管理」下载或上传；见 docs/install/image_models.md。
    """

    IMAGE_MAX_UPLOAD_MB: int = 20
    """单张图最大允许大小（MB）；超过拒绝上传。"""


class PlatformConfig(MyBaseModel):
    """模型加载平台配置"""

    platform_name: str = "xinference"
    """平台名称"""

    # 平台类型 — 决定后端用哪个 client 分支解析请求/响应。
    # 老版用 Literal 只允许 6 种,但 UI 的 PLATFORM_TYPE_CHOICES 已扩展到 9 种
    # (zhipu / qianfan / claude / azure / minimax 等),用户选这些会让 settings
    # 加载抛 validation_error 启动崩溃。改成 str 兼容任意值,真正路由分支由 utils
    # 的 get_ChatOpenAI / get_OpenAIClient 用 startswith / map 判,容错性更好。
    platform_type: str = "xinference"
    """平台类型"""

    api_base_url: str = "http://127.0.0.1:9997/v1"
    """openai api url"""

    api_key: str = "EMPTY"
    """api key if available"""

    api_proxy: str = ""
    """API 代理"""

    api_concurrencies: int = 5
    """该平台单模型最大并发数"""

    enabled: bool = True
    """厂商总开关。False 时该平台所有模型不会出现在 /v1/models 也不会被 chat 调用,
    用于临时离线某个 vendor(如关闭 OneAPI 走本地 Ollama)而不删配置。"""

    disabled_models: t.List[str] = []
    """该平台下禁用的模型名黑名单(精确匹配)。即使 auto_detect 把它检测到,
    /v1/models 也不会返回,用于过滤掉默认包里你不想暴露给用户的型号。"""

    auto_detect_model: bool = False
    """是否自动获取平台可用模型列表。设为 True 时下方不同模型类型可自动检测"""

    llm_models: t.Union[t.Literal["auto"], t.List[str]] = []
    """该平台支持的大语言模型列表，auto_detect_model 设为 True 时自动检测"""

    embed_models: t.Union[t.Literal["auto"], t.List[str]] = []
    """该平台支持的嵌入模型列表，auto_detect_model 设为 True 时自动检测"""

    text2image_models: t.Union[t.Literal["auto"], t.List[str]] = []
    """该平台支持的图像生成模型列表，auto_detect_model 设为 True 时自动检测"""

    image2text_models: t.Union[t.Literal["auto"], t.List[str]] = []
    """该平台支持的多模态模型列表，auto_detect_model 设为 True 时自动检测"""

    rerank_models: t.Union[t.Literal["auto"], t.List[str]] = []
    """该平台支持的重排模型列表，auto_detect_model 设为 True 时自动检测"""

    speech2text_models: t.Union[t.Literal["auto"], t.List[str]] = []
    """该平台支持的 STT 模型列表，auto_detect_model 设为 True 时自动检测"""

    text2speech_models: t.Union[t.Literal["auto"], t.List[str]] = []
    """该平台支持的 TTS 模型列表，auto_detect_model 设为 True 时自动检测"""


class ApiModelSettings(BaseFileSettings):
    """模型配置项"""

    model_config = SettingsConfigDict(yaml_file=CHAYUAN_ROOT / "model_settings.yaml")

    DEFAULT_LLM_MODEL: str = "glm4-chat"
    """默认选用的 LLM 名称"""

    DEFAULT_EMBEDDING_MODEL: str = "bge-m3"
    """默认选用的 Embedding 名称"""

    Agent_MODEL: str = ""
    """已废弃:Agent 默认跟随前端当前选择的模型。保留字段仅兼容旧 model_settings.yaml。"""

    HISTORY_LEN: int = 3
    """默认历史对话轮数"""

    MAX_TOKENS: t.Optional[int] = None # TODO: 似乎与 LLM_MODEL_CONFIG 重复了
    """大模型最长支持的长度，如果不填写，则使用模型默认的最大长度，如果填写，则为用户设定的最大长度"""

    TEMPERATURE: float = 0.7
    """LLM通用对话参数"""

    SUPPORT_AGENT_MODELS: t.List[str] = [
            "chatglm3-6b",
            "glm-4",
            "openai-api",
            "Qwen-2",
            "qwen2-instruct",
            "gpt-3.5-turbo",
            "gpt-4o",
        ]
    """支持的Agent模型"""

    LLM_MODEL_CONFIG: t.Dict[str, t.Dict] = {
            # 意图识别不需要输出，模型后台知道就行
            "preprocess_model": {
                "model": "",
                "temperature": 0.05,
                "max_tokens": 4096,
                "history_len": 10,
                "prompt_name": "default",
                "callbacks": False,
            },
            "llm_model": {
                "model": "",
                "temperature": 0.9,
                "max_tokens": 4096,
                "history_len": 10,
                "prompt_name": "default",
                "callbacks": True,
            },
            "action_model": {
                "model": "",
                "temperature": 0.01,
                "max_tokens": 4096,
                "history_len": 10,
                "prompt_name": "ChatGLM3",
                "callbacks": True,
            },
            "postprocess_model": {
                "model": "",
                "temperature": 0.01,
                "max_tokens": 4096,
                "history_len": 10,
                "prompt_name": "default",
                "callbacks": True,
            },
            "image_model": {
                "model": "sd-turbo",
                "size": "256*256",
            },
        }
    """
    LLM模型配置，包括了不同模态初始化参数。
    `model` 如果留空则自动使用 DEFAULT_LLM_MODEL
    """

    MODEL_PLATFORMS: t.List[PlatformConfig] = []
    """模型平台配置。

    单机版默认空列表 — 用户在「模型广场」里按需添加(DeepSeek / OpenAI / Ollama
    等),而非启动就显示一堆没填 key 的占位条目造成"是不是已经配好了?"的误解。
    厂商目录(PROVIDER_CATALOG)仍然在 UI 上展示可选品牌,只是不会预先注入到 DB
    /yaml。SaaS / 服务端部署需要预置默认时,通过 model_settings.yaml 的
    MODEL_PLATFORMS 字段在部署期注入即可,Pydantic 会读 yaml 覆盖这里的空默认。
    """


class ToolSettings(BaseFileSettings):
    """Agent 工具配置项"""
    model_config = SettingsConfigDict(yaml_file=CHAYUAN_ROOT / "tool_settings.yaml",
                                      json_file=CHAYUAN_ROOT / "tool_settings.json",
                                      extra="allow")

    search_local_knowledgebase: dict = {
        "use": False,
        "top_k": 3,
        "score_threshold": 2.0,
        "conclude_prompt": {
            "with_result": (
                "<指令>下列是与问题可能相关的检索资料，未必完整。请主要依据资料作答：能直接回答则简洁给出；若仅有部分相关信息，"
                "请结合可推出的内容作答并说明资料未覆盖的要点；不要编造资料中不存在的事实。仅当资料与问题明显无关、无法做出任何有据推断时，"
                "再简短说明无法从现有资料回答，并可建议换关键词或补充知识库。答案使用中文。</指令>\n"
                "<已知信息>{{ context }}</已知信息>\n"
                "<问题>{{ question }}</问题>\n"
            ),
            "without_result": "请你根据我的提问回答我的问题:\n"
            "{{ question }}\n"
            "请注意，你必须在回答结束后强调，你的回答是根据你的经验回答而不是参考资料回答的。\n",
        },
    }
    '''本地知识库工具配置项'''

    search_internet: dict = {
        "use": False,
        "search_engine_name": "duckduckgo",
        "search_engine_config": {
            "bing": {
                "bing_search_url": "https://api.bing.microsoft.com/v7.0/search",
                "bing_key": "",
            },
            "metaphor": {
                "metaphor_api_key": "",
                "split_result": False,
                "chunk_size": 500,
                "chunk_overlap": 0,
            },
            "duckduckgo": {},
            "searx": {
                "host": "https://metasearx.com",
                "engines": [],
                "categories": [],
                "language": "zh-CN",
            }
        },
        "top_k": 5,
        "verbose": "Origin",
        "conclude_prompt": "<指令>这是搜索到的互联网信息，请你根据这些信息进行提取并有调理，简洁的回答问题。如果无法从中得到答案，请说 “无法搜索到能回答问题的内容”。 "
        "</指令>\n<已知信息>{{ context }}</已知信息>\n"
        "<问题>\n"
        "{{ question }}\n"
        "</问题>\n",
    }
    '''搜索引擎工具配置项。推荐自己部署 searx 搜索引擎，国内使用最方便。'''

    arxiv: dict = {
        "use": False,
    }

    weather_check: dict = {
        "use": False,
        "api_key": "",
    }
    '''心知天气（https://www.seniverse.com/）工具配置项'''

    search_youtube: dict = {
        "use": False,
    }

    wolfram: dict = {
        "use": False,
        "appid": "",
    }

    calculate: dict = {
        "use": False,
    }
    '''numexpr 数学计算工具配置项'''

    text2images: dict = {
        "use": False,
        "model": "sd-turbo",
        "size": "256*256",
    }
    '''图片生成工具配置项。model 必须是在 model_settings.yaml/MODEL_PLATFORMS 中配置过的。'''

    text2sql: dict = {
        # 该工具需单独指定使用的大模型，与用户前端选择使用的模型无关
        "model_name": "qwen-plus",
        "use": False,
        # SQLAlchemy连接字符串，支持的数据库有：
        # crate、duckdb、googlesql、mssql、mysql、mariadb、oracle、postgresql、sqlite、clickhouse、prestodb
        # 不同的数据库请查阅SQLAlchemy用法，修改sqlalchemy_connect_str，配置对应的数据库连接，如sqlite为sqlite:///数据库文件路径，下面示例为mysql
        # 如提示缺少对应数据库的驱动，请自行通过poetry安装
        "sqlalchemy_connect_str": "mysql+pymysql://用户名:密码@主机地址/数据库名称",
        # 务必评估是否需要开启read_only,开启后会对sql语句进行检查，请确认text2sql.py中的intercept_sql拦截器是否满足你使用的数据库只读要求
        # 优先推荐从数据库层面对用户权限进行限制
        "read_only": False,
        # 限定返回的行数
        "top_k": 50,
        # 是否返回中间步骤
        "return_intermediate_steps": True,
        # 如果想指定特定表，请填写表名称，如["sys_user","sys_dept"]，不填写走智能判断应该使用哪些表
        "table_names": [],
        # 对表名进行额外说明，辅助大模型更好的判断应该使用哪些表，尤其是SQLDatabaseSequentialChain模式下,是根据表名做的预测，很容易误判。
        "table_comments": {
            # 如果出现大模型选错表的情况，可尝试根据实际情况填写表名和说明
            # "tableA":"这是一个用户表，存储了用户的基本信息",
            # "tableB":"角色表",
        },
    }
    '''
    text2sql使用建议
    1、因大模型生成的sql可能与预期有偏差，请务必在测试环境中进行充分测试、评估；
    2、生产环境中，对于查询操作，由于不确定查询效率，推荐数据库采用主从数据库架构，让text2sql连接从数据库，防止可能的慢查询影响主业务；
    3、对于写操作应保持谨慎，如不需要写操作，设置read_only为True,最好再从数据库层面收回数据库用户的写权限，防止用户通过自然语言对数据库进行修改操作；
    4、text2sql与大模型在意图理解、sql转换等方面的能力有关，可切换不同大模型进行测试；
    5、数据库表名、字段名应与其实际作用保持一致、容易理解，且应对数据库表名、字段进行详细的备注说明，帮助大模型更好理解数据库结构；
    6、若现有数据库表名难于让大模型理解，可配置下面table_comments字段，补充说明某些表的作用。
    '''
  
    amap: dict = {
        "use": False,
        "api_key": "高德地图 API KEY",
    }
    '''高德地图、天气相关工具配置项。'''

    text2promql: dict = {
        "use": False,
        # <your_prometheus_ip>:<your_prometheus_port>
        "prometheus_endpoint": "http://127.0.0.1:9090",
        # <your_prometheus_username>
        "username": "",
        # <your_prometheus_password>
        "password": "",
    }
    '''
    text2promql 使用建议
    1、因大模型生成的 promql 可能与预期有偏差, 请务必在测试环境中进行充分测试、评估;
    2、text2promql 与大模型在意图理解、metric 选择、promql 转换等方面的能力有关, 可切换不同大模型进行测试;
    3、当前仅支持 单prometheus 查询, 后续考虑支持 多prometheus 查询.
    '''

    url_reader: dict = {
        "use": False,
        "timeout": "10000",
    }
    '''URL内容阅读（https://r.jina.ai/）工具配置项
    请确保部署的网络环境良好，以免造成超时等问题'''



class PromptSettings(BaseFileSettings):
    """Prompt 模板.除 Agent 模板使用 f-string 外，其它均使用 jinja2 格式"""

    model_config = SettingsConfigDict(yaml_file=CHAYUAN_ROOT / "prompt_settings.yaml",
                                      json_file=CHAYUAN_ROOT / "prompt_settings.json",
                                      extra="allow")

    preprocess_model: dict = {
        "default": (
            "你只要回复0 和 1 ，代表不需要使用工具。以下几种问题不需要使用工具:\n"
            "1. 需要联网查询的内容\n"
            "2. 需要计算的内容\n"
            "3. 需要查询实时性的内容\n"
            "如果我的输入满足这几种情况，返回1。其他输入，请你回复0，你只要返回一个数字\n"
            "这是我的问题:"
            ),
    }
    """意图识别用模板"""

    llm_model: dict = {
        "default": "{{input}}",
        "with_history": (
            "The following is a friendly conversation between a human and an AI.\n"
            "The AI is talkative and provides lots of specific details from its context.\n"
            "If the AI does not know the answer to a question, it truthfully says it does not know.\n\n"
            "Current conversation:\n"
            "{{history}}\n"
            "Human: {{input}}\n"
            "AI:"
            ),
    }
    '''普通 LLM 用模板'''

    rag: dict = {
        "default": (
            "【角色】你是严谨的文档问答助手。\n"
            "【资料说明】下列「已知信息」来自检索，可能不完整或与问题仅有部分相关。\n"
            "【作答要求】\n"
            "1）优先依据「已知信息」作答：能完整回答则简洁准确；若仅有部分相关，请写出能从资料推出的结论，"
            "并自然说明资料未覆盖的要点（勿用固定套话敷衍）。\n"
            "2）可做显而易见的归纳整理，但不要编造资料中不存在的事实、数字、人名、条款或原文未出现的引用。\n"
            "3）仅当「已知信息」与问题明显无关、无法做出任何有据推断时，再简短说明无法从现有资料回答，"
            "并可提示用户换关键词、检查知识库或补充材料。\n"
            "请使用中文。\n\n"
            "【已知信息】\n{{context}}\n\n"
            "【问题】\n{{question}}\n"
            ),
        "empty": (
            "请你回答我的问题:\n"
            "{{question}}"
        ),
    }
    '''RAG 用模板，可用于知识库问答、文件对话、搜索引擎对话'''

    action_model: dict = {
        "default": {
            "SYSTEM_PROMPT": (
                "You are a helpful assistant"
            ),
        },
        "openai-functions": {
            "SYSTEM_PROMPT": (
                "You are a helpful assistant"
            ),
            "HUMAN_MESSAGE": (
                "{input}"
            )
        },
        "glm3": {
            "SYSTEM_PROMPT": ("\nAnswer the following questions as best as you can. You have access to the following "
                              "tools:\n{tools}"),
            "HUMAN_MESSAGE": "Let's start! Human:{input}\n\n{agent_scratchpad}"

        },
        "qwen": {
            "SYSTEM_PROMPT": (
                "Answer the following questions as best you can. You have access to the following APIs:\n\n"
                "{tools}\n\n"
                "Use the following format:\n\n"
                "Question: the input question you must answer\n"
                "Thought: you should always think about what to do\n"
                "Action: the action to take, should be one of [{tool_names}]\n"
                "Action Input: the input to the action\n"
                "Observation: the result of the action\n"
                "... (this Thought/Action/Action Input/Observation can be repeated zero or more times)\n"
                "Thought: I now know the final answer\n"
                "Final Answer: the final answer to the original input question\n\n"
                "Format the Action Input as a JSON object.\n\n"
                "Begin!\n\n"),
            "HUMAN_MESSAGE": (
                "Question: {input}\n\n"
                "{agent_scratchpad}\n\n")
        },
        "structured-chat-agent": {
            "SYSTEM_PROMPT": (
                "Respond to the human as helpfully and accurately as possible. You have access to the following tools:\n\n"
                "{tools}\n\n"
                "Use a json blob to specify a tool by providing an action key (tool name) and an action_input key (tool input).\n\n"
                'Valid "action" values: "Final Answer" or {tool_names}\n\n'
                "Provide only ONE action per $JSON_BLOB, as shown:\n\n"
                '```\n{{\n  "action": $TOOL_NAME,\n  "action_input": $INPUT\n}}\n```\n\n'
                "Follow this format:\n\n"
                "Question: input question to answer\n"
                "Thought: consider previous and subsequent steps\n"
                "Action:\n```\n$JSON_BLOB\n```\n"
                "Observation: action result\n"
                "... (repeat Thought/Action/Observation N times)\n"
                "Thought: I know what to respond\n"
                'Action:\n```\n{{\n  "action": "Final Answer",\n  "action_input": "Final response to human"\n}}\n\n'
                "Begin! Reminder to ALWAYS respond with a valid json blob of a single action. Use tools if necessary. Respond directly if appropriate. Format is Action:```$JSON_BLOB```then Observation\n"
            ),
            "HUMAN_MESSAGE": (
                "{input}\n\n"
                "{agent_scratchpad}\n\n"
            )
            # '(reminder to respond in a JSON blob no matter what)')
        },
        "platform-agent": {
            "SYSTEM_PROMPT": (
                "You are a helpful assistant"
            ),
            "HUMAN_MESSAGE": (
                "{input}\n\n"
            )
        },
        "platform-knowledge-mode": {
            "SYSTEM_PROMPT": (
                "</think>You are Chayuan, a content manager; you are familiar with how to find data from complex projects and better respond to users\n"
                "\n"
                "\n"
                "CRITICAL: TOOL RULES: All tool usage MUST ` Tool Use Formatting` the specified structured format. \n"
                "CRITICAL: THINKING RULES: In <thinking> tags, assess what information you already have and what information you need to proceed with the task. Include detailed output description text within <thinking> tags and always specify the `TOOL USE` next action to take.\n"
                "CRITICAL: MCP TOOL RULES: All MCP tool usage MUST strictly follow the Output Structure rules defined for `use_mcp_tool`. The output will always be returned within <use_mcp_tool> tags with the specified structured format.\n"
                "IMPORTANT: This tool usage process will be repeated multiple times throughout task completion. Each and every MCP tool call MUST follow the Output Structure rules without exception. The structured format must be applied consistently across all iterations to ensure proper parsing and execution.\n"
                "\n"
                "====\n"
                "\n"
                "TOOL USE\n"
                "You have access to a set of tools that are executed upon the user's approval. You can use one tool per message, and will receive the result of that tool use in the user's response. You use tools step-by-step to accomplish a given task, with each tool use informed by the result of the previous tool use.\n"
                "\n"
                "CRITICAL: MCP TOOL RULES: All MCP tool usage MUST strictly follow the Output Structure rules defined for `use_mcp_tool`. The output will always be returned within <use_mcp_tool> tags with the specified structured format.\n"
                "IMPORTANT: This tool usage process will be repeated multiple times throughout task completion. Each and every MCP tool call MUST follow the Output Structure rules without exception. The structured format must be applied consistently across all iterations to ensure proper parsing and execution.\n"
                "\n"
                "# Tool Use Formatting\n"
                "\n"
                "CRITICAL: TOOL USE FORMATTING: Tool use is formatted using XML-style tags. The tool name is enclosed in opening and closing tags, and each parameter is similarly enclosed within its own set of tags. This format is MANDATORY for proper parsing and execution. Here's the structure:\n"
                "\n"
                "<tool_name>\n"
                "<parameter1_name>value1</parameter1_name>\n"
                "<parameter2_name>value2</parameter2_name>\n"
                "...\n"
                "</tool_name>\n"
                "\n"
                "For example:\n"
                "\n"
                "<read_file>\n"
                "<path>src/main.js</path>\n"
                "</read_file>\n"
                "\n"
                "\n"
                "# Tools\n"
                "\n" 
                "{tools}\n"
                "\n" 
                "## use_mcp_tool\n"
                "Description: Request to use a tool provided by a connected MCP server. Each MCP server can provide multiple tools with different capabilities. Tools have defined input schemas that specify required and optional parameters.\n"
                "Parameters:\n"
                "- server_name: (required) The name of the MCP server providing the tool\n"
                "- tool_name: (required) The name of the tool to execute\n"
                "- arguments: (required) A JSON object containing the tool's input parameters, following the tool's input schema\n"
                "\n"
                "Usage:\n"
                "<use_mcp_tool>\n"
                "<server_name>server name here</server_name>\n"
                "<tool_name>tool name here</tool_name>\n"
                "<arguments>\n"
                "{{\n"
                "  \"param1\": \"value1\",\n"
                "  \"param2\": \"value2\"\n"
                "}}\n"
                "</arguments>\n"
                "</use_mcp_tool>\n"
                "\n"
                "Output Structure:\n"
                "The tool will return a structured response within <use_mcp_tool> tags containing:\n"
                "<use_mcp_tool>\n"
                "- success: boolean indicating if the tool execution succeeded\n"
                "- result: the actual output data from the tool execution\n"
                "- error: error message if the execution failed (null if successful)\n"
                "- server_name: the name of the MCP server that executed the tool\n"
                "- tool_name: the name of the tool that was executed\n"
                "</use_mcp_tool>\n"
                "\n"
                "\n"
                "## access_mcp_resource\n"
                "Description: Request to access a resource provided by a connected MCP server. Resources represent data sources that can be used as context, such as files, API responses, or system information.\n"
                "Parameters:\n"
                "- server_name: (required) The name of the MCP server providing the resource\n"
                "- uri: (required) The URI identifying the specific resource to access\n"
                "Usage:\n"
                "<access_mcp_resource>\n"
                "<server_name>server name here</server_name>\n"
                "<uri>resource URI here</uri>\n"
                "</access_mcp_resource>\n"
                "\n"
                "\n"
                "====\n"
                "\n"
                "# Tool Use Examples\n"
                "\n"
                "## Example 1: Requesting to use an MCP tool\n"
                "\n"
                "<use_mcp_tool>\n"
                "<server_name>weather-server</server_name>\n"
                "<tool_name>get_forecast</tool_name>\n"
                "<arguments>\n"
                "{{\n"
                "  \"city\": \"San Francisco\",\n"
                "  \"days\": 5\n"
                "}}\n"
                "</arguments>\n"
                "</use_mcp_tool>\n"
                "\n"
                "## Example 2: Requesting to access an MCP resource\n"
                "\n"
                "<access_mcp_resource>\n"
                "<server_name>weather-server</server_name>\n"
                "<uri>weather://san-francisco/current</uri>\n"
                "</access_mcp_resource>\n"
                "\n"
                "\n"
                "====\n"
                "\n"
                "MCP SERVERS\n"
                "\n"
                "The Model Context Protocol (MCP) enables communication between the system and locally running MCP servers that provide additional tools and resources to extend your capabilities.\n"
                "\n"
                "CRITICAL: MCP TOOL RULES: All MCP tool usage MUST strictly follow the Output Structure rules defined for `use_mcp_tool`. The output will always be returned within <use_mcp_tool> tags with the specified structured format.\n"
                "IMPORTANT: This tool usage process will be repeated multiple times throughout task completion. Each and every MCP tool call MUST follow the Output Structure rules without exception. The structured format must be applied consistently across all iterations to ensure proper parsing and execution.\n"
                "\n"
                "# Connected MCP Servers\n"
                "\n"
                "When a server is connected, you can use the server's tools via the `use_mcp_tool` tool, and access the server's resources via the `access_mcp_resource` tool.\n"
                "\n"
                "\n"
                "{mcp_tools}\n"
                "\n"
                "\n"
                "====\n"
                "\n"
                "\n"
                "# Choosing the Appropriate Tool\n"
                "\n"
                "None\n"
                "\n"
                "\n"
                "====\n"
                "# Auto-formatting Considerations\n"
                " \n"
                "None\n"
                "\n"
                "\n"
                "====\n"
                "# Workflow Tips\n"
                "\n"
                "None\n"
                "\n"
                "\n"
                "====\n"
                " \n"
                "CAPABILITIES\n"
                "\n"
                "- You have access to tools that\n" 
                "\n"
                "- You have access to MCP servers that may provide additional tools and resources. Each server may provide different capabilities that you can use to accomplish tasks more effectively.\n"
                "\n"
                "\n"
                "====\n"
                "\n"
                "RULES\n"
                "\n"
                "CRITICAL: Always adhere to this format for the tool use to ensure proper parsing and execution. Before completing the user's final task, all intermediate tool usage processes must maintain proper parsing and execution. Each tool call must be correctly formatted and executed according to the specified XML structure to ensure successful task completion.\n"
                "CRITICAL: MCP TOOL RULES: 1. All MCP tool output must be enclosed within <use_mcp_tool> opening and closing tags without exception.\n"
                "CRITICAL: MCP TOOL RULES: 2. The structured response format must be strictly followed for proper parsing and execution.\n"
                "CRITICAL: MCP TOOL RULES: 3. Before completing user's final task, all intermediate MCP tool processes must maintain proper parsing and execution.\n"
                "CRITICAL: THINKING RULES: In <thinking> tags, assess what information you already have and what information you need to proceed with the task. Include detailed output description text within <thinking> tags and always specify the `TOOL USE` next action to take.\n"
                "CRITICAL: PARAMETER RULES: 1. ALL parameters marked as (required) MUST be provided with actual content - empty or null values are strictly forbidden.\n"
                "CRITICAL: PARAMETER RULES: 2. The 'uri' parameter MUST contain a valid resource URI string.\n"
                "CRITICAL: PARAMETER RULES: 3. Missing parameters or empty parameter values will cause resource access to fail.\n" 
                "CRITICAL: PARAMETER RULES: 4. ALL parameters marked as (required) MUST be provided with actual content - empty or null values are strictly forbidden.\n"
                "CRITICAL: PARAMETER RULES: 5. The 'arguments' parameter MUST contain a valid JSON object with appropriate parameter values for the specified tool.\n"
                "CRITICAL: PARAMETER RULES: 6. Missing parameters or empty parameter values will cause tool execution to fail.\n"
                "CRITICAL: Tool Use RULES: 1. If multiple actions are needed, use one tool at a time per message to accomplish the task iteratively, with each tool use being informed by the result of the previous tool use. Do not assume the outcome of any tool use. Each step must be informed by the previous step's result.\n"
                "CRITICAL: Tool Use RULES: 2. Formulate your tool use using the XML format specified for each tool. by example `TOOL USE`\n"
                "Your current working directory is: {current_working_directory}\n"
                "You are STRICTLY FORBIDDEN from starting your messages with \"Great\", \"Certainly\", \"Okay\", \"Sure\". You should NOT be conversational in your responses, but rather direct and to the point. For example you should NOT say \"Great, I've find's the Chunk\" but instead something like \"I've find's the Chunk\". It is important you be clear and technical in your messages.\n"
                "When presented with images, utilize your vision capabilities to thoroughly examine them and extract meaningful information. Incorporate these insights into your thought process as you accomplish the user's task.\n"
                "At the end of each user message, you will automatically receive environment_details. This information is not written by the user themselves, but is auto-generated to provide potentially relevant context about the project structure and environment. While this information can be valuable for understanding the project context, do not treat it as a direct part of the user's request or response. Use it to inform your actions and decisions, but don't assume the user is explicitly asking about or referring to this information unless they clearly do so in their message. When using environment_details, explain your actions clearly to ensure the user understands, as they may not be aware of these details.\n"
                "MCP operations should be used one at a time, similar to other tool usage. Wait for confirmation of success before proceeding with additional operations.\n"
               
                "\n"
                "\n"
                "====\n"
                "\n"
                "SYSTEM INFORMATION\n"
                "\n"
                "None\n"
                "\n"
                "====\n"
                "\n"
                "OBJECTIVE\n"
                "\n"
                "You accomplish a given task iteratively, breaking it down into clear steps and working through them methodically.\n"
                "\n"
                "1. Analyze the user's task and set clear, achievable goals to accomplish it. Prioritize these goals in a logical order.\n"
                "2. Work through these goals sequentially, utilizing available tools one at a time as necessary. Each goal should correspond to a distinct step in your problem-solving process. You will be informed on the work completed and what's remaining as you go.\n"
                "3. Remember, you have extensive capabilities with access to a wide range of tools that can be used in powerful and clever ways as necessary to accomplish each goal. Before calling a tool, do some analysis within <thinking></thinking> tags. First, analyze the file structure provided in environment_details to gain context and insights for proceeding effectively. Then, think about which of the provided tools is the most relevant tool to accomplish the user's task.\n"
                "4. The user may provide feedback, which you can use to make improvements and try again. But DO NOT continue in pointless back and forth conversations, i.e. don't end your responses with questions or offers for further assistance.\n"
            ),
            "HUMAN_MESSAGE": (
                "{input}\n\n" 
                "<environment_details>\n"
                "# Current Time\n"
                "{datetime}\n"
                "</environment_details>\n"
            )
        },
    }
    """Agent 模板"""

    postprocess_model: dict = {
        "default": "{{input}}",
    }
    """后处理模板"""


def _reset_basic_path_fields_to_empty() -> None:
    """把 basic_settings.yaml 里三个路径字段写回空串，跟随 CHAYUAN_ROOT。

    仅在 `init` 等重建模板的流程里调用；对用户自己写死的路径，模板重建本身已经是
    主动覆盖行为，保持一致。使用 ruamel.yaml 保留注释/结构。
    """
    from chayuan.pydantic_settings_file import import_yaml
    import tempfile

    yaml_file = CHAYUAN_ROOT / "basic_settings.yaml"
    if not yaml_file.is_file():
        return
    y = import_yaml()
    with open(yaml_file, "r", encoding="utf-8") as f:
        doc = y.load(f)
    if not isinstance(doc, dict):
        return

    changed = False
    for key in ("KB_ROOT_PATH", "DB_ROOT_PATH", "SQLALCHEMY_DATABASE_URI"):
        if key in doc and doc.get(key) != "":
            doc[key] = ""
            changed = True

    if not changed:
        return

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{yaml_file.name}.", suffix=".tmp", dir=str(yaml_file.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fp:
            y.dump(doc, fp)
        os.replace(tmp_name, yaml_file)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


class SettingsContainer:
    CHAYUAN_ROOT = CHAYUAN_ROOT

    basic_settings: BasicSettings = settings_property(BasicSettings())
    kb_settings: KBSettings = settings_property(KBSettings())
    model_settings: ApiModelSettings = settings_property(ApiModelSettings())
    tool_settings: ToolSettings = settings_property(ToolSettings())
    prompt_settings: PromptSettings = settings_property(PromptSettings())

    def createl_all_templates(self):
        self.basic_settings.create_template_file(write_file=True)
        self.kb_settings.create_template_file(write_file=True)
        self.model_settings.create_template_file(sub_comments={
            "MODEL_PLATFORMS": {"model_obj": PlatformConfig(),
                                "is_entire_comment": True}},
            write_file=True)
        self.tool_settings.create_template_file(write_file=True, file_format="yaml", model_obj=ToolSettings())
        self.prompt_settings.create_template_file(write_file=True, file_format="yaml")
        # 让 basic_settings.yaml 的路径字段默认保持空字符串，便于跟随 CHAYUAN_ROOT。
        # 模板渲染阶段拿到的是 pydantic 校验后的绝对路径，这里再把它们写回空串，
        # 这样新机器从 yaml 读回来只要命中 validator fallback，就会指向当前 CHAYUAN_ROOT。
        try:
            _reset_basic_path_fields_to_empty()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(
                f"[chayuan][settings] 重置 basic_settings 路径字段失败（不影响主流程）："
                f"{type(e).__name__}: {e}\n"
            )

    def set_auto_reload(self, flag: bool=True):
        self.basic_settings.auto_reload = flag
        self.kb_settings.auto_reload = flag
        self.model_settings.auto_reload = flag
        self.tool_settings.auto_reload = flag
        self.prompt_settings.auto_reload = flag


Settings = SettingsContainer()
Settings.basic_settings.make_dirs()

# 40 题 P2:把 chayuan 自带 nltk_data 路径加入 NLTK_DATA 环境变量。
# nltk 在自己 import 时会 read 此 env 自动加入 ``nltk.data.path`` —
# 我们就**不再需要** ``import nltk`` + ``nltk.data.path.append(...)``。
# 启动开销减 ~0.8 秒,行为完全等价。
_chayuan_nltk_path = str(Settings.basic_settings.NLTK_DATA_PATH)
_existing_nltk_data = os.environ.get("NLTK_DATA", "")
if _chayuan_nltk_path not in _existing_nltk_data.split(os.pathsep):
    os.environ["NLTK_DATA"] = (
        _existing_nltk_data + os.pathsep + _chayuan_nltk_path
        if _existing_nltk_data else _chayuan_nltk_path
    )


if __name__ == "__main__":
    Settings.createl_all_templates()
