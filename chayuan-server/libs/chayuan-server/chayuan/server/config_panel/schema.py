"""配置面板的字段元数据（schema）。

设计思路：
- 每个 yaml 文件用 ``FileSchema`` 描述，再用若干 ``Section`` / ``Field`` 划分显示分组。
- 字段仅覆盖「日常需要在线修改」的原子项（log_verbose、端口、知识库阈值、各工具 `use` 等）；
  所有 yaml 文件都额外提供「原始 YAML」标签页，以便高级用户直接编辑。
- 每个 ``Field.needs_restart`` 标记改动是否需要重启服务，由 Panel UI 按钮根据集合
  判断是否弹出「需要重启」横幅。
- 所有字段均以 ``path`` 描述在文档中的位置，采用 **点号分隔的 key 序列**
  （例如 ``API_SERVER.port`` 或 ``search_internet.top_k``）。保存时 yaml_store 负责
  按路径写回，保留 ruamel 原有的注释与结构。

热配置（log_verbose / HTTPX_DEFAULT_TIMEOUT）来自 ``basic_settings.yaml`` 顶部注释：

    除 log_verbose/HTTPX_DEFAULT_TIMEOUT 修改后即时生效
    其它配置项修改后都需要重启服务器才能生效

这里与之保持一致。其余文件为保守策略，默认 ``needs_restart=True``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence


# ---------------------------------------------------------------------------
# 动态默认值：基础配置页那三个路径字段的 placeholder / description 需要随
# CHAYUAN_ROOT 变化。这里用一组小 helper 在模块导入期一次性算好，避免在 UI
# 渲染层再跑 I/O。任何异常都吞掉，退化到「<CHAYUAN_ROOT>/data/...」这种带
# 占位符的文案，保证即便 chayuan.paths 尚未可用，面板也不会崩。
# ---------------------------------------------------------------------------


def _compute_path_defaults() -> dict:
    try:
        from chayuan.paths import resolve_chayuan_root

        root = str(resolve_chayuan_root().path)
    except Exception:
        root = "<CHAYUAN_ROOT>"
    import os as _os

    kb = _os.path.join(root, "data", "knowledge_base")
    db = _os.path.join(kb, "info.db")
    # 为了在 Windows 上也能 copy-paste 即可用的 sqlite URI，这里强制 POSIX 风格斜杠。
    db_posix = db.replace("\\", "/")
    uri = f"sqlite:///{db_posix}" if db_posix.startswith("/") else f"sqlite:///{db_posix}"
    return {
        "root": root,
        "kb_root_path": kb,
        "db_root_path": db,
        "sqlalchemy_uri": uri,
    }


_PATH_DEFAULTS = _compute_path_defaults()


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Field:
    """单个可配置字段的描述。"""

    path: str
    """在 yaml 文档里的路径（点号分隔），例如 ``API_SERVER.port``。"""

    label: str
    """表单标签。"""

    widget: str
    """控件类型：switch / number / text / password / textarea / select / yaml。"""

    help: str = ""
    """帮助文本（一般对应 yaml 注释）。"""

    needs_restart: bool = True
    """修改后是否需要重启服务。"""

    choices: Optional[Sequence[Any]] = None
    """枚举候选值，仅 widget=select 有意义。"""

    placeholder: str = ""
    """输入框占位符。"""

    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    """number 控件参数。"""

    rows: int = 6
    """textarea / yaml 控件显示行数。"""

    cast: Optional[Callable[[Any], Any]] = None
    """保存前对 UI 值做一次强制转换，None 表示按 widget 默认。"""

    password_toggle: bool = False
    """password 控件是否允许显示/隐藏切换。"""


@dataclass
class Section:
    title: str
    description: str = ""
    fields: List[Field] = field(default_factory=list)


@dataclass
class FileSchema:
    filename: str
    title: str
    description: str = ""
    sections: List[Section] = field(default_factory=list)
    raw_editor: bool = True
    """是否额外展示「原始 YAML」编辑器。"""


# ---------------------------------------------------------------------------
# 具体 schema
# ---------------------------------------------------------------------------

BASIC_SETTINGS = FileSchema(
    filename="basic_settings.yaml",
    title="基础配置",
    description=(
        "服务器基本配置。其中 log_verbose、HTTPX_DEFAULT_TIMEOUT 修改后即时生效；"
        "其它项修改后需要重启服务器才能生效。"
    ),
    sections=[
        Section(
            title="运行时",
            fields=[
                Field(
                    path="log_verbose",
                    label="开启日志详细信息",
                    widget="switch",
                    help="是否开启日志详细信息",
                    needs_restart=False,
                ),
                Field(
                    path="HTTPX_DEFAULT_TIMEOUT",
                    label="httpx 默认超时时间（秒）",
                    widget="number",
                    help="httpx 请求默认超时时间（秒）。如果加载模型或对话较慢，出现超时错误，可以适当加大该值。",
                    needs_restart=False,
                    min=1,
                    max=3600,
                    step=1,
                ),
                Field(
                    path="OPEN_CROSS_DOMAIN",
                    label="API 开启跨域",
                    widget="switch",
                    help="API 是否开启跨域",
                ),
            ],
        ),
        # 注：知识库路径 + 文件存储后端（local / minio）由专门的
        # ``storage_config.render_storage_card`` 接管（置于"运行时"之上的独立卡），
        # 这样 backend 切换时 KB_ROOT_PATH / MINIO_* 字段可以**条件展开**，
        # 而且和独立存储配置 renderer 共用，避免双入口。
        # 原来的 `知识库存储路径` section 已被该卡接管，此处保留说明占位即可。
        Section(
            title="服务器绑定地址",
            fields=[
                Field(
                    path="DEFAULT_BIND_HOST",
                    label="默认绑定 host (DEFAULT_BIND_HOST)",
                    widget="text",
                    help=(
                        "各服务器默认绑定 host。如改为 \"0.0.0.0\" 需要修改下方所有 XX_SERVER 的 host。"
                        "Windows 下助手界面自动弹出浏览器时，如果地址为 \"0.0.0.0\" 是无法访问的，"
                        "需要手动修改地址栏。"
                    ),
                    placeholder="127.0.0.1 / 0.0.0.0",
                ),
                Field(
                    path="API_SERVER.host",
                    label="API host",
                    widget="text",
                    help="API 服务器 host。",
                ),
                Field(
                    path="API_SERVER.port",
                    label="API port",
                    widget="number",
                    help="API 服务器端口。",
                    min=1,
                    max=65535,
                    step=1,
                ),
                Field(
                    path="API_SERVER.public_host",
                    label="API public_host",
                    widget="text",
                    help="API 公网 host（用于生成云服务访问链接，如知识库文档链接）。",
                ),
                Field(
                    path="API_SERVER.public_port",
                    label="API public_port",
                    widget="number",
                    help="API 公网端口。",
                    min=1,
                    max=65535,
                    step=1,
                ),
                Field(
                    path="CONFIG_SERVER.host",
                    label="配置面板 host (CONFIG_SERVER.host)",
                    widget="text",
                    help="本配置面板（NiceGUI）绑定 host。",
                ),
                Field(
                    path="CONFIG_SERVER.port",
                    label="配置面板 port (CONFIG_SERVER.port)",
                    widget="number",
                    help="本配置面板（NiceGUI）端口。修改后需要重启。",
                    min=1,
                    max=65535,
                    step=1,
                ),
            ],
        ),
        Section(
            title="性能 / 可扩展性",
            description=(
                "面向多用户 / 高并发场景。字段含义与推荐值详见「⑨ 性能与可扩展性」"
                "页的实时体检，也可参考 docs/scalability.md。"
            ),
            fields=[
                Field(
                    path="DEPLOYMENT_MODE",
                    label="部署模式 DEPLOYMENT_MODE",
                    widget="select",
                    choices=["dev", "prod"],
                    help=(
                        "dev：开发/单机。prod：生产；配置面板会把 SQLite / FAISS / 单 worker 等"
                        "不达标项亮红色告警。切换本字段不会自动改其它配置，只影响校验严格程度。"
                    ),
                ),
                Field(
                    path="UVICORN_WORKERS",
                    label="API 进程数 UVICORN_WORKERS",
                    widget="number", min=1, max=128, step=1,
                    help=(
                        "API uvicorn worker 数。单机生产建议 2 × CPU 核心数 + 1。"
                        "> 1 时程序走多进程启动路径（uvicorn --workers）。"
                    ),
                ),
                Field(
                    path="DB_POOL_SIZE",
                    label="DB 连接池常驻 DB_POOL_SIZE",
                    widget="number", min=1, max=500, step=1,
                    help=(
                        "SQLAlchemy 常驻连接数。生产 Postgres 建议 ≥ 20；"
                        "数据库端真实连接 ≈ workers × (pool_size + max_overflow)。"
                    ),
                ),
                Field(
                    path="DB_MAX_OVERFLOW",
                    label="DB 连接池溢出 DB_MAX_OVERFLOW",
                    widget="number", min=0, max=500, step=1,
                    help="SQLAlchemy 溢出连接数。峰值连接 = DB_POOL_SIZE + DB_MAX_OVERFLOW。",
                ),
                Field(
                    path="DB_POOL_RECYCLE",
                    label="DB 连接回收秒数 DB_POOL_RECYCLE",
                    widget="number", min=-1, max=86400, step=60,
                    help="连接回收时间（秒）。Postgres/MySQL 建议 3600；-1 表示不回收。",
                ),
                Field(
                    path="DB_POOL_PRE_PING",
                    label="DB 取连接前 ping（DB_POOL_PRE_PING）",
                    widget="switch",
                    help="每次从池取连接前先 SELECT 1 探活，避免拿到被网关关掉的死连接。生产强烈建议开启。",
                ),
                Field(
                    path="DB_POOL_TIMEOUT",
                    label="DB 获取连接超时 DB_POOL_TIMEOUT",
                    widget="number", min=1, max=300, step=1,
                    help="从连接池拿连接的最长等待秒数。",
                ),
                # REDIS_URL 不在这里渲染：已由基础配置页顶部 / 性能与可扩展性页
                # 的「Redis 连接配置」卡片接管（结构化字段 + 在线验证 + 最佳实践）。
                Field(
                    path="RATE_LIMIT_ENABLED",
                    label="开启限流 RATE_LIMIT_ENABLED",
                    widget="switch",
                    help="开启后 API 将按 IP / 用户做令牌桶限流；需要先配置 REDIS_URL 才能在多副本间共享计数。",
                ),
                Field(
                    path="RATE_LIMIT_PER_MINUTE",
                    label="限流阈值（次/分钟）RATE_LIMIT_PER_MINUTE",
                    widget="number", min=1, max=100000, step=1,
                    help="每 IP / 每用户每分钟请求数上限。",
                ),
            ],
        ),
        Section(
            title="可观测性 / 弹性",
            description=(
                "日志 / 指标 / 链路追踪 / LLM 重试，统一在「⑨ 性能与可扩展性」页有实时体检。"
                "依赖 prometheus-client / opentelemetry-* 等包，缺包时自动降级为 no-op。"
            ),
            fields=[
                Field(
                    path="JSON_LOGS",
                    label="JSON 结构化日志 JSON_LOGS",
                    widget="switch",
                    help=(
                        "开启后 logging 以 JSON 格式输出，每条日志自带 request_id 字段，"
                        "与 nginx / 前端日志对齐。Loki / ELK 友好。"
                    ),
                ),
                Field(
                    path="METRICS_ENABLED",
                    label="Prometheus 指标 METRICS_ENABLED",
                    widget="switch",
                    help=(
                        "开启后 `/metrics` 暴露 QPS、延迟、in-flight、LLM 错误率等标准指标。"
                        "需要 `pip install prometheus-client`；未装时自动降级，不影响接口。"
                    ),
                ),
                Field(
                    path="OTEL_ENABLED",
                    label="OpenTelemetry 链路追踪 OTEL_ENABLED",
                    widget="switch",
                    help=(
                        "按标准 OTLP 协议上报 traces；需要安装 opentelemetry-sdk 与 "
                        "opentelemetry-exporter-otlp-proto-http。Collector 地址通过标准"
                        "环境变量 OTEL_EXPORTER_OTLP_ENDPOINT 配置。"
                    ),
                ),
                Field(
                    path="OTEL_SERVICE_NAME",
                    label="OTEL 服务名 OTEL_SERVICE_NAME",
                    widget="text",
                    placeholder="chayuan-api",
                    help="在 Jaeger / Tempo 中作为服务标识；同服务多副本共享同名。",
                ),
                # ------ Langfuse（LLM 链路观测，非必要服务）------
                # 只要 host + public_key + secret_key 齐全即启用；若环境变量已设，
                # 运行时优先读环境变量（方便 K8s Secret 注入）。
                Field(
                    path="LANGFUSE_HOST",
                    label="Langfuse host LANGFUSE_HOST",
                    widget="text",
                    placeholder="http://127.0.0.1:3000 或 https://cloud.langfuse.com",
                    help=(
                        "Langfuse 服务端地址（自托管或 SaaS）。与 PUBLIC_KEY/SECRET_KEY "
                        "同时填写方可启用。若环境变量 $LANGFUSE_HOST 已设，将优先使用 env。"
                        "留空 = 禁用 Langfuse，对业务无影响。"
                    ),
                ),
                Field(
                    path="LANGFUSE_PUBLIC_KEY",
                    label="Langfuse public key LANGFUSE_PUBLIC_KEY",
                    widget="text",
                    placeholder="pk-lf-xxxxxxxx",
                    help=(
                        "Langfuse 控制台为项目生成的 Public Key。生产推荐通过环境变量"
                        "$LANGFUSE_PUBLIC_KEY 注入而不是写 yaml；本字段作为"
                        "单机 / 开发环境的回退值。"
                    ),
                ),
                Field(
                    path="LANGFUSE_SECRET_KEY",
                    label="Langfuse secret key LANGFUSE_SECRET_KEY",
                    widget="password",
                    placeholder="sk-lf-xxxxxxxx",
                    password_toggle=True,
                    help=(
                        "对应 Secret Key。展示时会脱敏。生产应走 env / Secret Manager，"
                        "避免把密钥提交到代码仓库。"
                    ),
                ),
                Field(
                    path="CHAYUAN_LANGFUSE_DISABLE",
                    label="一键禁用 Langfuse CHAYUAN_LANGFUSE_DISABLE",
                    widget="switch",
                    help=(
                        "应急开关：打开后即使上面 3 项都配好了，也会全流程跳过 Langfuse 调用。"
                        "用于排查「Langfuse 侧故障是否在拖累业务」。等价 env "
                        "$CHAYUAN_LANGFUSE_DISABLE=1（任一生效即禁用）。"
                    ),
                ),
                Field(
                    path="LLM_RETRY_ATTEMPTS",
                    label="LLM 重试次数 LLM_RETRY_ATTEMPTS",
                    widget="number", min=0, max=10, step=1,
                    help=(
                        "LLM 调用失败时的重试次数（不含首次）。0 表示关闭重试。"
                        "重试采用指数退避 + 抖动，上限由 LLM_RETRY_WAIT_MAX 控制。"
                    ),
                ),
                Field(
                    path="LLM_RETRY_WAIT_MAX",
                    label="LLM 重试最大退避（秒）LLM_RETRY_WAIT_MAX",
                    widget="number", min=0.1, max=120, step=0.1,
                    help="指数退避封顶秒数；初始 0.5s，按 2 倍递增，到此截断。",
                ),
                Field(
                    path="LLM_TIMEOUT_SECONDS",
                    label="LLM 单次超时（秒）LLM_TIMEOUT_SECONDS",
                    widget="number", min=1, max=1800, step=1,
                    help="单次 LLM 调用超过该值视为失败并参与重试。流式长回复场景要相应调大。",
                ),
            ],
        ),
        Section(
            title="多用户鉴权 / 异步入库 / 语义缓存",
            description=(
                "开启多用户模式后，API 层 chat / kb 类端点都会验 JWT；"
                "异步入库把上传文件的解析 + 向量化扔到 Arq worker，API 只负责落盘；"
                "语义缓存对非流式 chat / kb_chat 的短 query 命中时直接返回历史回答，key 里带 user_id 防串权限。"
            ),
            fields=[
                Field(
                    path="AUTH_REQUIRED",
                    label="强制登录 AUTH_REQUIRED",
                    widget="switch",
                    help=(
                        "关闭时 chat / kb 端点允许匿名访问（向后兼容单机模式）；"
                        "开启后所有业务端点必须带 Bearer token，否则 401。"
                    ),
                ),
                Field(
                    path="AUTH_ALLOW_REGISTRATION",
                    label="开放自助注册 AUTH_ALLOW_REGISTRATION",
                    widget="switch",
                    help="是否允许自助注册；内部系统通常保持关闭。",
                ),
                Field(
                    path="JWT_SECRET",
                    label="JWT 签名密钥 JWT_SECRET",
                    widget="text",
                    placeholder="留空将运行时随机生成；服务重启后所有 token 失效",
                    help=(
                        "HMAC 签名密钥。多副本部署必须在所有副本间一致，否则 token 互不认。"
                        "建议从 env 注入而非写 yaml。"
                    ),
                ),
                Field(
                    path="JWT_ACCESS_TTL_SECONDS",
                    label="access token 有效期（秒）",
                    widget="number", min=60, max=86400, step=60,
                    help="一般 15 分钟 ~ 2 小时；前端到期后用 refresh token 换新。",
                ),
                Field(
                    path="JWT_REFRESH_TTL_SECONDS",
                    label="refresh token 有效期（秒）",
                    widget="number", min=3600, max=30 * 86400, step=3600,
                    help="通常 7 天~30 天；过期需重新登录。",
                ),
                Field(
                    path="AUTH_DEFAULT_ADMIN_USERNAME",
                    label="默认管理员用户名",
                    widget="text",
                    placeholder="admin",
                    help="仅在数据库内没有任何用户时生效；后续改名请走「用户管理」页。",
                ),
                Field(
                    path="AUTH_DEFAULT_ADMIN_PASSWORD",
                    label="默认管理员初始密码",
                    widget="text",
                    placeholder="留空将随机生成并打印到启动日志",
                    help="首次启动用；落库即时生效。请登录后第一时间在「用户管理」改密。",
                ),
                Field(
                    path="INGEST_ASYNC_ENABLED",
                    label="异步入库队列 INGEST_ASYNC_ENABLED",
                    widget="switch",
                    help=(
                        "开启后 /knowledge_base/upload_docs 只落盘并入队，向量化由独立 worker 处理。"
                        "依赖 Redis + `pip install arq`，并需要执行 `chayuan worker` 启动消费者。"
                    ),
                ),
                Field(
                    path="ARQ_QUEUE_NAME",
                    label="默认队列名 ARQ_QUEUE_NAME",
                    widget="text",
                    placeholder="chayuan:ingest",
                    help="未拆分任务的回退队列；生产建议优先使用下面的分类队列。",
                ),
                Field(
                    path="ARQ_QUEUE_INGEST",
                    label="入库队列 ARQ_QUEUE_INGEST",
                    widget="text",
                    placeholder="chayuan:queue:ingest",
                    help="文件上传后的 OCR / 解析 / 普通知识库入库任务。",
                ),
                Field(
                    path="ARQ_QUEUE_EMBEDDING",
                    label="Embedding 队列 ARQ_QUEUE_EMBEDDING",
                    widget="text",
                    placeholder="chayuan:queue:embedding",
                    help="重新向量化、RAPTOR、GraphRAG 等重建类任务；可单独扩容。",
                ),
                Field(
                    path="ARQ_QUEUE_ANNOTATION",
                    label="标注任务队列 ARQ_QUEUE_ANNOTATION",
                    widget="text",
                    placeholder="chayuan:queue:annotation",
                    help="标注导入、采样、评测等低优先级任务。",
                ),
                Field(
                    path="ARQ_MAX_JOBS",
                    label="单 worker 并发数 ARQ_MAX_JOBS",
                    widget="number", min=1, max=64, step=1,
                    help="worker 进程内最大同时处理任务数；CPU / 向量库吞吐紧张时调低。",
                ),
                Field(
                    path="ARQ_TASK_MAX_TRIES",
                    label="任务重试次数 ARQ_TASK_MAX_TRIES",
                    widget="number", min=1, max=5, step=1,
                    help="后台任务失败后的最大尝试次数；外部模型不稳定时可适当调高。",
                ),
                Field(
                    path="ARQ_IDEMPOTENCY_TTL_SECONDS",
                    label="任务幂等锁 TTL（秒）",
                    widget="number", min=60, max=86400, step=60,
                    help="同一文件 hash / 文档版本重复提交时复用已有 task_id 的时间窗口。",
                ),
                Field(
                    path="ARQ_DEAD_LETTER_TTL_SECONDS",
                    label="死信保留 TTL（秒）",
                    widget="number", min=3600, max=2592000, step=3600,
                    help="失败任务的错误摘要保留时间，便于排查和后续重放。",
                ),
                Field(
                    path="SEMANTIC_CACHE_ENABLED",
                    label="语义缓存 SEMANTIC_CACHE_ENABLED",
                    widget="switch",
                    help=(
                        "开启后 chat / kb_chat 的「非流式 + 单轮短 query」会尝试从 Redis 命中。"
                        "需要 REDIS_URL；失败时静默跳过不影响主流程。"
                    ),
                ),
                Field(
                    path="SEMANTIC_CACHE_TTL_SECONDS",
                    label="缓存 TTL（秒）",
                    widget="number", min=30, max=86400, step=30,
                    help="命中写回的有效期；与 KB 更新频率匹配，避免返回过期答案。",
                ),
                Field(
                    path="SEMANTIC_CACHE_MIN_LEN",
                    label="缓存触发最小 query 长度",
                    widget="number", min=0, max=128, step=1,
                    help="过短的 query（如『好的』）几乎不值得缓存；低于此长度直接跳过。",
                ),
                Field(
                    path="SEMANTIC_CACHE_NAMESPACE",
                    label="缓存键前缀",
                    widget="text",
                    placeholder="chayuan:semcache",
                    help="多项目共用一个 Redis 时用来隔离。key 会拼上 user_id / kb / model 的 hash。",
                ),
            ],
        ),
    ],
)


KB_SETTINGS = FileSchema(
    filename="kb_settings.yaml",
    title="知识库配置",
    description="向量库、切分器、搜索引擎等知识库相关参数。",
    raw_editor=False,
    sections=[
        Section(
            title="常用",
            description="向量库类型及其连接参数请在上方「向量库」卡片里配置，此处仅管理默认知识库、切分器等轻量字段。",
            fields=[
                Field(
                    path="DEFAULT_KNOWLEDGE_BASE",
                    label="默认知识库名",
                    widget="text",
                    help="默认使用的知识库。",
                ),
                Field(
                    path="TEXT_SPLITTER_NAME",
                    label="分词器 TEXT_SPLITTER_NAME",
                    widget="select",
                    choices=[
                        "ChineseRecursiveTextSplitter",
                        "RecursiveCharacterTextSplitter",
                        "MarkdownHeaderTextSplitter",
                        "ChineseTextSplitter",
                        "SpacyTextSplitter",
                        "AliTextSplitter",
                    ],
                    help=(
                        "选择知识库入库时使用的文本切分器。\n"
                        "ChineseRecursiveTextSplitter：中文默认推荐，按段落、中文标点、英文标点递归切分，稳定且无需额外模型，适合 docx/pdf/txt 等中文资料。\n"
                        "RecursiveCharacterTextSplitter：通用 token/字符递归切分，适合中英文混合、代码、日志、接口文档；结构感弱于中文专用切分。\n"
                        "MarkdownHeaderTextSplitter：按 Markdown 标题层级切分，适合 md/技术文档/手册，可保留章节结构；不适用 chunk_size/overlap。\n"
                        "ChineseTextSplitter：轻量中文断句切分，按句号、问号、感叹号等断句，适合短文本或较规整中文；长文档语义连续性不如递归切分。\n"
                        "SpacyTextSplitter：基于 tokenizer 的切分，适合英文或多语言 token 长度控制；当前配置使用 gpt2 tokenizer，中文场景通常不作为首选。\n"
                        "AliTextSplitter：达摩院文档语义切分模型，中文语义边界更强，但依赖 modelscope 模型，首次加载慢、资源占用高，适合高质量离线入库。"
                    ),
                ),
                Field(
                    path="DEFAULT_SEARCH_ENGINE",
                    label="默认搜索引擎",
                    widget="select",
                    choices=["bing", "duckduckgo", "metaphor", "searx"],
                    help="默认搜索引擎。",
                ),
            ],
        ),
        Section(
            title="检索 / 切分参数",
            fields=[
                Field(
                    path="CACHED_VS_NUM",
                    label="缓存向量库数量（FAISS）",
                    widget="number", min=1, max=1024, step=1,
                    help="缓存向量库数量（针对 FAISS）",
                ),
                Field(
                    path="CACHED_MEMO_VS_NUM",
                    label="缓存临时向量库数量（FAISS）",
                    widget="number", min=1, max=1024, step=1,
                    help="缓存临时向量库数量（针对 FAISS），用于文件对话",
                ),
                Field(
                    path="CHUNK_SIZE",
                    label="切分块大小 CHUNK_SIZE",
                    widget="number", min=50, max=8192, step=10,
                    help="知识库中单段文本长度（不适用 MarkdownHeaderTextSplitter）",
                ),
                Field(
                    path="OVERLAP_SIZE",
                    label="切分重合长度 OVERLAP_SIZE",
                    widget="number", min=0, max=4096, step=10,
                    help="知识库中相邻文本重合长度（不适用 MarkdownHeaderTextSplitter）",
                ),
                Field(
                    path="VECTOR_SEARCH_TOP_K",
                    label="匹配向量数量 VECTOR_SEARCH_TOP_K",
                    widget="number", min=1, max=100, step=1,
                    help="知识库匹配向量数量",
                ),
                Field(
                    path="SCORE_THRESHOLD",
                    label="相关度阈值 SCORE_THRESHOLD",
                    widget="number", min=0.0, max=2.0, step=0.05,
                    help=(
                        "知识库匹配相关度阈值，取值范围在 0-2 之间，"
                        "SCORE 越小相关度越高，取到 2 相当于不筛选，建议设置在 0.5 左右。"
                    ),
                ),
                Field(
                    path="SEARCH_ENGINE_TOP_K",
                    label="搜索引擎 TOP_K",
                    widget="number", min=1, max=50, step=1,
                    help="搜索引擎匹配结题数量",
                ),
                Field(
                    path="ZH_TITLE_ENHANCE",
                    label="中文标题加强",
                    widget="switch",
                    help="是否开启中文标题加强，以及标题增强的相关配置",
                ),
            ],
        ),
    ],
)


MODEL_SETTINGS = FileSchema(
    filename="model_settings.yaml",
    title="模型配置",
    description="LLM / Embedding / 平台相关参数。平台 / 模型清单请在上方「模型平台」卡片里图形化配置。",
    raw_editor=False,
    sections=[
        Section(
            title="默认",
            description="采样 / 上下文等通用参数；默认模型选择与平台清单请在上方「模型平台」卡片里配置。",
            fields=[
                Field(
                    path="HISTORY_LEN",
                    label="默认历史对话轮数",
                    widget="number", min=0, max=100, step=1,
                    help="默认历史对话轮数",
                ),
                Field(
                    path="MAX_TOKENS",
                    label="MAX_TOKENS（留空使用模型默认）",
                    widget="number", min=0, max=1048576, step=1,
                    help=(
                        "大模型最长支持的长度，如果不填写，则使用模型默认的最大长度，"
                        "如果填写，则为用户设定的最大长度。"
                    ),
                ),
                Field(
                    path="TEMPERATURE",
                    label="TEMPERATURE",
                    widget="number", min=0.0, max=2.0, step=0.05,
                    help="LLM 通用对话参数。",
                ),
            ],
        ),
    ],
)


TOOL_SETTINGS = FileSchema(
    filename="tool_settings.yaml",
    title="Agent 工具",
    description="各工具启用开关与重要参数。更多参数请在「原始 YAML」标签页编辑。",
    sections=[
        Section(
            title="启用开关",
            fields=[
                Field(path="search_local_knowledgebase.use", label="本地知识库", widget="switch",
                      help="本地知识库工具启用开关"),
                Field(path="search_internet.use", label="联网搜索", widget="switch",
                      help="搜索引擎工具启用开关。推荐自己部署 searx 搜索引擎，国内使用最方便。"),
                Field(path="arxiv.use", label="arXiv", widget="switch", help="arXiv 工具启用开关"),
                Field(path="weather_check.use", label="心知天气", widget="switch",
                      help="心知天气工具启用开关 (https://www.seniverse.com/)"),
                Field(path="search_youtube.use", label="YouTube 搜索", widget="switch"),
                Field(path="wolfram.use", label="Wolfram", widget="switch", help="Wolfram Alpha 工具"),
                Field(path="calculate.use", label="数学计算", widget="switch",
                      help="numexpr 数学计算工具启用开关"),
                Field(path="text2images.use", label="图片生成 text2images", widget="switch",
                      help="图片生成工具。model 必须是在 model_settings.yaml/MODEL_PLATFORMS 中配置过的。"),
                Field(path="text2sql.use", label="text2sql", widget="switch"),
                Field(path="amap.use", label="高德地图", widget="switch",
                      help="高德地图、天气相关工具启用开关"),
                Field(path="text2promql.use", label="text2promql", widget="switch"),
                Field(path="url_reader.use", label="URL 内容阅读", widget="switch",
                      help="URL 内容阅读（https://r.jina.ai/）工具启用开关"),
            ],
        ),
        Section(
            title="常用参数",
            fields=[
                Field(path="search_local_knowledgebase.top_k", label="本地知识库 top_k",
                      widget="number", min=1, max=50, step=1),
                Field(path="search_local_knowledgebase.score_threshold",
                      label="本地知识库 score_threshold",
                      widget="number", min=0.0, max=2.0, step=0.05),
                Field(path="search_internet.search_engine_name", label="联网搜索引擎",
                      widget="select", choices=["bing", "duckduckgo", "metaphor", "searx"]),
                Field(path="search_internet.top_k", label="联网搜索 top_k",
                      widget="number", min=1, max=50, step=1),
                Field(path="text2images.model", label="text2images 模型名", widget="text"),
                Field(path="text2images.size", label="text2images 尺寸", widget="text",
                      placeholder="256*256"),
                Field(path="weather_check.api_key", label="心知天气 api_key", widget="password",
                      password_toggle=True),
                Field(path="wolfram.appid", label="Wolfram appid", widget="password",
                      password_toggle=True),
                Field(path="amap.api_key", label="高德地图 api_key", widget="password",
                      password_toggle=True),
                Field(path="text2sql.model_name", label="text2sql 模型名", widget="text"),
                Field(path="text2sql.sqlalchemy_connect_str", label="text2sql 数据库连接",
                      widget="text"),
                Field(path="text2sql.read_only", label="text2sql 只读", widget="switch"),
                Field(path="text2promql.prometheus_endpoint", label="Prometheus endpoint",
                      widget="text"),
                Field(path="url_reader.timeout", label="URL 读取超时（毫秒）", widget="text",
                      placeholder="10000"),
            ],
        ),
    ],
)


PROMPT_SETTINGS = FileSchema(
    filename="prompt_settings.yaml",
    title="Prompt 模板",
    description=(
        "除 Agent 模板使用 f-string 外，其它均使用 jinja2 格式。"
        "字段数量较多，这里只暴露最常调整的 RAG 模板，其它请在「原始 YAML」标签页直接编辑。"
    ),
    sections=[
        Section(
            title="RAG 模板",
            fields=[
                Field(path="rag.default", label="RAG default", widget="textarea", rows=8,
                      help="RAG 主模板（知识库问答 / 文件对话 / 搜索引擎对话）。"),
                Field(path="rag.empty", label="RAG empty", widget="textarea", rows=4,
                      help="没有检索到内容时使用的模板。"),
            ],
        ),
    ],
)


# 按项目生命周期展示的顺序：基础 → 知识库 → 模型 → 工具 → Prompt。
# 不再包含 nacos 配置（无 nacos 部署场景）。
ALL_SCHEMAS: List[FileSchema] = [
    BASIC_SETTINGS,
    KB_SETTINGS,
    MODEL_SETTINGS,
    TOOL_SETTINGS,
    PROMPT_SETTINGS,
]


def schema_by_filename(name: str) -> Optional[FileSchema]:
    for s in ALL_SCHEMAS:
        if s.filename == name:
            return s
    return None
