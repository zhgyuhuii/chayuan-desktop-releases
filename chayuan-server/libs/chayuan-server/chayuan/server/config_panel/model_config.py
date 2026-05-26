"""模型平台配置页（紧凑双栏 · 完整平台目录 · 只保存"启用且验证过"的平台）。

设计要点
--------
参考 ``/Users/zyh/work/chayuan`` 的 "SettingsDialog → 模型设置" 交互：

- **左栏**：**完整的模型服务商目录**（~58 家主流厂商），列表：logo + 名称
  + 启用开关 + 已配置徽标。用户在列表里勾选/编辑；**未启用的不会被写入 yaml**。
- **右栏**：选中服务商的连接参数表单（platform_type / api_base_url / api_key /
  代理 / 并发 / auto_detect）+ "获取模型清单"按钮 + 按类型分组的模型 inventory。
- **顶部状态栏**：搜索框 + "全部保存" 按钮；**页面不再显示标题**（由父级 dashboard 统一管理）。

写盘策略
--------
点"全部保存"时：
1. 从服务商目录中筛出 ``enabled=True`` **且** ``已通过连接验证`` **且** 至少有 1 个
   启用模型 的条目；
2. 把它们组装成 ``MODEL_PLATFORMS`` 列表整体覆写 ``model_settings.yaml``（ruamel
   保注释 / 保结构 / 原子写 / bak 备份）；
3. 未启用 / 未验证 / 无模型 的服务商**不会**出现在 yaml 里。

这样默认配置文件保持最小，生产环境不会误把一堆空 platform 塞进去。

平台 logo 解析
-------------
- SVG 源自 ``/Users/zyh/work/chayuan/dist/images/models/logos/`` 拷贝进
  ``libs/chayuan-server/chayuan/img/model_logos/``；
- 运行时由 ``config_panel/app.py`` 挂到 ``/static/model_logos/<file>``；
- 无 logo 时回退为"首字母彩色头像"（纯 CSS，靠 platform 主色）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from chayuan.server.config_panel import yaml_store

logger = logging.getLogger("chayuan.config_panel.model_config")

_FILE = "model_settings.yaml"

# 模型分组与 YAML 字段一一对应
MODEL_GROUPS: List[Tuple[str, str, str]] = [
    ("llm_models", "对话模型 (LLM)", "chat"),
    ("embed_models", "嵌入模型 (Embedding)", "hub"),
    ("rerank_models", "重排 (Rerank)", "sort"),
    ("text2image_models", "文生图", "image"),
    ("image2text_models", "图生文 / 视觉", "image_search"),
    ("speech2text_models", "语音识别", "mic"),
    ("text2speech_models", "语音合成", "volume_up"),
]
_GROUP_KEYS = [g[0] for g in MODEL_GROUPS]

# platform_type：对应后端 server/utils.get_ChatOpenAI 等分支支持的类型
PLATFORM_TYPE_CHOICES: List[str] = [
    "openai", "ollama", "xinference", "zhipu", "qianfan",
    "minimax", "claude", "azure", "custom",
]


# ---------------------------------------------------------------------------
# 模型服务商目录（参考 /Users/zyh/work/chayuan 的 MODEL_INVENTORY + getDefaultModels）
# ---------------------------------------------------------------------------

@dataclass
class ProviderMeta:
    """服务商元信息（不含用户输入的 api_key / models 等）。"""
    pid: str                    # 稳定 id（等于 YAML 里的 platform_name）
    display_name: str           # UI 显示名
    platform_type: str = "openai"  # YAML platform_type 默认值
    default_api_base: str = ""  # UI 里 placeholder 用的官方默认 URL
    logo: str = ""              # 对应 img/model_logos/<file> 的文件名（含扩展名）
    color: str = "#6b7280"      # 无 logo 时的首字母头像底色
    tags: Tuple[str, ...] = ()  # 分类打标："国内" "国外" "本地" "聚合"
    icon: str = ""              # 自定义服务商专用：Material icon 名；优先级低于 logo
    # —— 卡片右上角"申请 API Key"链接 ——
    # 云厂商:跳到该平台的 key 申请控制台
    # 本地厂商(Ollama / Xinference 等):跳到官网安装文档
    # 留空:卡片不显示"申请"入口
    apply_key_url: str = ""
    # 本地推理服务的"安装指南"地址(优先级高于 apply_key_url,只对 tags=("本地",) 生效)
    install_guide_url: str = ""
    # 厂商官网首页(可选,卡片底部小字"了解更多")
    website_url: str = ""
    # 该厂商内置的"主流模型清单"——DB 里没有任何配置时,catalog 接口
    # 用 default_models 当 fallback 让卡片不为空。键 = PlatformConfig 字段名
    # (llm_models / embed_models / rerank_models / text2image_models /
    #  image2text_models / speech2text_models / text2speech_models),值 = 模型 id 列表。
    # 仅给主流厂商种入,长尾厂商保持空 dict——前端会在卡片底部展示"未配置"CTA。
    default_models: Dict[str, List[str]] = field(default_factory=dict)


# ~60 家常见服务商；顺序策略：
#   1. 首屏"首选推荐"（千问 / DeepSeek / 智谱 / Ollama / 百度千帆）优先展示；
#   2. 其余按"国内主流 → 国外主流 → 本地 → 聚合 → 长尾"铺陈；
# 运行时 _build_initial_states 会把"已启用且有模型"的平台再次提到最上面（见该函数）。
PROVIDER_CATALOG: List[ProviderMeta] = [
    # --- 首选推荐（无任何配置时优先露出）---
    ProviderMeta("bailian",         "阿里云百炼 (千问)",   "openai",
                 "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "bailian.png", "#615CED", ("国内", "推荐")),
    ProviderMeta("deepseek",        "深度求索 DeepSeek",  "openai",
                 "https://api.deepseek.com/v1",
                 "deepseek.png", "#4D6BFE", ("国内", "推荐")),
    ProviderMeta("zhipu",           "智谱 ChatGLM",        "zhipu",
                 "https://open.bigmodel.cn/api/paas/v4",
                 "zhipu.png", "#4E64F2", ("国内", "推荐")),
    ProviderMeta("ollama",          "Ollama (本地)",        "ollama",
                 "http://127.0.0.1:11434/v1",
                 "ollama.png", "#000000", ("本地", "推荐")),
    ProviderMeta("baidu-qianfan",   "百度云千帆 (文心)",   "qianfan",
                 "https://qianfan.baidubce.com/v2",
                 "qianfan.svg", "#2932E1", ("国内", "推荐")),

    # --- 国内主流 ---
    ProviderMeta("moonshot",        "月之暗面 Kimi",        "openai",
                 "https://api.moonshot.cn/v1",
                 "moonshot.webp", "#111111", ("国内",)),
    ProviderMeta("volcengine",      "火山引擎 (豆包)",      "openai",
                 "https://ark.cn-beijing.volces.com/api/v3",
                 "volcengine-la_PI8m-.png", "#E61934", ("国内",)),
    ProviderMeta("tencent-hunyuan", "腾讯混元",             "openai",
                 "https://hunyuan.tencentcloudapi.com",
                 "hunyuan.png", "#1975FF", ("国内",)),
    ProviderMeta("minimax",         "MiniMax",              "minimax",
                 "https://api.minimaxi.com/v1",
                 "minimax-B0Eo-1V9.png", "#E0481F", ("国内",)),
    ProviderMeta("lingyi-wanwu",    "零一万物 Yi",           "openai",
                 "https://api.lingyiwanwu.com/v1",
                 "yi.png", "#003425", ("国内",)),
    ProviderMeta("baichuan",        "百川 Baichuan",         "openai",
                 "https://api.baichuan-ai.com/v1",
                 "baichuan.svg", "#FF6B35", ("国内",)),
    ProviderMeta("step-ai",         "阶跃星辰 StepFun",      "openai",
                 "https://api.stepfun.com/v1",
                 "", "#0A5FFF", ("国内",)),
    ProviderMeta("wuwen-xinqiong",  "无问芯穹",              "openai",
                 "", "", "#2f54eb", ("国内",)),
    ProviderMeta("xiaomi-mimo",     "Xiaomi MiMo",           "openai",
                 "https://api.mimo.mi.com/v1",
                 "mimo.svg", "#FF6900", ("国内",)),
    ProviderMeta("sensetime",       "商汤日日新",            "openai",
                 "", "sensetime.png", "#1B9AFF", ("国内",)),
    ProviderMeta("tianyi-xirang",   "天翼云息壤",            "openai",
                 "https://api.ctyun.cn",
                 "xirang-B42-6Dao.png", "#007FFF", ("国内",)),
    ProviderMeta("tencent-cloud-ti", "腾讯云 TI",             "openai",
                 "https://ti.tencentcloudapi.com",
                 "", "#1975FF", ("国内",)),

    # --- 国外主流 ---
    ProviderMeta("openai",          "OpenAI",                "openai",
                 "https://api.openai.com/v1",
                 "openai.png", "#10A37F", ("国外",)),
    ProviderMeta("azure-openai",    "Azure OpenAI",          "azure",
                 "https://your-resource.openai.azure.com",
                 "", "#0078D4", ("国外",)),
    ProviderMeta("anthropic",       "Anthropic Claude",      "claude",
                 "https://api.anthropic.com/v1",
                 "anthropic.png", "#D97757", ("国外",)),
    ProviderMeta("gemini",          "Google Gemini",         "openai",
                 "https://generativelanguage.googleapis.com/v1beta/openai",
                 "gemini.png", "#4285F4", ("国外",)),
    ProviderMeta("vertex-ai",       "Google Vertex AI",      "openai",
                 "https://us-central1-aiplatform.googleapis.com/v1",
                 "google.png", "#4285F4", ("国外",)),
    ProviderMeta("github-models",   "GitHub Models",         "openai",
                 "https://models.inference.ai.azure.com",
                 "github.png", "#24292E", ("国外",)),
    ProviderMeta("github-copilot",  "GitHub Copilot",        "openai",
                 "", "github-copilot.webp", "#24292E", ("国外",)),
    ProviderMeta("mistral",         "Mistral AI",            "openai",
                 "https://api.mistral.ai/v1",
                 "mistral.svg", "#FF7000", ("国外",)),
    ProviderMeta("grok",            "xAI Grok",              "openai",
                 "https://api.x.ai/v1",
                 "grok.png", "#111111", ("国外",)),
    ProviderMeta("perplexity",      "Perplexity",            "openai",
                 "https://api.perplexity.ai",
                 "perplexity.png", "#22B8CD", ("国外",)),
    ProviderMeta("groq",            "Groq",                  "openai",
                 "https://api.groq.com/openai/v1",
                 "groq.png", "#F54A00", ("国外",)),
    ProviderMeta("cerebras",        "Cerebras",              "openai",
                 "https://api.cerebras.ai/v1",
                 "cerebras.webp", "#F9423A", ("国外",)),
    ProviderMeta("nvidia",          "NVIDIA NIM",            "openai",
                 "https://integrate.api.nvidia.com/v1",
                 "nvidia.png", "#76B900", ("国外",)),
    ProviderMeta("cohere",          "Cohere",                "openai",
                 "https://api.cohere.ai/v1",
                 "cohere.png", "#39584C", ("国外",)),
    ProviderMeta("jina",            "Jina AI",               "openai",
                 "https://api.jina.ai/v1",
                 "jina.svg", "#000000", ("国外",)),
    ProviderMeta("aws-bedrock",     "AWS Bedrock",           "openai",
                 "https://bedrock-runtime.us-east-1.amazonaws.com",
                 "aws-bedrock.webp", "#FF9900", ("国外",)),
    ProviderMeta("huggingface",     "Hugging Face",          "openai",
                 "https://api-inference.huggingface.co",
                 "huggingface.webp", "#FFD21E", ("国外",)),
    ProviderMeta("voyage-ai",       "Voyage AI",             "openai",
                 "https://api.voyageai.com/v1",
                 "voyageai.png", "#2563EB", ("国外",)),
    ProviderMeta("hyperbolic",      "Hyperbolic",            "openai",
                 "https://api.hyperbolic.xyz/v1",
                 "hyperbolic.png", "#0066FF", ("国外",)),

    # --- 本地 / 自部署 ---
    ProviderMeta("xinference",      "Xinference",            "xinference",
                 "http://127.0.0.1:9997/v1",
                 "xinference.svg", "#1677FF", ("本地",)),
    ProviderMeta("lm-studio",       "LM Studio",             "openai",
                 "http://127.0.0.1:1234/v1",
                 "lmstudio.png", "#222222", ("本地",)),
    ProviderMeta("vllm",            "FastChat / vLLM",       "openai",
                 "http://127.0.0.1:8000/v1",
                 "vllm.svg", "#30A46C", ("本地",)),
    ProviderMeta("gpustack",        "GPUStack",              "openai",
                 "http://127.0.0.1/v1-openai",
                 "gpustack-D7EptUU-.svg", "#8E51FF", ("本地",)),
    # 本地推理服务器(均 OpenAI 兼容,GET /v1/models 拉模型清单)。端口为各自默认。
    ProviderMeta("llama-cpp",       "llama.cpp",             "openai",
                 "http://127.0.0.1:8080/v1",
                 "llama-cpp.png", "#000000", ("本地",)),
    ProviderMeta("localai",         "LocalAI",               "openai",
                 "http://127.0.0.1:8080/v1",
                 "localai.png", "#FF6F61", ("本地",)),
    ProviderMeta("tgi",             "TGI (HuggingFace)",     "openai",
                 "http://127.0.0.1:8080/v1",
                 "tgi.png", "#FFD21E", ("本地",)),
    ProviderMeta("text-gen-webui",  "Text Gen WebUI",        "openai",
                 "http://127.0.0.1:5000/v1",
                 "text-gen-webui.png", "#7C3AED", ("本地",)),
    ProviderMeta("koboldcpp",       "KoboldCpp",             "openai",
                 "http://127.0.0.1:5001/v1",
                 "koboldcpp.png", "#1F2937", ("本地",)),
    ProviderMeta("jan",             "Jan",                   "openai",
                 "http://127.0.0.1:1337/v1",
                 "jan.png", "#0EA5E9", ("本地",)),
    ProviderMeta("gpt4all",         "GPT4All",               "openai",
                 "http://127.0.0.1:4891/v1",
                 "gpt4all.png", "#16A34A", ("本地",)),
    ProviderMeta("openllm",         "OpenLLM",               "openai",
                 "http://127.0.0.1:3000/v1",
                 "openllm.png", "#FB7185", ("本地",)),
    # Infinity:michaelfeil/infinity 高性能 embedding / reranker 推理服务,OpenAI 兼容。
    # 后端 install_recipes / infinity_inventory / local_infinity_pip 都已支持,
    # 这里把它列入 catalog 让模型广场"本地"分类下能看到并配置。
    ProviderMeta("infinity",        "Infinity",              "openai",
                 "http://127.0.0.1:7997/v1",
                 "infinity.png", "#0EA5E9", ("本地",)),

    # --- 聚合 / 中转 / 其它 ---
    # One API / New API:本地部署的 LLM 网关 — 用户确认归类到 "本地"
    ProviderMeta("oneapi",          "One API",               "openai",
                 "http://127.0.0.1:3000/v1",
                 "oneapi.svg", "#1E40AF", ("本地",)),
    ProviderMeta("new-api",         "New API",               "openai",
                 "http://127.0.0.1:3000/v1",
                 "newapi.png", "#1E40AF", ("本地",)),
    ProviderMeta("api-compatible",  "自定义 OpenAI 兼容",    "custom",
                 "", "api-compatible.svg", "#64748B", ("聚合",)),
    ProviderMeta("openrouter",      "OpenRouter",            "openai",
                 "https://openrouter.ai/api/v1",
                 "openrouter.png", "#6366F1", ("聚合",)),
    ProviderMeta("aihubmix",        "AiHubMix",              "openai",
                 "https://aihubmix.com/v1",
                 "aihubmix.webp", "#FF3366", ("聚合",)),
    ProviderMeta("together",        "Together",              "openai",
                 "https://api.together.xyz/v1",
                 "together.png", "#0F6FFF", ("聚合",)),
    ProviderMeta("fireworks",       "Fireworks",             "openai",
                 "https://api.fireworks.ai/inference/v1",
                 "fireworks.png", "#6B22FF", ("聚合",)),
    ProviderMeta("modelscope",      "ModelScope 魔搭",       "openai",
                 "https://api-inference.modelscope.cn/v1",
                 "modelscope.png", "#624AFF", ("聚合",)),
    ProviderMeta("poe",             "Poe",                   "openai",
                 "", "poe.svg", "#5D5DFF", ("聚合",)),
    ProviderMeta("302-ai",          "302.AI",                "openai",
                 "https://api.302.ai/v1",
                 "302ai-OYnezl-B.webp", "#0EA5E9", ("聚合",)),
    ProviderMeta("dmxapi",          "DMXAPI",                "openai",
                 "https://www.dmxapi.com/v1",
                 "DMXAPI.png", "#F59E0B", ("聚合",)),
    ProviderMeta("burncloud",       "BurnCloud",             "openai",
                 "", "burncloud.png", "#DC2626", ("聚合",)),
    ProviderMeta("aionly",          "唯一 AI",                "openai",
                 "", "aiOnly-CX5LzR-B.webp", "#0EA5E9", ("聚合",)),
    ProviderMeta("ppio",            "PPIO 派欧云",            "openai",
                 "https://api.ppinfra.com/v3/openai",
                 "", "#00B3BA", ("聚合",)),
    ProviderMeta("qiniu",           "七牛云 AI",              "openai",
                 "https://api.qnaigc.com/v1",
                 "", "#00AAEE", ("聚合",)),
    ProviderMeta("lanyun",          "蓝耘",                   "openai",
                 "", "lanyun.png", "#0EA5E9", ("聚合",)),
    ProviderMeta("sophnet",         "SophNet",                "openai",
                 "", "", "#6B7280", ("聚合",)),
    ProviderMeta("cephalon",        "Cephalon",               "openai",
                 "", "", "#6B7280", ("聚合",)),
    ProviderMeta("netease-youdao",  "网易有道",                "openai",
                 "", "netease-youdao.svg", "#D7312C", ("国内",)),
    ProviderMeta("pangu",           "华为盘古",                "openai",
                 "", "pangu.svg", "#FF0000", ("国内",)),
]

# 申请 API Key / 安装指南 / 官网 三件套(后期补充式覆盖原 catalog,避免在 ~60 行
# Provider 行里塞 4 个新字段把 diff 撑爆)。键不在表中的厂商,这三个字段保持空。
# 来源:各厂商最新公开控制台链接(2026-04 校验);如有变更,直接改这张表即可。
_PROVIDER_LINKS: Dict[str, Dict[str, str]] = {
    # —— 国内主流 ——
    "deepseek":         {"apply": "https://platform.deepseek.com/api_keys",                              "site": "https://www.deepseek.com"},
    "bailian":          {"apply": "https://dashscope.console.aliyun.com/apiKey",                         "site": "https://dashscope.aliyun.com"},
    "zhipu":            {"apply": "https://open.bigmodel.cn/usercenter/apikeys",                         "site": "https://open.bigmodel.cn"},
    "moonshot":         {"apply": "https://platform.moonshot.cn/console/api-keys",                      "site": "https://www.moonshot.cn"},
    "minimax":          {"apply": "https://platform.minimaxi.com/user-center/basic-information/interface-key", "site": "https://www.minimaxi.com"},
    "volcengine":       {"apply": "https://console.volcengine.com/iam/keymanage/",                       "site": "https://www.volcengine.com/product/doubao"},
    "baidu-qianfan":    {"apply": "https://console.bce.baidu.com/qianfan/credential/api-key",            "site": "https://qianfan.cloud.baidu.com"},
    "tencent-hunyuan":  {"apply": "https://console.cloud.tencent.com/hunyuan/api-key",                   "site": "https://hunyuan.tencent.com"},
    "lingyi-wanwu":     {"apply": "https://platform.lingyiwanwu.com/apikeys",                            "site": "https://www.lingyiwanwu.com"},
    "baichuan":         {"apply": "https://platform.baichuan-ai.com/console/apikey",                    "site": "https://www.baichuan-ai.com"},
    "step-ai":          {"apply": "https://platform.stepfun.com/interface-key",                         "site": "https://www.stepfun.com"},
    "siliconflow":      {"apply": "https://cloud.siliconflow.cn/account/ak",                            "site": "https://siliconflow.cn"},
    "modelscope":       {"apply": "https://www.modelscope.cn/my/myaccesstoken",                         "site": "https://www.modelscope.cn"},
    "tencent-cloud-ti": {"apply": "https://console.cloud.tencent.com/cam/capi",                         "site": "https://cloud.tencent.com"},
    # —— 国外主流 ——
    "openai":           {"apply": "https://platform.openai.com/api-keys",                                "site": "https://openai.com"},
    "anthropic":        {"apply": "https://console.anthropic.com/settings/keys",                         "site": "https://www.anthropic.com"},
    "gemini":           {"apply": "https://aistudio.google.com/app/apikey",                              "site": "https://ai.google.dev"},
    "vertex-ai":        {"apply": "https://console.cloud.google.com/vertex-ai",                          "site": "https://cloud.google.com/vertex-ai"},
    "mistral":          {"apply": "https://console.mistral.ai/api-keys",                                 "site": "https://mistral.ai"},
    "grok":             {"apply": "https://console.x.ai/team/default/api-keys",                          "site": "https://x.ai"},
    "groq":             {"apply": "https://console.groq.com/keys",                                       "site": "https://groq.com"},
    "cerebras":         {"apply": "https://cloud.cerebras.ai/platform",                                  "site": "https://www.cerebras.ai"},
    "perplexity":       {"apply": "https://www.perplexity.ai/settings/api",                              "site": "https://www.perplexity.ai"},
    "cohere":           {"apply": "https://dashboard.cohere.com/api-keys",                               "site": "https://cohere.com"},
    "huggingface":      {"apply": "https://huggingface.co/settings/tokens",                              "site": "https://huggingface.co"},
    "azure-openai":     {"apply": "https://portal.azure.com",                                            "site": "https://azure.microsoft.com/products/ai-services/openai-service"},
    "github-models":    {"apply": "https://github.com/settings/personal-access-tokens/new",              "site": "https://github.com/marketplace/models"},
    "github-copilot":   {"apply": "https://github.com/features/copilot",                                 "site": "https://github.com/features/copilot"},
    "voyage-ai":        {"apply": "https://dash.voyageai.com/api-keys",                                  "site": "https://www.voyageai.com"},
    "jina":             {"apply": "https://jina.ai/api-dashboard/",                                      "site": "https://jina.ai"},
    "nvidia":           {"apply": "https://build.nvidia.com",                                            "site": "https://developer.nvidia.com/nim"},
    "aws-bedrock":      {"apply": "https://console.aws.amazon.com/bedrock",                              "site": "https://aws.amazon.com/bedrock"},
    "hyperbolic":       {"apply": "https://app.hyperbolic.xyz/settings",                                 "site": "https://hyperbolic.xyz"},
    # —— 聚合 / 中转 ——
    "openrouter":       {"apply": "https://openrouter.ai/keys",                                          "site": "https://openrouter.ai"},
    "together":         {"apply": "https://api.together.xyz/settings/api-keys",                          "site": "https://www.together.ai"},
    "fireworks":        {"apply": "https://fireworks.ai/api-keys",                                       "site": "https://fireworks.ai"},
    "aihubmix":         {"apply": "https://aihubmix.com/token",                                          "site": "https://aihubmix.com"},
    "302-ai":           {"apply": "https://302.ai/keys",                                                 "site": "https://302.ai"},
    # —— 本地 / 自部署 安装指南 ——
    "ollama":           {"install": "https://ollama.com/download",                                       "site": "https://ollama.com"},
    "lm-studio":        {"install": "https://lmstudio.ai/download",                                      "site": "https://lmstudio.ai"},
    "xinference":       {"install": "https://inference.readthedocs.io/en/latest/getting_started/installation.html", "site": "https://github.com/xorbitsai/inference"},
    "vllm":             {"install": "https://docs.vllm.ai/en/latest/getting_started/installation.html",  "site": "https://github.com/vllm-project/vllm"},
    "gpustack":         {"install": "https://docs.gpustack.ai/latest/installation/installation-script/", "site": "https://gpustack.ai"},
    "llama-cpp":        {"install": "https://github.com/ggml-org/llama.cpp#building",                    "site": "https://github.com/ggml-org/llama.cpp"},
    "localai":          {"install": "https://localai.io/basics/getting_started/",                        "site": "https://localai.io"},
    "tgi":              {"install": "https://huggingface.co/docs/text-generation-inference/quicktour",   "site": "https://github.com/huggingface/text-generation-inference"},
    "text-gen-webui":   {"install": "https://github.com/oobabooga/text-generation-webui#installation",   "site": "https://github.com/oobabooga/text-generation-webui"},
    "koboldcpp":        {"install": "https://github.com/LostRuins/koboldcpp#quick-start",                "site": "https://github.com/LostRuins/koboldcpp"},
    "jan":              {"install": "https://jan.ai/docs/quickstart",                                    "site": "https://jan.ai"},
    "gpt4all":          {"install": "https://docs.gpt4all.io/gpt4all_desktop/quickstart.html",           "site": "https://www.nomic.ai/gpt4all"},
    "openllm":          {"install": "https://github.com/bentoml/OpenLLM#quickstart",                     "site": "https://github.com/bentoml/OpenLLM"},
    "infinity":         {"install": "https://michaelfeil.github.io/infinity/latest/deploy/",             "site": "https://github.com/michaelfeil/infinity"},
    # —— 自定义 / 兼容 ——
    "oneapi":           {"install": "https://github.com/songquanpeng/one-api#%E9%83%A8%E7%BD%B2",        "site": "https://github.com/songquanpeng/one-api"},
    "new-api":          {"install": "https://docs.newapi.pro/installation/",                             "site": "https://github.com/Calcium-Ion/new-api"},
}


def _attach_provider_links() -> None:
    """把 _PROVIDER_LINKS 应用到 PROVIDER_CATALOG 上(运行时一次性,幂等)。"""
    for p in PROVIDER_CATALOG:
        link = _PROVIDER_LINKS.get(p.pid)
        if not link:
            continue
        if not p.apply_key_url:
            p.apply_key_url = link.get("apply", "")
        if not p.install_guide_url:
            p.install_guide_url = link.get("install", "")
        if not p.website_url:
            p.website_url = link.get("site", "")


_attach_provider_links()


# ---------------------------------------------------------------------------
# 各厂商内置主流模型清单(2026-04 校验快照)
# ---------------------------------------------------------------------------
# 设计:
#   - 用单独的 _PROVIDER_DEFAULT_MODELS 表,而不是塞进 ProviderMeta 行,避免 60 行
#     Provider 定义被撑爆;键不在表里的厂商 default_models 留空,前端展示"未配置"CTA。
#   - 模型选择策略:每家厂商最具代表性的 3-6 个模型,优先选"还在线 + 公开计费档"的;
#     退役/Beta/限内测的不收。
#   - 字段名严格对齐 PlatformConfig 字段(llm_models / embed_models / rerank_models /
#     text2image_models / image2text_models / speech2text_models / text2speech_models)。
#   - 这些只是"默认展示"——用户配完 key 做连通测试后,detected 会覆盖;DB 的优先级 > 默认。
_PROVIDER_DEFAULT_MODELS: Dict[str, Dict[str, List[str]]] = {
    # —— 国内主流 ——
    "deepseek": {
        "llm_models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "bailian": {
        "llm_models": [
            "qwen-max", "qwen-plus", "qwen-turbo", "qwen-long",
            "qwen3-235b-a22b", "qwen3-32b", "qwen2.5-72b-instruct",
            "qwq-plus", "qwq-32b",
        ],
        "embed_models": ["text-embedding-v3", "text-embedding-v2"],
        "rerank_models": ["gte-rerank"],
        "text2image_models": ["wanx-v1", "flux-schnell", "flux-dev"],
        "image2text_models": ["qwen-vl-max", "qwen-vl-plus", "qwen2.5-vl-72b-instruct"],
        "speech2text_models": ["paraformer-v2", "sensevoice-v1"],
        "text2speech_models": ["cosyvoice-v1", "sambert-zhichu-v1"],
    },
    "zhipu": {
        "llm_models": ["glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long", "glm-zero-preview"],
        "embed_models": ["embedding-3", "embedding-2"],
        "image2text_models": ["glm-4v-plus", "glm-4v"],
        "text2image_models": ["cogview-3-plus", "cogview-3"],
    },
    "moonshot": {
        "llm_models": [
            "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
            "kimi-latest", "kimi-thinking-preview",
        ],
        "image2text_models": ["moonshot-v1-8k-vision-preview", "moonshot-v1-32k-vision-preview"],
    },
    "volcengine": {
        "llm_models": [
            "doubao-1.5-pro-256k", "doubao-1.5-pro-32k", "doubao-1.5-lite-32k",
            "doubao-pro-256k", "doubao-pro-32k", "doubao-lite-32k",
            "doubao-1.5-thinking-pro",
        ],
        "embed_models": ["doubao-embedding", "doubao-embedding-large"],
        "image2text_models": ["doubao-1.5-vision-pro-32k", "doubao-vision-pro-32k"],
        "text2image_models": ["doubao-seedream-3-0-t2i", "doubao-seedream-2-0-t2i"],
    },
    "minimax": {
        "llm_models": ["MiniMax-Text-01", "abab6.5s-chat", "abab6.5-chat", "abab5.5-chat"],
        "embed_models": ["embo-01"],
        "text2image_models": ["image-01"],
        "speech2text_models": ["speech-02-hd", "speech-01-hd"],
        "text2speech_models": ["speech-02-hd", "speech-01-turbo"],
    },
    "baidu-qianfan": {
        "llm_models": [
            "ernie-4.5-turbo-32k", "ernie-4.0-turbo-128k", "ernie-4.0-8k",
            "ernie-3.5-128k", "ernie-speed-128k", "ernie-tiny-8k",
        ],
        "embed_models": ["embedding-v1", "bge-large-zh"],
    },
    "tencent-hunyuan": {
        "llm_models": ["hunyuan-pro", "hunyuan-standard", "hunyuan-lite", "hunyuan-turbo"],
        "embed_models": ["hunyuan-embedding"],
        "image2text_models": ["hunyuan-vision"],
    },
    "lingyi-wanwu": {
        "llm_models": ["yi-lightning", "yi-large", "yi-medium", "yi-large-rag", "yi-large-fc"],
        "image2text_models": ["yi-vision"],
    },
    "baichuan": {
        "llm_models": ["Baichuan4-Turbo", "Baichuan4-Air", "Baichuan4", "Baichuan3-Turbo"],
        "embed_models": ["Baichuan-Text-Embedding"],
    },
    "step-ai": {
        "llm_models": ["step-2-16k", "step-1-256k", "step-1-128k", "step-1-flash", "step-r1-v-mini"],
        "image2text_models": ["step-1v-32k", "step-1v-8k"],
        "text2image_models": ["step-1x-medium"],
    },
    "siliconflow": {
        "llm_models": [
            "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen2.5-72B-Instruct", "Qwen/QwQ-32B",
            "meta-llama/Meta-Llama-3.1-405B-Instruct",
        ],
        "embed_models": ["BAAI/bge-m3", "BAAI/bge-large-zh-v1.5"],
        "rerank_models": ["BAAI/bge-reranker-v2-m3"],
        "image2text_models": ["Qwen/Qwen2-VL-72B-Instruct"],
        "text2image_models": ["black-forest-labs/FLUX.1-dev", "stabilityai/stable-diffusion-3-medium"],
    },
    "modelscope": {
        "llm_models": [
            "Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3",
            "Qwen/QwQ-32B-Preview",
        ],
        "embed_models": ["iic/nlp_gte_sentence-embedding_chinese-base"],
    },
    # —— 国外主流 ——
    "openai": {
        "llm_models": [
            "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-preview", "o1-mini", "o3-mini",
            "gpt-3.5-turbo",
        ],
        "embed_models": ["text-embedding-3-large", "text-embedding-3-small", "text-embedding-ada-002"],
        "image2text_models": ["gpt-4o", "gpt-4o-mini"],
        "text2image_models": ["dall-e-3", "dall-e-2"],
        "speech2text_models": ["whisper-1"],
        "text2speech_models": ["tts-1", "tts-1-hd"],
    },
    "anthropic": {
        "llm_models": [
            "claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5",
            "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest",
        ],
        "image2text_models": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-3-5-sonnet-latest"],
    },
    "gemini": {
        "llm_models": [
            "gemini-2.0-flash", "gemini-2.0-flash-thinking-exp",
            "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b",
        ],
        "embed_models": ["text-embedding-004"],
        "image2text_models": ["gemini-2.0-flash", "gemini-1.5-pro"],
    },
    "mistral": {
        "llm_models": [
            "mistral-large-latest", "mistral-small-latest", "ministral-8b-latest",
            "ministral-3b-latest", "codestral-latest", "open-mistral-nemo",
        ],
        "embed_models": ["mistral-embed"],
    },
    "grok": {
        "llm_models": ["grok-3", "grok-2-latest", "grok-2-vision-latest", "grok-beta"],
        "image2text_models": ["grok-2-vision-latest"],
    },
    "groq": {
        "llm_models": [
            "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "llama3-70b-8192",
            "mixtral-8x7b-32768", "gemma2-9b-it", "qwen-2.5-32b",
        ],
        "speech2text_models": ["whisper-large-v3", "whisper-large-v3-turbo"],
    },
    "cerebras": {
        "llm_models": ["llama-3.3-70b", "llama3.1-8b", "llama3.1-70b"],
    },
    "perplexity": {
        "llm_models": [
            "sonar", "sonar-pro", "sonar-reasoning", "sonar-reasoning-pro",
            "llama-3.1-sonar-large-128k-online",
        ],
    },
    "cohere": {
        "llm_models": ["command-r-plus", "command-r", "command-r7b", "command"],
        "embed_models": ["embed-english-v3.0", "embed-multilingual-v3.0"],
        "rerank_models": ["rerank-v3.5", "rerank-multilingual-v3.0"],
    },
    "voyage-ai": {
        "embed_models": ["voyage-3-large", "voyage-3", "voyage-3-lite", "voyage-code-3"],
        "rerank_models": ["rerank-2", "rerank-2-lite"],
    },
    "jina": {
        "embed_models": ["jina-embeddings-v3", "jina-embeddings-v2-base-zh"],
        "rerank_models": ["jina-reranker-v2-base-multilingual"],
    },
    "nvidia": {
        "llm_models": [
            "meta/llama-3.1-405b-instruct", "meta/llama-3.3-70b-instruct",
            "deepseek-ai/deepseek-r1", "nvidia/llama-3.1-nemotron-70b-instruct",
        ],
    },
    "huggingface": {
        "llm_models": [
            "meta-llama/Meta-Llama-3.1-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct",
            "mistralai/Mistral-7B-Instruct-v0.3",
        ],
    },
    "github-models": {
        "llm_models": [
            "gpt-4o", "gpt-4o-mini", "Phi-3.5-MoE-instruct", "Mistral-large",
            "Llama-3.3-70B-Instruct",
        ],
    },
    # —— 聚合 / 中转 ——
    "openrouter": {
        "llm_models": [
            "anthropic/claude-3.5-sonnet", "openai/gpt-4o", "google/gemini-2.0-flash-exp:free",
            "deepseek/deepseek-chat", "meta-llama/llama-3.3-70b-instruct",
        ],
    },
    "together": {
        "llm_models": [
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ],
        "embed_models": ["BAAI/bge-base-en-v1.5", "BAAI/bge-large-en-v1.5"],
    },
    "fireworks": {
        "llm_models": [
            "accounts/fireworks/models/llama-v3p3-70b-instruct",
            "accounts/fireworks/models/deepseek-v3",
            "accounts/fireworks/models/qwen2p5-72b-instruct",
        ],
    },
    # —— 本地 / 自部署 (常见拉取的开源模型) ——
    "ollama": {
        "llm_models": ["llama3.3", "llama3.2", "qwen2.5:7b", "deepseek-r1:7b", "mistral", "phi4"],
        "embed_models": ["nomic-embed-text", "mxbai-embed-large", "bge-m3"],
        "image2text_models": ["llava", "llama3.2-vision"],
    },
    "xinference": {
        "llm_models": ["qwen2.5-instruct", "deepseek-v3", "llama-3.3-instruct"],
        "embed_models": ["bge-m3", "bge-large-zh-v1.5"],
        "rerank_models": ["bge-reranker-v2-m3"],
    },
    "lm-studio": {
        "llm_models": ["qwen2.5-7b-instruct", "llama-3.2-3b-instruct", "phi-3.5-mini-instruct"],
    },
    "vllm": {
        "llm_models": ["Qwen/Qwen2.5-7B-Instruct", "meta-llama/Meta-Llama-3.1-8B-Instruct"],
    },
}


def _attach_provider_default_models() -> None:
    """把 _PROVIDER_DEFAULT_MODELS 写到 PROVIDER_CATALOG 各 ProviderMeta.default_models 上。"""
    for p in PROVIDER_CATALOG:
        defaults = _PROVIDER_DEFAULT_MODELS.get(p.pid)
        if not defaults:
            continue
        # 浅拷贝防止 catalog 被外部 mutate 时污染原表
        p.default_models = {k: list(v) for k, v in defaults.items()}


_attach_provider_default_models()


# ---------------------------------------------------------------------------
# 主流模型元信息 seed —— 让"模型广场"卡片 hover 时立刻有简介可读
# ---------------------------------------------------------------------------
# 设计:
#   - 这是一份"开箱即可用"的快照,含 description / release_date / context_length /
#     performance_note 四字段,不写 DB(避免每次启动反复 upsert);
#   - catalog 端点把 seed 与 model_metadata 表合并:**DB 优先 > seed**;
#     即用户用"AI 补全简介"重写过 → 用 DB,否则回落到 seed。
#   - 数据来源:各厂商公开 API 文档 / 官方博客(2026-04 校验)。覆盖范围以 30+
#     最常见模型为主,长尾留给用户用 enrich 端点跑 LLM 自动补全。
#
# 字段约定:
#   description     ≤120 中文字符
#   release_date    YYYY-MM(精确到月够用,避免 day 老滚)或空
#   context_length  tokens 整数;不知道时给 None(0 也行,前端 0 不展示)
#   performance_note 一句话能力档位,≤30 中文字符
_MODEL_METADATA_SEED: Dict[Tuple[str, str], Dict[str, Any]] = {
    # ─── 国内 ───
    ("deepseek", "deepseek-chat"): {
        "description": "DeepSeek-V3 主力对话模型,671B MoE 架构,通用能力均衡,中英文表现强,性价比高。",
        "release_date": "2024-12", "context_length": 64000,
        "performance_note": "旗舰对话、性价比之王",
    },
    ("deepseek", "deepseek-reasoner"): {
        "description": "DeepSeek-R1 系列思考型模型,具备显式推理链,擅长数学 / 代码 / 复杂推理,首 token 稍慢。",
        "release_date": "2025-01", "context_length": 64000,
        "performance_note": "强推理,数学 / 代码竞赛级",
    },
    ("bailian", "qwen-max"): {
        "description": "千问最强商用版,Qwen 系列旗舰,长上下文 + 工具调用稳定,适合企业复杂场景。",
        "release_date": "2024-09", "context_length": 32768,
        "performance_note": "旗舰商用、长上下文",
    },
    ("bailian", "qwen-plus"): {
        "description": "千问中端版,在 max 与 turbo 间取得能力 / 价格平衡,日常对话 / 摘要 / 工具调用主力。",
        "release_date": "2024-09", "context_length": 131072,
        "performance_note": "性价比主力、长上下文",
    },
    ("bailian", "qwen-turbo"): {
        "description": "千问极速版,响应快、价格低,适合高 QPS 简单任务(分类 / 摘要 / 客服)。",
        "release_date": "2024-09", "context_length": 8192,
        "performance_note": "轻量、快速、低成本",
    },
    ("bailian", "qwen-long"): {
        "description": "千问超长上下文版,1M tokens 输入,擅长长文档问答与摘要。",
        "release_date": "2024-04", "context_length": 1000000,
        "performance_note": "超长文档专用",
    },
    ("bailian", "qwen3-235b-a22b"): {
        "description": "Qwen3 系列 235B MoE,激活参数 22B,2025 年新一代旗舰,综合能力对标 GPT-4o。",
        "release_date": "2025-04", "context_length": 131072,
        "performance_note": "新一代旗舰、复杂推理",
    },
    ("bailian", "qwq-plus"): {
        "description": "QwQ 思考模型云端版,擅长数学 / 代码 / 多步推理,带显式推理过程。",
        "release_date": "2024-11", "context_length": 32768,
        "performance_note": "强推理、数学代码",
    },
    ("bailian", "qwen2.5-vl-72b-instruct"): {
        "description": "千问视觉模型 72B,理解图像 / 视频帧 / 图表 / 文档,VQA 能力强。",
        "release_date": "2025-01", "context_length": 32768,
        "performance_note": "视觉理解旗舰",
    },
    ("bailian", "text-embedding-v3"): {
        "description": "千问 v3 嵌入模型,1024 / 768 / 512 / 256 多维度,支持中英多语,适合 RAG 召回。",
        "release_date": "2024-08", "context_length": 8192,
        "performance_note": "通用嵌入、多维度",
    },
    ("zhipu", "glm-4-plus"): {
        "description": "智谱 GLM-4-Plus 旗舰对话,综合能力对标 GPT-4 系列,中英双语 + 工具调用稳定。",
        "release_date": "2024-08", "context_length": 128000,
        "performance_note": "旗舰对话、长上下文",
    },
    ("zhipu", "glm-4-air"): {
        "description": "GLM-4 轻量版,价格便宜、响应快,日常对话 / 摘要 / 简单工具调用够用。",
        "release_date": "2024-06", "context_length": 128000,
        "performance_note": "轻量、性价比",
    },
    ("zhipu", "glm-4-flash"): {
        "description": "GLM-4 极速免费版(限速),适合开发原型与高频简单任务。",
        "release_date": "2024-08", "context_length": 128000,
        "performance_note": "免费、高频简单任务",
    },
    ("zhipu", "glm-zero-preview"): {
        "description": "智谱思考模型 preview,带显式推理链,数学 / 代码 / 逻辑题表现强。",
        "release_date": "2025-01", "context_length": 16384,
        "performance_note": "思考推理、preview",
    },
    ("zhipu", "embedding-3"): {
        "description": "智谱第三代嵌入模型,2048 维,中英多语种检索效果稳定。",
        "release_date": "2024-09", "context_length": 8192,
        "performance_note": "通用嵌入",
    },
    ("zhipu", "glm-4v-plus"): {
        "description": "GLM-4V Plus 多模态旗舰,理解图像 / 视频,适合 VQA / 文档 OCR / UI 理解。",
        "release_date": "2024-10", "context_length": 16384,
        "performance_note": "多模态旗舰",
    },
    ("moonshot", "moonshot-v1-128k"): {
        "description": "Kimi 长上下文旗舰,128K tokens 输入,擅长长文档阅读 / 总结 / 代码仓库分析。",
        "release_date": "2024-03", "context_length": 128000,
        "performance_note": "超长上下文、文档分析",
    },
    ("moonshot", "moonshot-v1-32k"): {
        "description": "Kimi 通用版,32K 上下文,日常对话 / 摘要 / 工具调用平衡选择。",
        "release_date": "2024-03", "context_length": 32768,
        "performance_note": "通用对话",
    },
    ("moonshot", "kimi-latest"): {
        "description": "Kimi 最新版本(滚动更新),始终指向当前最新 production 模型。",
        "release_date": "2025-01", "context_length": 128000,
        "performance_note": "rolling 最新版",
    },
    ("moonshot", "kimi-thinking-preview"): {
        "description": "Kimi 思考模型 preview,o1 风格显式推理,数学 / 代码 / 复杂逻辑表现强。",
        "release_date": "2025-01", "context_length": 32768,
        "performance_note": "思考推理 preview",
    },
    ("volcengine", "doubao-1.5-pro-256k"): {
        "description": "豆包 1.5 Pro 长上下文版,256K tokens,擅长长文档 / 多轮对话 / 工具调用。",
        "release_date": "2025-01", "context_length": 256000,
        "performance_note": "长上下文旗舰",
    },
    ("volcengine", "doubao-1.5-pro-32k"): {
        "description": "豆包 1.5 Pro 主力版,综合能力均衡,中英对话 / 推理 / 工具表现稳定。",
        "release_date": "2025-01", "context_length": 32768,
        "performance_note": "通用旗舰",
    },
    ("volcengine", "doubao-1.5-thinking-pro"): {
        "description": "豆包 1.5 思考模型,显式推理链,数学 / 代码 / 复杂推理任务专用。",
        "release_date": "2025-04", "context_length": 32768,
        "performance_note": "思考推理",
    },
    ("minimax", "MiniMax-Text-01"): {
        "description": "MiniMax 旗舰,4M tokens 超长上下文,Linear Attention 架构,长文档处理标杆。",
        "release_date": "2025-01", "context_length": 4000000,
        "performance_note": "超长上下文之王",
    },
    ("minimax", "abab6.5s-chat"): {
        "description": "MiniMax abab 6.5s 系列,日常对话 / 中文创作 / 工具调用,价格友好。",
        "release_date": "2024-04", "context_length": 245760,
        "performance_note": "通用、中文创作",
    },
    ("baidu-qianfan", "ernie-4.0-turbo-128k"): {
        "description": "百度文心 4.0 Turbo 长上下文,128K tokens,综合能力强,工具调用稳定。",
        "release_date": "2024-08", "context_length": 128000,
        "performance_note": "旗舰、长上下文",
    },
    ("baidu-qianfan", "ernie-3.5-128k"): {
        "description": "文心 3.5 长上下文版,日常对话 / 摘要 / 工具调用主力,价格平衡。",
        "release_date": "2024-06", "context_length": 128000,
        "performance_note": "通用主力",
    },
    ("tencent-hunyuan", "hunyuan-pro"): {
        "description": "腾讯混元 Pro 旗舰对话,综合能力均衡,中文创作与逻辑推理表现稳定。",
        "release_date": "2024-08", "context_length": 32768,
        "performance_note": "旗舰对话",
    },
    ("lingyi-wanwu", "yi-lightning"): {
        "description": "零一万物 Yi-Lightning,2024 年底 SOTA 中文模型,LMArena 中文榜前列。",
        "release_date": "2024-10", "context_length": 16384,
        "performance_note": "中文旗舰、SOTA",
    },
    ("step-ai", "step-2-16k"): {
        "description": "阶跃星辰 Step-2,万亿参数级,综合能力对标 GPT-4 系列,工具调用稳定。",
        "release_date": "2024-11", "context_length": 16384,
        "performance_note": "万亿参数旗舰",
    },
    ("siliconflow", "deepseek-ai/DeepSeek-V3"): {
        "description": "硅基流动托管的 DeepSeek-V3,API 兼容 OpenAI,免维护版本,定价友好。",
        "release_date": "2024-12", "context_length": 64000,
        "performance_note": "托管旗舰、性价比",
    },
    ("siliconflow", "BAAI/bge-m3"): {
        "description": "BGE-M3 多语种 / 多功能 / 多粒度嵌入,中英检索 SOTA 之一,RAG 首选。",
        "release_date": "2024-01", "context_length": 8192,
        "performance_note": "RAG 嵌入首选",
    },
    ("siliconflow", "BAAI/bge-reranker-v2-m3"): {
        "description": "BGE Reranker v2 多语种重排,接在 bge-m3 召回后,显著提升 RAG top-k 命中。",
        "release_date": "2024-04", "context_length": 8192,
        "performance_note": "重排首选",
    },
    # ─── 国外 ───
    ("openai", "gpt-4o"): {
        "description": "GPT-4o 多模态旗舰,文本 / 视觉 / 音频统一,综合能力顶尖。",
        "release_date": "2024-05", "context_length": 128000,
        "performance_note": "多模态旗舰",
    },
    ("openai", "gpt-4o-mini"): {
        "description": "GPT-4o-mini 性价比版,能力接近 GPT-4 Turbo,价格仅 1/30,日常任务首选。",
        "release_date": "2024-07", "context_length": 128000,
        "performance_note": "性价比主力",
    },
    ("openai", "o1-preview"): {
        "description": "OpenAI 首代思考模型,长链推理,数学 / 代码 / 科研问题表现突出,首 token 慢。",
        "release_date": "2024-09", "context_length": 128000,
        "performance_note": "强推理、preview",
    },
    ("openai", "o1-mini"): {
        "description": "o1 系列轻量版,擅长 STEM 推理,价格便宜、响应快于 o1-preview。",
        "release_date": "2024-09", "context_length": 128000,
        "performance_note": "STEM 推理、性价比",
    },
    ("openai", "o3-mini"): {
        "description": "o3 系列轻量版,2025 年新一代思考模型,数学 / 代码 SOTA。",
        "release_date": "2025-01", "context_length": 200000,
        "performance_note": "新一代推理",
    },
    ("openai", "text-embedding-3-large"): {
        "description": "OpenAI 第三代大型嵌入模型,3072 维,跨语种检索稳定。",
        "release_date": "2024-01", "context_length": 8191,
        "performance_note": "通用嵌入",
    },
    ("openai", "text-embedding-3-small"): {
        "description": "OpenAI 第三代小型嵌入,1536 维,价格仅大型 1/5,日常 RAG 够用。",
        "release_date": "2024-01", "context_length": 8191,
        "performance_note": "性价比嵌入",
    },
    ("openai", "whisper-1"): {
        "description": "Whisper v2 通用语音识别,支持 99 种语言,中文表现稳定。",
        "release_date": "2023-03", "context_length": 0,
        "performance_note": "多语 ASR 标杆",
    },
    ("openai", "tts-1"): {
        "description": "OpenAI TTS 标准版,6 种英文语音,中文也可用,价格友好。",
        "release_date": "2023-11", "context_length": 0,
        "performance_note": "标准 TTS",
    },
    ("openai", "tts-1-hd"): {
        "description": "OpenAI TTS 高清版,音质更好但价格 4 倍,适合产出态使用。",
        "release_date": "2023-11", "context_length": 0,
        "performance_note": "高清 TTS",
    },
    ("openai", "dall-e-3"): {
        "description": "DALL·E 3 文生图,prompt 遵循度强,适合创意 / 海报 / 插画。",
        "release_date": "2023-10", "context_length": 0,
        "performance_note": "文生图主力",
    },
    ("anthropic", "claude-opus-4-7"): {
        "description": "Claude Opus 4.7 当家旗舰,超长推理 + 工具使用 + 代码能力顶级,适合复杂任务。",
        "release_date": "2026-01", "context_length": 200000,
        "performance_note": "超旗舰、复杂任务",
    },
    ("anthropic", "claude-sonnet-4-6"): {
        "description": "Claude Sonnet 4.6 主力版,能力 / 价格 / 速度三角最佳平衡,日常推荐。",
        "release_date": "2025-11", "context_length": 200000,
        "performance_note": "主力推荐",
    },
    ("anthropic", "claude-haiku-4-5"): {
        "description": "Claude Haiku 4.5 轻量极速版,价格便宜、首 token 快,适合高频简单任务。",
        "release_date": "2025-10", "context_length": 200000,
        "performance_note": "轻量、性价比",
    },
    ("anthropic", "claude-3-5-sonnet-latest"): {
        "description": "Claude 3.5 Sonnet 上代主力,工具使用 / 编码强,目前仍可用。",
        "release_date": "2024-10", "context_length": 200000,
        "performance_note": "上代主力",
    },
    ("gemini", "gemini-2.0-flash"): {
        "description": "Gemini 2.0 Flash,Google 主力多模态,文 / 图 / 视频统一,工具调用稳定。",
        "release_date": "2024-12", "context_length": 1048576,
        "performance_note": "多模态、超长上下文",
    },
    ("gemini", "gemini-1.5-pro"): {
        "description": "Gemini 1.5 Pro,2M tokens 超长上下文,适合长文档与代码仓库理解。",
        "release_date": "2024-05", "context_length": 2000000,
        "performance_note": "超长上下文",
    },
    ("gemini", "gemini-1.5-flash"): {
        "description": "Gemini 1.5 Flash,价格便宜、响应快,日常任务主力。",
        "release_date": "2024-05", "context_length": 1048576,
        "performance_note": "性价比、长上下文",
    },
    ("mistral", "mistral-large-latest"): {
        "description": "Mistral Large 旗舰,工具调用 / 代码 / 多语种表现强,欧洲合规友好。",
        "release_date": "2024-11", "context_length": 128000,
        "performance_note": "欧系旗舰",
    },
    ("mistral", "ministral-8b-latest"): {
        "description": "Ministral 8B 边缘部署版,小参数高密度,适合本地 / 边缘设备。",
        "release_date": "2024-10", "context_length": 128000,
        "performance_note": "轻量、边缘部署",
    },
    ("mistral", "codestral-latest"): {
        "description": "Codestral 代码专用,80+ 编程语言,FIM(中间填充)能力强。",
        "release_date": "2024-05", "context_length": 32000,
        "performance_note": "代码专用",
    },
    ("grok", "grok-3"): {
        "description": "xAI Grok 3,2025 新版,数学 / 推理 SOTA 候选,知识截止较新。",
        "release_date": "2025-02", "context_length": 1000000,
        "performance_note": "新一代旗舰",
    },
    ("grok", "grok-2-vision-latest"): {
        "description": "Grok 2 视觉版,理解图像 + 实时网络数据,适合时事问答。",
        "release_date": "2024-10", "context_length": 32768,
        "performance_note": "视觉 + 实时网络",
    },
    ("groq", "llama-3.3-70b-versatile"): {
        "description": "Groq LPU 托管 Llama 3.3 70B,推理速度顶级(~280 tok/s),适合实时场景。",
        "release_date": "2024-12", "context_length": 131072,
        "performance_note": "极速 LPU 推理",
    },
    ("perplexity", "sonar-pro"): {
        "description": "Perplexity Sonar Pro,带网页搜索的对话模型,适合时事 / 引用追溯。",
        "release_date": "2025-01", "context_length": 200000,
        "performance_note": "联网搜索、引用",
    },
    ("cohere", "command-r-plus"): {
        "description": "Cohere Command R+,RAG / 工具调用专用,多语种召回与重排稳定。",
        "release_date": "2024-04", "context_length": 128000,
        "performance_note": "RAG 专用",
    },
    ("cohere", "embed-multilingual-v3.0"): {
        "description": "Cohere v3 多语种嵌入,1024 维,100+ 语种,跨语检索稳定。",
        "release_date": "2023-11", "context_length": 512,
        "performance_note": "多语嵌入",
    },
    ("cohere", "rerank-v3.5"): {
        "description": "Cohere Rerank v3.5,接在 embed 召回后做精排,显著提升 RAG 精度。",
        "release_date": "2024-12", "context_length": 4096,
        "performance_note": "重排专用",
    },
    ("voyage-ai", "voyage-3-large"): {
        "description": "Voyage 3 Large 嵌入,通用领域 SOTA,适合代码 / 文档 / 法律文本。",
        "release_date": "2024-09", "context_length": 32000,
        "performance_note": "通用嵌入旗舰",
    },
    ("voyage-ai", "rerank-2"): {
        "description": "Voyage Rerank 2,接在 voyage embed 后做精排,RAG 端到端搭档。",
        "release_date": "2024-08", "context_length": 16000,
        "performance_note": "重排专用",
    },
    ("jina", "jina-embeddings-v3"): {
        "description": "Jina v3 嵌入,1024 维,89 种语言,Matryoshka 可裁剪维度。",
        "release_date": "2024-09", "context_length": 8192,
        "performance_note": "多语嵌入、可裁剪",
    },
    # ─── 本地 ───
    ("ollama", "llama3.3"): {
        "description": "Meta Llama 3.3 70B,本地推理,综合能力强,~40GB 显存(4-bit 量化 ~24GB)。",
        "release_date": "2024-12", "context_length": 131072,
        "performance_note": "本地大模型",
    },
    ("ollama", "qwen2.5:7b"): {
        "description": "Qwen2.5 7B 本地版,中英对话 / 工具调用,~5GB 显存,消费级显卡可跑。",
        "release_date": "2024-09", "context_length": 131072,
        "performance_note": "本地中文友好",
    },
    ("ollama", "deepseek-r1:7b"): {
        "description": "DeepSeek-R1 7B 蒸馏版,本地思考模型,数学 / 代码题表现可观。",
        "release_date": "2025-01", "context_length": 131072,
        "performance_note": "本地思考",
    },
    ("ollama", "nomic-embed-text"): {
        "description": "Nomic 嵌入模型,768 维,英文为主,RAG 入门友好,~300MB。",
        "release_date": "2024-02", "context_length": 8192,
        "performance_note": "本地 RAG 嵌入",
    },
    ("ollama", "bge-m3"): {
        "description": "BGE-M3 本地版(GGUF),中英多语 RAG,~2GB,本地 RAG 主力。",
        "release_date": "2024-01", "context_length": 8192,
        "performance_note": "本地 RAG 主力",
    },
    # ─── 聚合 ───
    ("openrouter", "anthropic/claude-3.5-sonnet"): {
        "description": "通过 OpenRouter 接入的 Claude 3.5 Sonnet,免 Anthropic 直签,定价透明。",
        "release_date": "2024-10", "context_length": 200000,
        "performance_note": "聚合接入",
    },
    ("openrouter", "openai/gpt-4o"): {
        "description": "通过 OpenRouter 接入的 GPT-4o,免 OpenAI 直签,接受多种付款方式。",
        "release_date": "2024-05", "context_length": 128000,
        "performance_note": "聚合接入",
    },
    ("openrouter", "google/gemini-2.0-flash-exp:free"): {
        "description": "Gemini 2.0 Flash 实验版,通过 OpenRouter 免费(限速),适合开发原型。",
        "release_date": "2024-12", "context_length": 1048576,
        "performance_note": "免费、限速",
    },
    ("openrouter", "deepseek/deepseek-chat"): {
        "description": "通过 OpenRouter 接入的 DeepSeek-V3,信用卡付费,绕过国内 API 注册流程。",
        "release_date": "2024-12", "context_length": 64000,
        "performance_note": "聚合接入",
    },
}


def model_metadata_seed_for(platform_name: str) -> Dict[str, Dict[str, Any]]:
    """返回某厂商 model_id → metadata 的 dict;catalog 端点 join 用。"""
    out: Dict[str, Dict[str, Any]] = {}
    for (pname, model_id), meta in _MODEL_METADATA_SEED.items():
        if pname == platform_name:
            out[model_id] = dict(meta)
    return out


_PROVIDER_BY_ID: Dict[str, ProviderMeta] = {p.pid: p for p in PROVIDER_CATALOG}


def _logo_url(p: ProviderMeta) -> str:
    """返回 <img src> 的值；没有本地 svg 时返回空字符串（让 UI 回退到 letter avatar）。"""
    if not p.logo:
        return ""
    return f"/static/model_logos/{p.logo}"


# ---------------------------------------------------------------------------
# 57 题 P2:云厂商分组网格(c 项 — "下面是根据分类卡片式显示全部的厂商")
# ---------------------------------------------------------------------------


# 分组顺序与中文标签
_CLOUD_GROUP_ORDER: List[Tuple[str, str, str]] = [
    # (group_key, label, icon)
    ("推荐", "推荐",  "star"),
    ("国内", "国内",  "public"),
    ("国外", "国外",  "language"),
    ("聚合", "聚合中转", "hub"),
    ("其它", "其它",  "more_horiz"),
]


def _classify_cloud_provider(p: ProviderMeta) -> str:
    """把厂商按 tags 归到 _CLOUD_GROUP_ORDER 的某组。本地厂商不参与(由 hero strip 渲染)。"""
    tags = tuple(p.tags or ())
    if "本地" in tags:
        return ""  # 本地不属于云分组网格
    if "推荐" in tags:
        return "推荐"
    if "聚合" in tags:
        return "聚合"
    if "国内" in tags:
        return "国内"
    if "国外" in tags:
        return "国外"
    return "其它"


def group_cloud_providers(
    providers: List[ProviderMeta],
    *,
    search: str = "",
    filter_tag: str = "",
) -> Dict[str, List[ProviderMeta]]:
    """把 providers 分组返回。可选搜索和单 tag 过滤(和 hero strip 的"更多"对话框同源)。

    Args:
        search: 在 pid / display_name 中模糊匹配(忽略大小写)。
        filter_tag: 仅保留 tags 含此 tag 的厂商。空 = 不过滤。

    Returns:
        ``{group_label: [providers]}``;空组不会出现在结果中。
    """
    q = (search or "").strip().lower()
    out: Dict[str, List[ProviderMeta]] = {}
    for p in providers:
        group = _classify_cloud_provider(p)
        if not group:
            continue  # 本地厂商
        if filter_tag and filter_tag not in (p.tags or ()):
            continue
        if q and q not in p.pid.lower() and q not in p.display_name.lower():
            continue
        out.setdefault(group, []).append(p)
    # 按 _CLOUD_GROUP_ORDER 重新排序返回(使用 dict 保留插入顺序)
    ordered: Dict[str, List[ProviderMeta]] = {}
    for key, _label, _icon in _CLOUD_GROUP_ORDER:
        if key in out:
            ordered[key] = out[key]
    return ordered


# 64.2 题:保存厂商配置后,云分组网格也要 refresh(否则禁用了厂商,卡片绿色对号还在)
# trigger_cloud_grid_refresh() 由 _save_all → _do_cascade_refresh 调用
_CLOUD_GRID_REFRESHERS: List[Callable[[], None]] = []


def trigger_cloud_grid_refresh() -> None:
    """触发所有已注册的云厂商分组网格刷新。dead client 的 fn 自动从 list 移除。"""
    for fn in list(_CLOUD_GRID_REFRESHERS):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            logger.debug("[cloud_grid] refresh failed: %r", e)
            try:
                _CLOUD_GRID_REFRESHERS.remove(fn)
            except ValueError:
                pass


def _render_cloud_providers_grouped(
    ui: Any,
    *,
    providers: List[ProviderMeta],
    state_lookup: Callable[[str], Tuple[bool, bool, int]],
    on_pick: Callable[[str], None],
    on_add_custom: Optional[Callable[[], None]] = None,
) -> Callable[[], None]:
    """57 题 P2 c 项:云厂商按分类卡片网格展示(全部,不限 8 张)。

    顶部:搜索框 + 标签筛选 chips + "添加自定义"按钮
    下方:推荐/国内/国外/聚合/其它 5 段 expansion,各自一列卡片网格

    返回 ``refresh()`` 钩子,供保存厂商配置后调用刷新各卡片状态。
    """
    state: Dict[str, str] = {"search": "", "filter_tag": ""}
    container = ui.column().classes("w-full q-mb-md").style("gap: 8px;")

    # 可用筛选 tag 候选(剔除"本地"因为本网格不显示本地)
    _ALL_TAGS = ["全部", "推荐", "国内", "国外", "聚合"]

    # 105 题:**顶栏 mount 一次,只刷新分组容器** — 搜索框输入时不再被 reload。
    # 旧实现:_refresh 时 container.clear() 把搜索框一起清掉,每次输入 1 字符
    # NiceGUI 都重 mount 整个搜索框 → 焦点丢失 + value 被设回 state["search"] →
    # 用户感知"页面被刷新,无法搜索"。
    # 改为:
    #   * `chrome_container` mount 一次 — 顶栏(搜索 + chips + 添加 + 刷新),永不 clear
    #   * `groups_container` 只 clear+ 重 mount 分组(用 _refresh_groups 触发)
    #   * 搜索/过滤回调只调 _refresh_groups,搜索框焦点和 value 都不动
    #   * 全量 refresh(_save_all 后)调 _refresh = 重渲分组 + 让 total 计数文本同步更新

    chrome_container = ui.column().classes("w-full").style("gap: 0;")
    groups_container = ui.column().classes("w-full").style("gap: 4px;")
    total_label_holder: Dict[str, Any] = {}

    def _compute_groups() -> Dict[str, List[Any]]:
        return group_cloud_providers(
            providers,
            search=state["search"],
            filter_tag=state["filter_tag"],
        )

    def _render_chrome() -> None:
        """顶栏:搜索 + chips + 添加 + 刷新 — **只 mount 一次**,永不重 mount。"""
        with chrome_container:
            with ui.card().props("flat bordered").classes("w-full").style(
                "background: #fafbfc; padding: 10px 12px;"
            ):
                with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                    ui.icon("storefront", size="18px").classes("text-grey-7")
                    ui.label("云厂商").classes("text-subtitle2")
                    total_init = sum(len(v) for v in _compute_groups().values())
                    total_label_holder["label"] = ui.label(
                        f"共 {total_init} 家 · 按分类展示"
                    ).classes("text-caption text-grey-6")
                    ui.space()
                    if on_add_custom is not None:
                        ui.button(
                            "添加自定义", icon="add",
                            on_click=lambda _e=None: on_add_custom(),
                        ).props("dense unelevated color=primary size=sm").tooltip(
                            "添加自定义 OpenAI 兼容厂商"
                        )
                    ui.button(
                        "刷新", icon="refresh", on_click=lambda: _refresh(),
                    ).props("dense flat color=primary size=sm")

                # 第二行:搜索框 + tag chips
                with ui.row().classes("items-center w-full no-wrap q-mt-xs").style(
                    "gap: 8px; flex-wrap: wrap;"
                ):
                    search_in = ui.input(
                        placeholder="搜索厂商名(pid 或显示名)...",
                        value=state["search"],
                    ).props("dense outlined clearable").classes("col-grow").style(
                        "max-width: 320px;"
                    )

                    def _on_search(e: Any) -> None:
                        state["search"] = str(getattr(e, "value", "") or "").strip()
                        _refresh_groups()
                    search_in.on("update:model-value", _on_search)

                    for tag in _ALL_TAGS:
                        active = (state["filter_tag"] or "全部") == tag
                        chip = ui.chip(tag).props(
                            "clickable" + (" color=primary text-color=white" if active else "")
                        )

                        def _on_chip(_e=None, t: str = tag) -> None:
                            state["filter_tag"] = "" if t == "全部" else t
                            _refresh_groups()

                        chip.on("click", _on_chip)

    def _refresh_groups() -> None:
        """只重渲下方分组容器 — 搜索/筛选/加载都只走这条路径。"""
        from chayuan.server.config_panel._safe_ui import is_client_alive
        if not is_client_alive(groups_container):
            try:
                _CLOUD_GRID_REFRESHERS.remove(_refresh)
            except ValueError:
                pass
            return
        groups = _compute_groups()
        # 同步更新顶栏的"共 N 家"计数
        total = sum(len(v) for v in groups.values())
        lbl = total_label_holder.get("label")
        if lbl is not None:
            try:
                lbl.set_text(f"共 {total} 家 · 按分类展示")
            except Exception:  # noqa: BLE001
                pass

        # 流式分块 mount — 每个分组一个 chunk
        from chayuan.server.config_panel.model_settings._async_mount import (
            chunked_async_render,
        )

        def _make_group_chunk(key: str, label: str, icon: str, items: list):
            def _chunk():
                with groups_container:
                    any_enabled = any(state_lookup(p.pid)[0] for p in items)
                    exp = ui.expansion(
                        f"{label} ({len(items)} 家)",
                        icon=icon,
                        value=bool(any_enabled),
                    ).classes("w-full").props("dense")
                    with exp:
                        with ui.row().classes("w-full q-pa-sm").style(
                            "gap: 8px; flex-wrap: wrap;"
                        ):
                            for p in items:
                                _render_cloud_provider_card(
                                    ui, p, state_lookup, on_pick,
                                )
            return (label, _chunk)

        chunks: List[Tuple[str, Callable[[], None]]] = []
        for key, label, icon in _CLOUD_GROUP_ORDER:
            items = groups.get(key, [])
            if not items:
                continue
            chunks.append(_make_group_chunk(key, label, icon, items))

        if chunks:
            chunked_async_render(
                ui, groups_container,
                "云厂商分组",
                chunks,
                initial_delay=0.03,
                batch_delay=0.03,
                show_progress=False,
            )
        else:
            # 搜索无结果 → 显示空提示
            try:
                groups_container.clear()
            except Exception:  # noqa: BLE001
                return
            with groups_container:
                ui.label(
                    f"未找到匹配 '{state['search']}' 的厂商" if state["search"]
                    else "无可用厂商"
                ).classes("text-grey-6 q-pa-md")

    def _refresh() -> None:
        """全量刷新(_save_all 后等):仅重渲分组,顶栏保留。"""
        _refresh_groups()

    _render_chrome()
    _refresh_groups()
    # 64.2 题:注册到全局 refresh list,供 _save_all → _do_cascade_refresh 调用
    if _refresh not in _CLOUD_GRID_REFRESHERS:
        _CLOUD_GRID_REFRESHERS.append(_refresh)
    return _refresh


def _render_cloud_provider_card(
    ui: Any,
    p: ProviderMeta,
    state_lookup: Callable[[str], Tuple[bool, bool, int]],
    on_pick: Callable[[str], None],
) -> None:
    """单张云厂商卡片(分组网格内一格)。**4 列**布局(64.1 题用户要求)。"""
    enabled, configured, n = state_lookup(p.pid)
    color = (p.color or "#6b7280")
    border = "#a7f3d0" if enabled else "#e5e7eb"
    bg = "#f0fdf4" if enabled else "white"

    # 4 列:容器 padding=12 + 卡片间 gap=8(3 个 gap) = 12*2+8*3=48,但容器 padding 已在
    # 父 row q-pa-sm 给了(8px),所以纯 gap 24px。calc((100% - 24px) / 4)
    with ui.card().props("flat bordered").style(
        f"flex: 0 0 calc((100% - 24px) / 4); min-width: 200px; "
        f"padding: 8px 10px; cursor: pointer; "
        f"border-color: {border}; background: {bg};"
    ).on("click", lambda _e=None, pid=p.pid: on_pick(pid)):
        with ui.row().classes("items-center no-wrap w-full").style("gap: 8px;"):
            # logo / 首字母占位
            logo = (p.logo or "").strip()
            if logo:
                ui.html(
                    f'<img src="/static/model_logos/{logo}" '
                    f'style="width:28px;height:28px;border-radius:6px;'
                    f'object-fit:contain;background:#f9fafb;flex:0 0 auto;" />'
                )
            else:
                first = p.display_name[:1] if p.display_name else "?"
                ui.html(
                    f'<div style="width:28px;height:28px;border-radius:6px;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'background:{color};color:white;font-weight:600;'
                    f'font-size:12px;flex:0 0 auto;">{first}</div>'
                )
            ui.label(p.display_name).style(
                "font-weight: 600; font-size: 13px; flex: 1 1 auto; "
                "white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
            )
            if enabled:
                ui.icon("check_circle", size="16px").classes("text-positive").tooltip(
                    f"已启用 · {n} 个模型"
                )
            elif configured:
                ui.icon("settings", size="14px").classes("text-grey-6").tooltip(
                    "已填密钥但未启用"
                )
        # tag 行(只取前 2 个,不挤主行)
        if p.tags:
            with ui.row().classes("items-center q-mt-xs").style(
                "gap: 4px; flex-wrap: wrap;"
            ):
                for t in p.tags[:2]:
                    ui.label(t).classes("text-caption").style(
                        "background: #f3f4f6; border-radius: 4px; "
                        "padding: 0 5px; font-size: 10px; color: #4b5563;"
                    )


# ---------------------------------------------------------------------------
# YAML 读写
# ---------------------------------------------------------------------------

@dataclass
class _PlatformState:
    """UI / 内存里单个服务商的状态。不会全部写盘——只有 enabled+validated+有模型的才会。"""
    pid: str
    meta: ProviderMeta
    # 连接参数
    platform_type: str = "openai"
    api_base_url: str = ""
    api_key: str = ""
    api_proxy: str = ""
    api_concurrencies: int = 5
    auto_detect_model: bool = False
    # 状态
    enabled: bool = False
    validated: bool = False
    validate_message: str = ""
    # 已知模型清单： model_id -> {"group": ..., "enabled": bool}
    models: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def has_enabled_model(self) -> bool:
        return any(m.get("enabled") for m in self.models.values())

    def to_yaml_item(self) -> Dict[str, Any]:
        """组装成 yaml MODEL_PLATFORMS / 草稿里的一项。"""
        item: Dict[str, Any] = {
            "platform_name": self.pid,
            "platform_type": self.platform_type or "openai",
            "api_base_url": self.api_base_url,
            "api_key": self.api_key or "EMPTY",
            "api_proxy": self.api_proxy,
            "api_concurrencies": int(self.api_concurrencies or 5),
            "auto_detect_model": bool(self.auto_detect_model),
            # 74 题:显式持久化 enabled,关闭后草稿落盘 + 重启读回为 False
            "enabled": bool(self.enabled),
        }
        # 自定义服务商：把 UI 层 meta 里的 display_name / icon / color 作为扩展
        # 字段写入，方便刷新页面后保留用户选择。内建 catalog 里的服务商不写这些，
        # 避免污染默认配置。
        if self.pid not in _PROVIDER_BY_ID:
            if self.meta.display_name and self.meta.display_name != self.pid:
                item["display_name"] = self.meta.display_name
            if self.meta.icon:
                item["icon"] = self.meta.icon
            if self.meta.color and self.meta.color != "#6b7280":
                item["color"] = self.meta.color
            if self.meta.tags:
                item["tags"] = list(self.meta.tags)
        group_lists: Dict[str, List[str]] = {g: [] for g in _GROUP_KEYS}
        for mid, meta in self.models.items():
            if not meta.get("enabled"):
                continue
            g = str(meta.get("group") or "llm_models")
            if g not in group_lists:
                g = "llm_models"
            group_lists[g].append(mid)
        for g in _GROUP_KEYS:
            item[g] = group_lists[g]
        return item


def _build_initial_states() -> List[_PlatformState]:
    """从 yaml 把已有 MODEL_PLATFORMS 合并进目录;用户之前填过的 api_key / models 保留。

    数据来源优先级(后者覆盖前者):
        1. catalog 默认值(meta)
        2. ``model_settings.yaml`` MODEL_PLATFORMS(已点"全部保存"的正式数据)
        3. ``model_settings.draft.yaml`` DRAFTS(用户在编辑但还没点保存的草稿)

    草稿覆盖正式 → 用户改了 api_key 还没保存就切走,回来看到的是改过的 key。
    点"全部保存"后 ``_save_all`` 会清掉对应 pid 的草稿,避免下次启动重叠。
    """
    load = yaml_store.load_yaml(_FILE)
    raw = load.doc.get("MODEL_PLATFORMS") if isinstance(load.doc, dict) else None
    if not isinstance(raw, list):
        raw = []

    saved_by_pid: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("platform_name") or "").strip()
        if pid:
            saved_by_pid[pid] = item

    # 草稿(用户在改但还没点"全部保存"的)— 草稿优先级高于正式 yaml
    draft_by_pid = _load_draft_states()
    if draft_by_pid:
        logger.info(
            "[model_config] 加载草稿: %d 个 provider 的未保存修改 → %s",
            len(draft_by_pid), list(draft_by_pid.keys()),
        )

    def _merged(pid: str) -> Optional[Dict[str, Any]]:
        """同 pid: 草稿覆盖正式;只有正式无草稿 → 正式;只有草稿 → 草稿。"""
        if pid in draft_by_pid:
            base = dict(saved_by_pid.get(pid) or {})
            base.update(draft_by_pid[pid])
            return base
        return saved_by_pid.get(pid)

    states: List[_PlatformState] = []
    # 先把 catalog 里的按原顺序生成
    seen: set = set()
    for meta in PROVIDER_CATALOG:
        seen.add(meta.pid)
        s = _make_state_from_saved(meta, _merged(meta.pid))
        states.append(s)

    # 用户在 yaml 里自定义的(catalog 没有的)追加到末尾
    # 草稿里也可能有自定义 pid(用户新加但没保存),都要恢复
    custom_pids = set(saved_by_pid.keys()) | set(draft_by_pid.keys())
    for pid in custom_pids:
        if pid in seen:
            continue
        item = _merged(pid) or {}
        saved_tags = item.get("tags")
        if isinstance(saved_tags, (list, tuple)) and saved_tags:
            tags_tuple: Tuple[str, ...] = tuple(str(t) for t in saved_tags if t)
        else:
            tags_tuple = ("自定义",)
        meta = ProviderMeta(
            pid=pid,
            display_name=str(item.get("display_name") or pid),
            platform_type=str(item.get("platform_type") or "openai"),
            default_api_base=str(item.get("api_base_url") or ""),
            logo="",
            color=str(item.get("color") or "#6366F1"),
            tags=tags_tuple,
            icon=str(item.get("icon") or "auto_awesome"),
        )
        states.append(_make_state_from_saved(meta, item))

    # 把"已启用且有模型"的平台稳定排序到前面，保证刷新后最关心的条目始终在顶部。
    # enumerate 的索引作为 tie-breaker 维持 catalog 本来的相对顺序（stable sort）。
    states = [
        s for _, s in sorted(
            enumerate(states),
            key=lambda pair: (0 if pair[1].has_enabled_model() else 1, pair[0]),
        )
    ]
    return states


def _make_state_from_saved(meta: ProviderMeta, item: Optional[Dict[str, Any]]) -> _PlatformState:
    if item is None:
        return _PlatformState(
            pid=meta.pid,
            meta=meta,
            platform_type=meta.platform_type,
            api_base_url=meta.default_api_base,
        )

    s = _PlatformState(
        pid=meta.pid,
        meta=meta,
        platform_type=str(item.get("platform_type") or meta.platform_type),
        api_base_url=str(item.get("api_base_url") or meta.default_api_base),
        api_key=str(item.get("api_key") or ""),
        api_proxy=str(item.get("api_proxy") or ""),
        api_concurrencies=int(item.get("api_concurrencies") or 5),
        auto_detect_model=bool(item.get("auto_detect_model") or False),
    )
    if s.api_key.upper() == "EMPTY":
        s.api_key = ""
    # 74 题:优先用 yaml/draft 中显式记录的 enabled;只有该字段缺失(老 yaml)
    # 才按"已保存即启用"兜底为 True。这样禁用后保存重启能记住关闭状态。
    if "enabled" in item:
        s.enabled = bool(item.get("enabled"))
    else:
        s.enabled = True
    # 把已保存的 *_models 当作已"启用"的模型注入 inventory
    for g in _GROUP_KEYS:
        for mid in (item.get(g) or []):
            mid = str(mid).strip()
            if not mid:
                continue
            s.models[mid] = {"group": g, "enabled": True}
    # yaml 里已有 → 我们标记 validated=True，用户不必再点一次
    if s.models:
        s.validated = True
        s.validate_message = "已加载历史配置"
    return s


# =============================================================================
# 草稿(draft)落库 — 解决"用户填了 api_key 但没点'获取模型'就切走,数据丢失"
# -----------------------------------------------------------------------------
# 设计:
#   * 用户每改一个字段(api_key / base_url / proxy / type / 启用开关 / 模型勾选)
#     都会调 ``_save_draft_state(s)`` 写到 ``model_settings.draft.yaml``
#   * 文件结构: ``DRAFTS: {pid: state_dict}`` — 多个 provider 草稿并存
#   * ``_build_initial_states`` 启动时合并 base_yaml + draft(草稿覆盖正式)
#   * 点"全部保存"成功后,清掉对应 pid 的草稿(已经入正式 yaml 了)
#   * 进程重启 / 页面切换 / dashboard 重渲染都不会丢数据 — yaml 是唯一真源
# =============================================================================
_DRAFT_FILE = "model_settings.draft.yaml"


def _load_draft_states() -> Dict[str, Dict[str, Any]]:
    """读 draft yaml,返回 ``{pid: yaml_item_dict}``。文件不存在或解析错返空 dict。"""
    try:
        load = yaml_store.load_yaml(_DRAFT_FILE)
        if isinstance(load.doc, dict):
            drafts = load.doc.get("DRAFTS", {}) or {}
            return drafts if isinstance(drafts, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.debug("[model_config] load draft failed: %r", e)
    return {}


def _save_draft_state(state: "_PlatformState") -> None:
    """把单个 state 的当前值写入草稿 yaml。

    其他 provider 的草稿不动,确保多个厂商并行编辑互不干扰。
    """
    if not state or not state.pid:
        return
    try:
        load = yaml_store.load_yaml(_DRAFT_FILE)
        doc = load.doc if isinstance(load.doc, dict) else {}
        drafts = doc.get("DRAFTS")
        if not isinstance(drafts, dict):
            drafts = {}
        # 用与 _save_all 相同的字段集 dump,保证读回来能还原
        drafts[state.pid] = state.to_yaml_item()
        doc["DRAFTS"] = drafts
        from chayuan.pydantic_settings_file import import_yaml
        y = import_yaml()
        yaml_store._atomic_write(load.path, lambda f: y.dump(doc, f))  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        logger.debug("[model_config] save draft(%s) failed: %r", state.pid, e)


# Debounce 落草稿:同一 pid 多次连续调用合并成 1 次写盘
# 100+ 模型逐个勾选时,旧实现每次都写 yaml(IO 100+ 次);
# 新实现取消上次 pending timer + 延迟 300ms 落盘 — 实际只写最后 1 次状态
_DRAFT_FLUSH_TIMERS: Dict[str, Any] = {}


def _save_draft_state_debounced(
    state: "_PlatformState", *, delay: float = 0.3,
) -> None:
    """Debounce 版的 _save_draft_state — 连续调用合并写盘。

    在 NiceGUI 上下文里用 ui.timer;非 NiceGUI(纯 CLI/test)退回同步。
    """
    if not state or not state.pid:
        return
    pid = state.pid
    # 取消上次 pending 的 timer(若有)
    old = _DRAFT_FLUSH_TIMERS.pop(pid, None)
    if old is not None:
        try:
            old.cancel()
        except Exception:  # noqa: BLE001
            pass
    try:
        from nicegui import ui as _ng_ui
        # 用 dataclass 的内存对象做 closure,timer 触发时读最新 state 字段
        timer = _ng_ui.timer(
            delay,
            lambda: (_DRAFT_FLUSH_TIMERS.pop(pid, None), _save_draft_state(state))[1],
            once=True,
        )
        _DRAFT_FLUSH_TIMERS[pid] = timer
    except Exception:  # noqa: BLE001
        # 非 NiceGUI 上下文 — 直接同步写
        _save_draft_state(state)


def _flush_pending_draft(state: "_PlatformState") -> None:
    """67 题:**同步**强制落盘当前 state 草稿,取消任何 pending debounce timer。

    使用场景:
    * dialog X 关闭前 — 防止用户关闭开关后 300ms debounce 内 timer 被 dialog
      销毁,草稿没写盘,重启后看到的还是旧值
    * dialog 💾 保存前 — 确保保存逻辑读到最新 state
    """
    if not state or not state.pid:
        return
    pid = state.pid
    # 取消 pending timer(避免重复写盘)
    old = _DRAFT_FLUSH_TIMERS.pop(pid, None)
    if old is not None:
        try:
            old.cancel()
        except Exception:  # noqa: BLE001
            pass
    # 立即同步落盘
    _save_draft_state(state)


def _flush_all_pending_drafts(states: List["_PlatformState"]) -> None:
    """同步落盘**所有** pid 的 pending 草稿。dialog 关闭时调,兜底防丢。

    Args:
        states: 内存中的 ``_PlatformState`` 列表;按 pid 找出 pending 的逐个落盘。
    """
    pids_to_flush = list(_DRAFT_FLUSH_TIMERS.keys())
    if not pids_to_flush:
        return
    pid_to_state = {s.pid: s for s in states if s and s.pid}
    for pid in pids_to_flush:
        old = _DRAFT_FLUSH_TIMERS.pop(pid, None)
        if old is not None:
            try:
                old.cancel()
            except Exception:  # noqa: BLE001
                pass
        s = pid_to_state.get(pid)
        if s is not None:
            try:
                _save_draft_state(s)
            except Exception:  # noqa: BLE001
                logger.debug("[model_config] flush draft %s failed", pid)


def _clear_draft_state(pid: str) -> None:
    """从草稿里删掉指定 pid(对应正式保存成功)。"""
    if not pid:
        return
    try:
        load = yaml_store.load_yaml(_DRAFT_FILE)
        if not isinstance(load.doc, dict):
            return
        drafts = load.doc.get("DRAFTS", {}) or {}
        if pid in drafts:
            del drafts[pid]
            load.doc["DRAFTS"] = drafts
            from chayuan.pydantic_settings_file import import_yaml
            y = import_yaml()
            yaml_store._atomic_write(load.path, lambda f: y.dump(load.doc, f))  # noqa: SLF001
    except Exception as e:  # noqa: BLE001
        logger.debug("[model_config] clear draft(%s) failed: %r", pid, e)


def _save_all(states: List[_PlatformState]) -> Tuple[int, int]:
    """把"启用"的服务商写回 YAML;返回 (写入条数, 跳过条数)。

    67 题修订:
      * 过滤条件放宽到 ``s.enabled``(去掉"至少 1 个启用模型"的硬要求)
      * 启用但 inventory 空 → 用 ``meta.default_models`` 自动 seed catalog
        默认主流模型(deepseek/openai 等),让用户"填 api_key + 启用 + 保存"
        三步即可在 ④ 默认模型 tab 看到候选模型,不必先点"验证并获取模型"

    其他不变:
      * 已写入正式 yaml 的 pid,其草稿条目会被清掉(避免下次启动重叠)。
      * 未启用但有草稿的 pid(如用户填了 key 但没启用),草稿仍保留 —
        否则切走再回来 api_key 又丢了。
    """
    # 67 题:启用但 inventory 空 → 用 catalog 默认模型自动 seed
    # 这是 _save_all 内部行为,不影响 hero strip / dialog 显示(它们看 s.models)
    for s in states:
        if not s.enabled:
            continue
        if s.has_enabled_model():
            continue  # 已经有用户选好的模型,不动
        defaults = getattr(s.meta, "default_models", None) or {}
        if not defaults:
            continue  # 该厂商 catalog 也没默认,跳过 — 保存空模型清单
        for group_key, mids in defaults.items():
            if group_key not in _GROUP_KEYS:
                continue
            for mid in (mids or []):
                mid = str(mid).strip()
                if not mid:
                    continue
                s.models[mid] = {"group": group_key, "enabled": True}
        if s.models:
            logger.info(
                "[model_config] %s 启用但 inventory 空 → 已用 catalog 默认 %d 个模型 seed",
                s.pid, sum(len(v) for v in defaults.values()),
            )

    keep = [s for s in states if s.enabled]
    yaml_list = [s.to_yaml_item() for s in keep]

    load = yaml_store.load_yaml(_FILE)
    doc = load.doc if isinstance(load.doc, dict) else {}
    doc["MODEL_PLATFORMS"] = yaml_list

    from chayuan.pydantic_settings_file import import_yaml

    yaml_store._backup(load.path)  # noqa: SLF001 - intentional reuse
    y = import_yaml()
    yaml_store._atomic_write(load.path, lambda f: y.dump(doc, f))  # noqa: SLF001
    # 同步到配置中心（跨副本 + 历史审计）；model_settings.yaml 已在 _YAML_TO_NAMESPACE 里登记
    yaml_store.mirror_namespace_to_db("model_settings.yaml", doc)

    # 71 题(根治):同步写 model_platform DB 表。
    # 历史 bug:_merge_platforms 中"DB 行整体覆盖 yaml" — 如果 DB 表里有同名旧
    # 记录(historic admin UI 写的),会覆盖 yaml 刚保存的新值;chayuan-client 调
    # /v1/models 通过 get_config_models → _resolved_platforms → _merge_platforms,
    # 拿到的是被 DB 覆盖过的旧数据 → 看不到刚配的 deepseek。
    # 解法:对每个 keep 调 upsert_platform(enabled=True);对未在 keep 中但
    # 内存里 enabled=False 的(用户禁用过但 yaml 已不写出),DB 里若有则
    # update_platform(enabled=False),保持 yaml/DB 一致。
    # upsert_platform 内部已 bump_platform_version,所以 cache 立即失效。
    try:
        from chayuan.server.db.repository.model_platform_repository import (
            upsert_platform, update_platform, get_platform, bump_platform_version,
        )
        keep_pids = {s.pid for s in keep}
        # 1) 启用的 → upsert 到 DB
        for s in keep:
            try:
                yaml_item = s.to_yaml_item()
                # to_yaml_item 不写 enabled 字段(yaml 默认 enabled=True);DB 必须显式
                fields = {k: v for k, v in yaml_item.items() if k != "platform_name"}
                fields["enabled"] = True
                upsert_platform(platform_name=s.pid, fields=fields)
            except Exception as e:  # noqa: BLE001
                logger.debug("[model_config] upsert DB %s failed: %r", s.pid, e)
        # 2) 不在 keep 中的 state(被禁用 / 删除)— 若 DB 还有该平台,标 enabled=False
        for s in states:
            if s.pid in keep_pids:
                continue
            try:
                if get_platform(s.pid):
                    update_platform(s.pid, {"enabled": False})
            except Exception as e:  # noqa: BLE001
                logger.debug("[model_config] disable DB %s failed: %r", s.pid, e)
        # 兜底再 bump 一次(upsert 内部已 bump,这里只是双保险)
        bump_platform_version()
    except Exception as e:  # noqa: BLE001
        # 整个 DB 模块不可用 — 至少 yaml 已落,不影响主路径
        logger.debug("[model_config] sync model_platform DB skipped: %r", e)

    # 已正式保存的 pid 从草稿里清掉(避免下次启动 base_yaml + draft 重叠时草稿覆盖正式)
    for s in keep:
        _clear_draft_state(s.pid)

    # 通知 4 个面板刷新(默认模型选择器/框架卡片/hero 顶卡/hero 弹出列表)
    # —— **异步 schedule** —— 让 _save_all 立即返回,UI 立即给用户反馈"已保存",
    # 4 个 refresh 在 200ms 后异步执行 — 用户感知"嗖一下保存完成",不再卡顿。
    # 如 ui.timer 不可用(非 NiceGUI 上下文),退回同步调用。
    def _do_cascade_refresh() -> None:
        try:
            from chayuan.server.config_panel.runtime_framework_panel import (
                trigger_capability_defaults_refresh,
                trigger_framework_cards_refresh,
            )
            trigger_capability_defaults_refresh()
            trigger_framework_cards_refresh()
        except Exception as e:  # noqa: BLE001
            logger.debug("[model_config] cascade refresh (runtime_framework) failed: %r", e)
        try:
            from chayuan.server.config_panel.provider_hero_strip import (
                trigger_hero_strip_refresh,
                trigger_more_dialog_refresh,
            )
            trigger_hero_strip_refresh()
            trigger_more_dialog_refresh()
        except Exception as e:  # noqa: BLE001
            logger.debug("[model_config] cascade refresh (hero_strip) failed: %r", e)
        # 64.2 题:云厂商分组网格刷新(本模块内部 list — 不在 hero_strip 里)
        try:
            trigger_cloud_grid_refresh()
        except Exception as e:  # noqa: BLE001
            logger.debug("[model_config] cascade refresh (cloud_grid) failed: %r", e)

    try:
        from nicegui import ui as _ng_ui
        _ng_ui.timer(0.2, _do_cascade_refresh, once=True)
    except Exception:  # noqa: BLE001
        # 非 NiceGUI 上下文(如纯 CLI 调用 _save_all)— 退回同步
        _do_cascade_refresh()

    return len(keep), len(states) - len(keep)


# ---------------------------------------------------------------------------
# 拉取 / 验证
# ---------------------------------------------------------------------------

def _httpx_client(proxy: str, timeout: float):
    import httpx  # 主依赖
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def fetch_models(s: _PlatformState, timeout: float = 8.0) -> Dict[str, Any]:
    """``GET {api_base_url}/models``，兼容 OpenAI / Ollama 两种响应结构。"""
    base = (s.api_base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "message": "api_base_url 为空，无法拉模型清单", "models": []}

    try:
        import httpx  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"未安装 httpx：{e}", "models": []}

    headers = {"Authorization": f"Bearer {s.api_key or 'EMPTY'}"}
    url = f"{base}/models"

    try:
        with _httpx_client(s.api_proxy, timeout) as c:
            resp = c.get(url, headers=headers)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}", "models": []}

    if resp.status_code != 200:
        return {
            "ok": False,
            "message": f"HTTP {resp.status_code}：{(resp.text or '').strip()[:200]}",
            "models": [],
        }

    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"响应非 JSON：{e}", "models": []}

    raw = data.get("data") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raw = data.get("models") if isinstance(data, dict) else []

    out: List[Dict[str, str]] = []
    seen: set = set()
    for item in raw or []:
        mid = str(item.get("id") or item.get("name") or "").strip() \
            if isinstance(item, dict) else str(item).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append({"id": mid, "guessed_group": _guess_group(mid)})

    return {"ok": True, "message": f"成功，共 {len(out)} 个模型", "models": out}


def _guess_group(model_id: str) -> str:
    m = model_id.lower()
    if any(k in m for k in ("embed", "bge", "m3e", "text-embedding", "gte-")):
        return "embed_models"
    if "rerank" in m:
        return "rerank_models"
    if any(k in m for k in ("dall-e", "stable-diffusion", "sd-", "flux",
                             "text2image", "image-gen", "wan2.", "kolors")):
        return "text2image_models"
    if any(k in m for k in ("vision", "-vl", "vl-", "image2text", "gpt-4o-vision")):
        return "image2text_models"
    if any(k in m for k in ("whisper", "asr", "speech-to-text", "paraformer")):
        return "speech2text_models"
    if any(k in m for k in ("tts", "text-to-speech", "cosyvoice")):
        return "text2speech_models"
    return "llm_models"


# ---------------------------------------------------------------------------
# 下拉列表图标映射（供 ui.select 的 option / selected-item slot 使用）
# ---------------------------------------------------------------------------

# platform_type -> Material icon 名称；未命中走 fallback
_PLATFORM_TYPE_ICONS: Dict[str, str] = {
    "openai":     "bolt",           # 万金油 OpenAI 兼容
    "ollama":     "dns",            # 本地服务器
    "xinference": "flash_on",       # 本地推理加速
    "zhipu":      "psychology",     # 智谱 ChatGLM
    "qianfan":    "flight",         # 百度千帆
    "minimax":    "smart_toy",
    "claude":     "auto_awesome",   # Anthropic
    "azure":      "cloud",
    "custom":     "tune",
}
# 对应的 Quasar 颜色名（可写成 #hex，但用 palette 名让主题更一致）
_PLATFORM_TYPE_COLORS: Dict[str, str] = {
    "openai":     "teal",
    "ollama":     "grey-8",
    "xinference": "blue-7",
    "zhipu":      "indigo-6",
    "qianfan":    "blue-9",
    "minimax":    "deep-orange-6",
    "claude":     "orange-8",
    "azure":      "light-blue-7",
    "custom":     "grey-7",
}

# 分类标签 -> icon / color
_TAG_ICONS: Dict[str, str] = {
    "自定义": "tune",
    "国内":   "flag",
    "国外":   "public",
    "本地":   "computer",
    "聚合":   "hub",
    "推荐":   "star",
}
_TAG_COLORS: Dict[str, str] = {
    "自定义": "grey-7",
    "国内":   "red-6",
    "国外":   "blue-7",
    "本地":   "green-7",
    "聚合":   "purple-6",
    "推荐":   "amber-7",
}


def _icon_for_platform_type(pt: str) -> str:
    """返回某 platform_type 默认 Material icon 名（查表 + 回退）。"""
    return _PLATFORM_TYPE_ICONS.get(pt, "settings_suggest")


def _icon_for_tag(tag: str) -> str:
    return _TAG_ICONS.get(tag, "label")


# 历史版本曾借助 ``add_slot`` 向 ``ui.select`` 注入 Vue 模板做图标化选项，
# 但这会让 Quasar 在客户端侧编译失败、进而整页 JS 崩溃（对话框无法弹出）。
# 当前采用稳健方案：
#   - 让 ui.select 保持纯文本选项（不再 add_slot）；
#   - 选中值旁边单独渲染一个 ``ui.icon`` 作为视觉提示（由调用方接入）。
# 这个包装函数仅作为语义化快捷方式，给 ui.row 里的选中项图标用。
def _render_icon_badge(
    ui_mod: Any,
    icon_name: str,
    color: str = "grey-7",
    size: str = "20px",
) -> Any:
    return ui_mod.icon(icon_name).props(f"color={color} size={size}")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def render_model_settings_page(
    ui,
    *,
    mark_restart_needed: Optional[Callable[[], None]] = None,
    _prefetched_states: Optional[List["_PlatformState"]] = None,
    _prefetched_healths: Optional[List[Any]] = None,
    _prefetched_capability_grouped: Optional[Dict[str, Any]] = None,
    _prefetched_capability_defaults: Optional[Dict[str, str]] = None,
    _mode: str = "full",
) -> None:
    """渲染整块"服务商目录 + 连接参数 + 模型清单"——紧凑双栏。

    父级 dashboard 已经不给这个页面铺标题了，所以第一个元素就是搜索 / 操作工具条。

    Args:
        _prefetched_states: 38 题 P1 — 预取的 ``_build_initial_states`` 结果。
        _prefetched_healths: 42 题 P0.B — 预取的 ``probe_all_frameworks`` 结果。
        _prefetched_capability_grouped: 48 题 — 预取的 ``_capability_grouped`` 结果,
            避免主线程 yaml.load + 本地索引扫描(100-300ms 阻塞)。
        _prefetched_capability_defaults: 48 题 — 预取的 ``_load_capability_defaults``。
        _mode: 57 题 P2 — ``"full"`` 渲染全部 4 行 + 双栏(legacy);
            ``"providers_only"`` 仅渲染 hero strip(本地 + 云) + dialog,
            供新版 ``model_settings`` tab 调用,不渲染 runtime/capability/marketplace 三行。
    """
    _providers_only = (_mode == "providers_only")
    if _prefetched_states is not None:
        states: List[_PlatformState] = _prefetched_states
    else:
        states = _build_initial_states()

    # ---- 顶部两行：模型框架 + 9 类默认模型（v6 增量；用户反馈要求） -------
    # 设计：
    #   * 第一行 = 模型框架卡片（11 个适配器健康状态 + 一键安装入口）
    #   * 第二行 = 9 类 capability 默认模型选择器
    # 与下面"原有的服务商目录 + 双栏"完全独立，渲染失败不影响主体。
    try:
        from chayuan.server.config_panel.runtime_framework_panel import (
            render_runtime_framework_row,
            render_capability_defaults_row,
        )
        # 顶部四段(自上而下):
        #   * 模型框架卡片(11 个适配器健康状态)
        #   * 模型厂商(配 api_key,加载模型)
        #   * 9 类默认模型选择器
        #   * 模型广场(本地+catalog 合并 + 下载)
        # 任何一段渲染失败不阻塞下面"原有的服务商目录 + 双栏"。
        #
        # **不再渲染顶部"镜像源"段** — 已经分散到每个 framework 的安装弹窗里
        # (chip 行 + mirror_pool 自动探活排序),顶部统一切换镜像源已无意义。
        # mirror_source_panel.py 文件保留(以防有其它入口),只是在 model
        # 配置页里不再调它。
        #
        # 渲染顺序符合 "操作流" 直觉:
        #   1. 运行时与服务(本地框架健康状态 + 启停)
        #   2. 模型厂商(配 api_key,加载模型)
        #   3. 默认模型选择(从已加载的模型里挑各 capability 默认)
        #   4. 模型广场(本地 + catalog 合并下载)
        # 42 题 P0.B:把 wrapper 在 thread 预取的 healths 传给 render_runtime_framework_row
        # 跳过其内部同步 probe_all_frameworks (cache 空时会卡 ~2s)
        # 57 题 P2:_mode="providers_only" 时跳过 runtime/capability/marketplace 三行,
        # 只留 hero strip(本地 + 云)+ dialog,新版 ``model_settings`` 4 tab 各自负责
        if not _providers_only:
            render_runtime_framework_row(ui, _prefetched_healths=_prefetched_healths)
        # 56-1 题:本地模型行 — 在"模型厂商"之上,与"运行时与服务"区分:
        #   - 运行时与服务 = chayuan 自带的 framework runner(可启停 daemon)
        #   - 本地模型     = 用户自己在本地部署的 OpenAI 兼容服务器(配 url + api_key)
        # 同样的卡片 + 配置 dialog 复用 _open_provider_config_dialog,行的 kind="local"
        local_hero_anchor = ui.column().classes("w-full q-mb-sm")
        # 注意: 模型厂商占位在本地模型之下 — 渲染顺序: 运行时 → 本地模型 → 厂商 → 默认 → 广场
        nonlocal_hero_anchor = ui.column().classes("w-full q-mb-sm")
        if not _providers_only:
            # 48 题:capability defaults row 也吃 prefetched
            render_capability_defaults_row(
                ui,
                _prefetched_grouped=_prefetched_capability_grouped,
                _prefetched_defaults=_prefetched_capability_defaults,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("render runtime/defaults/marketplace rows failed: %s", e)
        ui.label(
            f"框架/默认模型/广场行渲染失败:{type(e).__name__}: {e}"
        ).classes("text-negative text-sm")
        local_hero_anchor = ui.column().classes("w-full q-mb-sm")
        nonlocal_hero_anchor = ui.column().classes("w-full q-mb-sm")

    # 注入一次性的样式（行 hover / active / 阴影等），集中管理避免到处拼字符串
    ui.add_head_html(
        """
        <style>
        .chayuan-provider-row {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 8px;
            border-radius: 6px;
            cursor: pointer;
            border: 1px solid transparent;
            transition: background 120ms ease, border-color 120ms ease;
        }
        .chayuan-provider-row:hover {
            background: #f3f4f6;
        }
        .chayuan-provider-row.is-active {
            background: #e7f1ff;
            border-color: #bfdbfe;
        }
        .chayuan-provider-row.is-active:hover {
            background: #dbeafe;
        }
        /* 模型行的删除按钮 hover 态变红，明确"危险操作"视觉 */
        .chayuan-model-del-btn:hover {
            background: rgba(239, 68, 68, 0.1) !important;
        }
        .chayuan-model-del-btn:hover .q-icon,
        .chayuan-model-del-btn:hover i {
            color: #dc2626 !important;
        }
        </style>
        """
    )

    # 当前选中的 pid
    selection: Dict[str, Any] = {
        "pid": next((s.pid for s in states if s.enabled), states[0].pid if states else ""),
        "search": "",
        "filter_tag": "全部",
    }

    def _current() -> Optional[_PlatformState]:
        for s in states:
            if s.pid == selection["pid"]:
                return s
        return None

    def _mark_changed() -> None:
        if mark_restart_needed is not None:
            try:
                mark_restart_needed()
            except Exception:  # noqa: BLE001
                pass

    # -----------------------------------------------------------------------
    # 默认模型卡片（旧版 LLM + Embedding 双 select）：
    # 已被顶部 ``render_capability_defaults_row`` 的 9 类 capability 覆盖；
    # 这里保留一个**隐藏**容器以便老调用方（``_render_defaults`` 在文件后面
    # 还有引用）不会 NameError，但实际不会渲染任何内容。
    # -----------------------------------------------------------------------
    defaults_container = ui.row().classes("w-full").style("display: none;")

    # 模型厂商横向卡片(8 + 更多)的容器 — 已经在 runtime_framework_panel 之后、
    # render_capability_defaults_row 之前预留好位置(nonlocal_hero_anchor),
    # 此处只是把它别名到 hero_strip_container 让下方填充逻辑保持不变。
    # 这样渲染顺序就是: 运行时 → 厂商 → 默认模型 → 广场 ↓
    hero_strip_container = nonlocal_hero_anchor

    # -----------------------------------------------------------------------
    # 主体双栏 —— P3 重构后**隐藏**(display: none)
    # 内容已 100% 移入 _open_provider_config_dialog 弹窗;hero 卡片点击触发。
    # 不删代码是为了保持 _render_left / _render_right 等闭包变量不变,
    # 避免改动整文件 1900 行。display:none 的容器在 NiceGUI 里仍正常构造,
    # 渲染开销可忽略。
    # -----------------------------------------------------------------------
    main_row = ui.row().classes("w-full no-wrap items-stretch").style(
        "gap: 12px; min-height: 70vh; display: none;"
    )
    with main_row:
        # 左栏容器：搜索 + 筛选 + 添加按钮 + 目录列表 一体化
        left_col = ui.column().classes("col-shrink").style(
            "width: 300px; min-width: 300px; max-height: 78vh; "
            "padding: 8px; background: #ffffff; "
            "border: 1px solid #e5e7eb; border-radius: 8px; "
            "display: flex; flex-direction: column; gap: 6px;"
        )
        with left_col:
            # 第一行：搜索框 + 添加按钮（按钮紧挨筛选上方，避免与 toggle 挤在同一行）
            with ui.row().classes("items-center w-full no-wrap").style(
                "gap: 6px; flex: 0 0 auto;"
            ):
                search_input = ui.input(
                    placeholder="搜索服务商...",
                ).props("dense outlined clearable").classes("col-grow").style(
                    "min-width: 0;"
                )
                ui.button(
                    "添加",
                    icon="add",
                    on_click=lambda: _open_add_provider_dialog(),
                ).props("dense unelevated color=primary size=sm").tooltip(
                    "添加自定义服务商"
                ).style("flex: 0 0 auto;")
            # 第二行：筛选标签（全宽，自动均分）
            filter_tabs = ui.toggle(
                ["全部", "推荐", "国内", "国外", "本地", "聚合"],
                value="全部",
            ).props("dense no-caps spread").classes("w-full").style(
                "flex: 0 0 auto; font-size: 12px;"
            )
            # 列表自身滚动；外层不再 overflow 以免双滚动条
            list_scroll = ui.column().classes("w-full").style(
                "flex: 1 1 auto; overflow: auto; min-height: 0; gap: 0;"
            )
        right_col = ui.column().classes("col-grow").style(
            "min-width: 0; max-height: 78vh; overflow: auto; "
            "padding: 2px 4px;"
        )

    def _do_save_all() -> None:
        try:
            kept, skipped = _save_all(states)
            # 默认模型字段也要写入
            _save_defaults()
        except Exception as e:  # noqa: BLE001
            logger.exception("save model platforms failed")
            ui.notify(f"保存失败：{type(e).__name__}: {e}", type="negative")
            return
        _mark_changed()
        ui.notify(
            f"已保存 {kept} 个服务商；跳过 {skipped} 个（未启用 / 未验证 / 无可用模型）",
            type="positive",
            multi_line=True,
        )

    # -----------------------------------------------------------------------
    # 添加自定义服务商
    # -----------------------------------------------------------------------

    def _slug(text: str) -> str:
        """从"显示名"派生一个合法 pid：小写 ASCII + 数字 + 连字符。"""
        import re
        s = (text or "").strip().lower()
        s = re.sub(r"[^a-z0-9\-_]+", "-", s).strip("-_")
        return s or "custom"

    def _next_available_pid(base: str) -> str:
        existing = {s.pid for s in states}
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    _COLOR_PRESETS: List[Tuple[str, str]] = [
        ("#6366F1", "靛蓝（默认）"),
        ("#2563EB", "蓝色"),
        ("#10A37F", "绿色"),
        ("#F59E0B", "琥珀"),
        ("#E0481F", "红橙"),
        ("#111111", "黑色"),
        ("#6B7280", "灰色"),
        ("#8B5CF6", "紫色"),
    ]

    def _open_add_provider_dialog() -> None:
        with ui.dialog() as dialog, ui.card().style("min-width: 520px; max-width: 560px;"):
            ui.label("添加自定义服务商").classes("text-h6 q-mb-xs")
            ui.label(
                "提供一个 OpenAI 兼容的 API 端点即可接入。保存前请先连接并启用至少一个模型。"
            ).classes("text-caption text-grey-6 q-mb-sm")

            name_input = ui.input(
                label="显示名称（必填）",
                placeholder="如：本地推理网关",
            ).props("dense outlined autofocus").classes("w-full")

            pid_input = ui.input(
                label="platform_name (pid)",
                placeholder="留空则从显示名自动生成",
            ).props("dense outlined").classes("w-full")
            pid_input.tooltip(
                "写入 model_settings.yaml 的 platform_name 字段；"
                "仅允许小写字母 / 数字 / - / _；同名则自动追加后缀。"
            )

            type_select = ui.select(
                PLATFORM_TYPE_CHOICES,
                value="openai",
                label="平台类型",
                with_input=True,
            ).props("dense outlined").classes("w-full")

            base_input = ui.input(
                label="API 地址（可选，事后也能改）",
                placeholder="https://api.example.com/v1",
            ).props("dense outlined").classes("w-full")

            key_input = ui.input(
                label="API Key（可选）",
                password=True,
                password_toggle_button=True,
            ).props("dense outlined").classes("w-full")

            tag_select = ui.select(
                ["自定义", "国内", "国外", "本地", "聚合"],
                value="自定义",
                label="分类标签",
            ).props("dense outlined").classes("w-full")

            # --- Logo 底色（无自定义 logo 时用于首字母头像） ---------------------
            ui.label("Logo 底色（可选）").classes("text-caption text-grey-7 q-mt-sm")

            color_map_dict = {k: v for k, v in _COLOR_PRESETS}
            color_select = ui.select(
                options=color_map_dict,
                value="#6366F1",
                label="底色",
            ).props("dense outlined").classes("w-full")

            preview_row = ui.row().classes("items-center q-mt-xs").style(
                "gap: 10px; padding: 6px 8px; "
                "background: #f8fafc; border-radius: 6px;"
            )

            def _refresh_preview() -> None:
                preview_row.clear()
                with preview_row:
                    color_val = str(color_select.value or "#6366F1")
                    preview_meta = ProviderMeta(
                        pid="__preview__",
                        display_name=(name_input.value or "自定义").strip() or "自定义",
                        platform_type=str(type_select.value or "openai"),
                        color=color_val,
                    )
                    _render_provider_avatar(preview_meta, size=40)
                    with ui.column().classes("q-gutter-none"):
                        ui.label(preview_meta.display_name).classes(
                            "text-body2"
                        ).style("font-weight: 600;")
                        ui.label(f"color: {color_val}").classes(
                            "text-caption text-grey-6"
                        )

            color_select.on_value_change(lambda _e: _refresh_preview())
            name_input.on_value_change(lambda _e: _refresh_preview())
            _refresh_preview()

            def _do_add() -> None:
                name = (name_input.value or "").strip()
                if not name:
                    ui.notify("请填写显示名称", type="warning")
                    return
                raw_pid = (pid_input.value or "").strip()
                pid = _slug(raw_pid) if raw_pid else _slug(name)
                pid = _next_available_pid(pid)

                color_val = str(color_select.value or "#6366F1") or "#6366F1"

                meta = ProviderMeta(
                    pid=pid,
                    display_name=name,
                    platform_type=str(type_select.value or "openai"),
                    default_api_base=(base_input.value or "").strip(),
                    logo="",
                    color=color_val,
                    tags=(str(tag_select.value or "自定义"),),
                )
                new_state = _PlatformState(
                    pid=pid,
                    meta=meta,
                    platform_type=meta.platform_type,
                    api_base_url=meta.default_api_base,
                    api_key=(key_input.value or "").strip(),
                    enabled=True,
                )
                states.insert(0, new_state)
                selection["pid"] = pid
                dialog.close()
                _render_left()
                _render_right()
                _render_defaults()
                ui.notify(
                    f"已添加服务商 {name}（pid={pid}）。请填写 API 地址 / Key 后点击「验证并获取模型」。",
                    type="positive",
                    multi_line=True,
                )

            with ui.row().classes("w-full justify-end q-mt-sm").style("gap: 6px;"):
                ui.button("取消", on_click=dialog.close).props("flat dense")
                ui.button("添加", icon="add", on_click=_do_add).props(
                    "unelevated dense color=primary"
                )

        dialog.open()

    def _confirm_delete_custom(s: _PlatformState) -> None:
        with ui.dialog() as dialog, ui.card().style("min-width: 360px;"):
            ui.label(f"删除服务商「{s.meta.display_name}」？").classes("text-subtitle1")
            ui.label(
                f"pid: {s.pid}。该条目会立即从列表中移除；"
                "若下次点击「全部保存」，配置文件中对应的 platform 也会被清理。"
            ).classes("text-caption text-grey-6 q-mb-sm")

            def _do_delete() -> None:
                try:
                    states.remove(s)
                except ValueError:
                    pass
                # 选中下一条（如果有）
                if states:
                    selection["pid"] = states[0].pid
                else:
                    selection["pid"] = ""
                dialog.close()
                _render_left()
                _render_right()
                _render_defaults()
                ui.notify(f"已移除 {s.meta.display_name}", type="info")

            with ui.row().classes("w-full justify-end").style("gap: 6px;"):
                ui.button("取消", on_click=dialog.close).props("flat dense")
                ui.button(
                    "确认删除", icon="delete_outline", on_click=_do_delete,
                ).props("unelevated dense color=negative")

        dialog.open()

    # -----------------------------------------------------------------------
    # 默认模型卡片
    # -----------------------------------------------------------------------

    def _collect_enabled_models(group_key: str) -> List[str]:
        """汇总所有"启用"平台里属于 ``group_key`` 且被勾选启用的模型名。"""
        out: List[str] = []
        for s in states:
            if not s.enabled:
                continue
            for mid, meta in s.models.items():
                if not meta.get("enabled"):
                    continue
                if (meta.get("group") or "llm_models") == group_key:
                    out.append(mid)
        # 去重 + 字母序，保持稳定
        return sorted(set(out), key=lambda x: x.lower())

    def _load_defaults() -> Dict[str, str]:
        load = yaml_store.load_yaml(_FILE)
        doc = load.doc if isinstance(load.doc, dict) else {}
        return {
            "DEFAULT_LLM_MODEL": str(doc.get("DEFAULT_LLM_MODEL") or ""),
            "DEFAULT_EMBEDDING_MODEL": str(doc.get("DEFAULT_EMBEDDING_MODEL") or ""),
        }

    default_cur = _load_defaults()
    default_selects: Dict[str, Any] = {}
    chat_inputs: Dict[str, Any] = {}

    def _options_with_current(group: str, current: str) -> List[str]:
        opts = _collect_enabled_models(group)
        if current and current not in opts:
            opts = [current] + opts  # 保留用户历史值，避免消失
        return opts or [""]  # 至少给一个占位

    def _render_defaults() -> None:
        # 若已有旧 select，先把当前值快照到 default_cur，避免重渲时丢失。
        for k, sel in default_selects.items():
            v = getattr(sel, "value", None)
            if isinstance(v, str) and v:
                default_cur[k] = v
        default_selects.clear()
        defaults_container.clear()
        with defaults_container:
            with ui.card().classes("w-full").props("flat bordered").style(
                "background: #fafbfc; padding: 10px 12px;"
            ):
                with ui.row().classes("items-center w-full no-wrap").style("gap: 10px;"):
                    ui.icon("tune", size="18px").classes("text-grey-7")
                    ui.label("默认模型").classes("text-subtitle2")
                    ui.label("（下拉选项来自已启用平台中已勾选启用的模型）").classes(
                        "text-caption text-grey-6"
                    )

                with ui.grid(columns=2).classes("w-full q-gutter-xs q-mt-xs"):
                    llm_opts = _options_with_current(
                        "llm_models", default_cur["DEFAULT_LLM_MODEL"]
                    )
                    sel_llm = ui.select(
                        llm_opts,
                        value=default_cur["DEFAULT_LLM_MODEL"] or (llm_opts[0] if llm_opts else ""),
                        label="默认 LLM (DEFAULT_LLM_MODEL)",
                        with_input=True,
                        new_value_mode="add-unique",
                    ).props("dense outlined clearable").classes("w-full")
                    sel_llm.tooltip("默认选用的 LLM 名称；未列出的名称可直接输入并回车。")
                    default_selects["DEFAULT_LLM_MODEL"] = sel_llm

                    emb_opts = _options_with_current(
                        "embed_models", default_cur["DEFAULT_EMBEDDING_MODEL"]
                    )
                    sel_emb = ui.select(
                        emb_opts,
                        value=default_cur["DEFAULT_EMBEDDING_MODEL"]
                        or (emb_opts[0] if emb_opts else ""),
                        label="默认 Embedding (DEFAULT_EMBEDDING_MODEL)",
                        with_input=True,
                        new_value_mode="add-unique",
                    ).props("dense outlined clearable").classes("w-full")
                    default_selects["DEFAULT_EMBEDDING_MODEL"] = sel_emb

    def _save_defaults() -> None:
        """把默认模型三项 + 对话参数（HISTORY_LEN / MAX_TOKENS / TEMPERATURE）
        一起写入 yaml（随 MODEL_PLATFORMS 同一次 save）。"""
        load = yaml_store.load_yaml(_FILE)
        doc = load.doc if isinstance(load.doc, dict) else {}
        for key, sel in default_selects.items():
            val = (sel.value or "").strip() if isinstance(sel.value, str) else ""
            if val:
                doc[key] = val
            else:
                doc.pop(key, None)
        # 对话参数（若 UI 未挂载则跳过，保留原值）
        if chat_inputs:
            hv = chat_inputs["HISTORY_LEN"].value
            try:
                doc["HISTORY_LEN"] = int(hv) if hv is not None and hv != "" else 3
            except (TypeError, ValueError):
                doc["HISTORY_LEN"] = 3

            mv = chat_inputs["MAX_TOKENS"].value
            if mv in (None, "", 0):
                # 留空 = 使用模型默认；写回 None 保持 yaml 语义一致
                doc["MAX_TOKENS"] = None
            else:
                try:
                    doc["MAX_TOKENS"] = int(mv)
                except (TypeError, ValueError):
                    doc["MAX_TOKENS"] = None

            tv = chat_inputs["TEMPERATURE"].value
            try:
                doc["TEMPERATURE"] = float(tv) if tv is not None and tv != "" else 0.7
            except (TypeError, ValueError):
                doc["TEMPERATURE"] = 0.7

        from chayuan.pydantic_settings_file import import_yaml
        y = import_yaml()
        yaml_store._backup(load.path)  # noqa: SLF001
        yaml_store._atomic_write(load.path, lambda f: y.dump(doc, f))  # noqa: SLF001
        yaml_store.mirror_namespace_to_db("model_settings.yaml", doc)

    # -----------------------------------------------------------------------
    # 左栏：平台目录
    # -----------------------------------------------------------------------

    def _filtered_states() -> List[_PlatformState]:
        q = (selection["search"] or "").strip().lower()
        tag = selection["filter_tag"] or "全部"
        out: List[_PlatformState] = []
        for s in states:
            if tag != "全部" and tag not in s.meta.tags:
                continue
            if q and q not in s.meta.display_name.lower() and q not in s.pid.lower():
                continue
            out.append(s)
        return out

    def _render_left() -> None:
        list_scroll.clear()
        with list_scroll:
            rows = _filtered_states()
            if not rows:
                ui.label("无匹配服务商").classes("text-grey-7 text-caption q-pa-sm")
                return

            # 48 题增量:**分批 mount** — 每批 6 个 provider × 8-10 ui element,
            # 50ms 间隔。20 个 provider 共 4 批 ≈ 200ms 完成,期间 asyncio loop 有
            # 机会 flush WS 心跳给客户端,**不再因 200+ DOM 一次性 mount 卡住 WS**。
            # 第一批同步立即 mount(用户首屏立刻看到上面 6 个最常用 provider),
            # 后续批用 ui.timer 异步推迟。
            container = ui.column().classes("w-full").style("gap: 2px;")
            BATCH = 6
            BATCH_DELAY = 0.05  # 50ms

            with container:
                # 第 0 批 — 同步,首屏立即可见
                for s in rows[:BATCH]:
                    _render_left_row(s)

            # 后续批 — ui.timer 分批 mount
            cursor = {"i": BATCH}

            def _mount_next_batch() -> None:
                if cursor["i"] >= len(rows):
                    return
                end = min(cursor["i"] + BATCH, len(rows))
                try:
                    with container:
                        for s in rows[cursor["i"]:end]:
                            _render_left_row(s)
                except Exception as e:  # noqa: BLE001
                    logger.debug("[provider_list] batch mount failed: %r", e)
                cursor["i"] = end
                if cursor["i"] < len(rows):
                    try:
                        ui.timer(BATCH_DELAY, _mount_next_batch, once=True)
                    except Exception:  # noqa: BLE001
                        pass

            if len(rows) > BATCH:
                try:
                    ui.timer(BATCH_DELAY, _mount_next_batch, once=True)
                except Exception:  # noqa: BLE001
                    # 没有 ui.timer 就退回同步(测试 / 非 NiceGUI 环境)
                    while cursor["i"] < len(rows):
                        end = min(cursor["i"] + BATCH, len(rows))
                        with container:
                            for s in rows[cursor["i"]:end]:
                                _render_left_row(s)
                        cursor["i"] = end

    def _render_left_row(s: _PlatformState) -> None:
        is_active = s.pid == selection["pid"]
        row_classes = "w-full no-wrap chayuan-provider-row"
        if is_active:
            row_classes += " is-active"

        row = ui.row().classes(row_classes)
        row.on("click", lambda _=None, pid=s.pid: _select(pid))

        with row:
            _render_provider_avatar(s.meta, size=26)
            col = ui.column().classes("col-grow").style(
                "gap: 0; min-width: 0; overflow: hidden;"
            )
            with col:
                line1 = ui.row().classes("items-center no-wrap").style("gap: 4px;")
                with line1:
                    ui.label(s.meta.display_name).classes("text-body2").style(
                        "font-weight: 500; overflow: hidden; text-overflow: ellipsis; "
                        "white-space: nowrap; min-width: 0;"
                    )
                    if s.validated and s.has_enabled_model():
                        ui.icon("check_circle", size="13px").classes("text-positive")
                # sub line：仅显示模型计数（简洁），不再渲染 tag——tag 靠顶部筛选已经够用
                if s.models:
                    enabled_cnt = sum(1 for m in s.models.values() if m["enabled"])
                    ui.label(
                        f"{enabled_cnt}/{len(s.models)} 已启用模型"
                    ).classes("text-caption text-grey-6").style(
                        "font-size: 11px; line-height: 1.1;"
                    )

            def _toggle(e, pid=s.pid) -> None:
                st = next((x for x in states if x.pid == pid), None)
                if st is None:
                    return
                st.enabled = bool(e.value)

            sw = ui.switch(value=s.enabled, on_change=_toggle).props("dense")
            # 开关缩小并阻止冒泡
            sw.style("transform: scale(0.8); transform-origin: right center;")
            sw.on("click.stop", lambda _e: None)

    def _render_provider_avatar(meta: ProviderMeta, size: int = 32) -> None:
        url = _logo_url(meta)
        if url:
            ui.image(url).style(
                f"width: {size}px; height: {size}px; object-fit: contain; "
                f"background: #fff; padding: 2px; "
                f"border-radius: {max(4, size // 5)}px; "
                f"border: 1px solid #e5e7eb; flex: 0 0 {size}px;"
            )
        elif meta.icon:
            # 自定义服务商：用 Material icon 作为 logo（白色底 + 主色图标）
            ui.html(
                f'<div style="width: {size}px; height: {size}px; '
                f'border-radius: {max(4, size // 5)}px; '
                f'background: #fff; border: 1px solid #e5e7eb; '
                f'display: flex; align-items: center; justify-content: center; '
                f'flex: 0 0 {size}px;">'
                f'<span class="material-icons" '
                f'style="color: {meta.color}; font-size: {int(size*0.62)}px;">'
                f'{_html_escape(meta.icon)}</span></div>'
            )
        else:
            letter = (meta.display_name or meta.pid)[:1].upper()
            ui.html(
                f'<div style="width: {size}px; height: {size}px; '
                f'border-radius: {max(4, size // 5)}px; '
                f'background: {meta.color}; color: #fff; display: flex; '
                f'align-items: center; justify-content: center; '
                f'font-weight: 600; font-size: {int(size*0.5)}px; '
                f'flex: 0 0 {size}px;">{_html_escape(letter)}</div>'
            )

    def _select(pid: str) -> None:
        selection["pid"] = pid
        _render_left()
        _render_right()

    def _on_search(e) -> None:
        selection["search"] = str(e.value or "")
        _render_left()

    def _on_filter(e) -> None:
        selection["filter_tag"] = str(e.value or "全部") or "全部"
        _render_left()

    search_input.on_value_change(_on_search)
    filter_tabs.on_value_change(_on_filter)

    # -----------------------------------------------------------------------
    # 右栏：选中服务商的详情
    # -----------------------------------------------------------------------

    def _render_right() -> None:
        right_col.clear()
        with right_col:
            s = _current()
            if s is None:
                ui.label("请从左侧选择一个服务商").classes("text-grey-7 q-pa-md")
                return
            _render_right_detail(s)

    # P3: 把右栏内容包进 dialog 弹出 (代码 100% 复用 _render_right_detail)
    # 关闭按钮在右上角;点 hero 卡片或 hero 行"更多"目录中条目都走这里
    def _open_provider_config_dialog(pid: str) -> None:
        target = next((x for x in states if x.meta.pid == pid), None)
        if target is None:
            ui.notify(f"未找到供应商: {pid}", type="warning")
            return
        # persistent + no-esc-dismiss + no-backdrop-dismiss: **三重保护**
        # 任何隐式触发(失焦/ESC/backdrop click)都不能关闭 dialog,只有显式点右上 X 才能关
        # 修复"加载阿里百炼 100+ 模型时,_render_left() 重渲让 dialog 失焦自动关闭"
        # 数据已通过 _autosave_on_change 实时落草稿,不会因 dialog 被关而丢失
        with ui.dialog().props("persistent no-esc-dismiss no-backdrop-dismiss") as dlg:
            with ui.card().style(
                "width: 920px; max-width: 96vw; "
                "height: 80vh; max-height: 80vh; "
                "padding: 0; gap: 0; "
                "display: flex; flex-direction: column;"
            ):
                # 顶栏: 标题 + [保存] + [关闭]
                with ui.row().classes("w-full items-center no-wrap").style(
                    "padding: 10px 16px; border-bottom: 1px solid #e5e7eb; "
                    "flex: 0 0 auto; gap: 8px;"
                ):
                    ui.label(f"{target.meta.display_name} · 配置").style(
                        "font-size: 16px; font-weight: 600; flex: 1;"
                    )

                    def _save_and_close() -> None:
                        """点保存:正式入 model_settings.yaml,清草稿,通知下游刷新。"""
                        # 67 题:保存前 flush pending 草稿,确保拿最新内存状态
                        _flush_pending_draft(target)
                        try:
                            kept, skipped = _save_all(states)
                        except Exception as e:  # noqa: BLE001
                            logger.exception("save_all from dialog failed")
                            ui.notify(f"保存失败: {e}", type="negative")
                            return
                        # 66 题:单个 dialog 保存也要走 mark_restart_needed wrapper,
                        # 触发 state_cache.invalidate + mark_tab_dirty(defaults/marketplace)
                        # 让 ④ 默认模型 tab 在用户切到时重渲染拿最新厂商列表
                        _mark_changed()
                        # _save_all 内部已 trigger_capability_defaults_refresh +
                        # trigger_framework_cards_refresh,无需重复
                        ui.notify(
                            f"已保存 {kept} 家厂商配置(过滤 {skipped} 家无启用模型)",
                            type="positive",
                        )
                        dlg.close()

                    def _close_with_flush() -> None:
                        """X 按钮:不保存关闭,但**必须 flush** 草稿(67 题 bug)。

                        否则用户关闭"启用"开关后立刻点 X,300ms debounce timer
                        被 dialog 销毁,草稿没写盘,重启后看到的还是旧值。
                        """
                        _flush_pending_draft(target)
                        dlg.close()

                    ui.button("💾 保存", on_click=_save_and_close).props(
                        "unelevated dense color=primary"
                    ).style("font-weight: 600;").tooltip(
                        "保存到正式 yaml + 通知默认模型选择器刷新候选"
                    )
                    ui.button(icon="close", on_click=_close_with_flush).props(
                        "dense flat round"
                    ).tooltip("不保存关闭(数据已落草稿,下次打开还在)").style(
                        "color: #6b7280;"
                    )
                # 主体: 复用 _render_right_detail
                # 97 题:**异步延迟 mount**,让 dialog 先出现 + WS 心跳能继续。
                # 阿里百炼这种厂商 100+ 模型 → 一次性同步 mount 整个 body
                # 会让 NiceGUI WS ack 超时 → connection lost。
                # 改为:先放 spinner,ui.timer 50ms 后真渲染内容。
                body = ui.scroll_area().classes("w-full").style(
                    "flex: 1 1 auto; min-height: 0; padding: 16px;"
                )
                with body:
                    spinner = ui.row().classes("items-center w-full").style(
                        "padding: 24px; gap: 8px;"
                    )
                    with spinner:
                        ui.spinner(size="md").classes("text-primary")
                        ui.label(f"加载 {target.meta.display_name} 配置…").classes(
                            "text-caption text-grey-7"
                        )

                def _mount_body() -> None:
                    body.clear()
                    with body:
                        try:
                            _render_right_detail(target)
                        except Exception as ex:  # noqa: BLE001
                            logger.exception("[provider dialog] body mount failed")
                            ui.label(
                                f"加载失败: {type(ex).__name__}: {ex}"
                            ).classes("text-negative")

                # 50ms 给 NiceGUI 一帧时间把 dialog 容器同步到客户端,
                # 然后再 mount 重内容 — WS 不会一次性塞太多消息超时
                ui.timer(0.05, _mount_body, once=True)
                # 底部:状态条 — 提示用户"修改已落草稿,点保存才正式生效"
                with ui.row().classes("items-center w-full no-wrap").style(
                    "padding: 6px 16px; border-top: 1px solid #f3f4f6; "
                    "background: #fafbfc; flex: 0 0 auto; gap: 6px;"
                ):
                    ui.icon("info", size="14px").classes("text-grey-6")
                    ui.label(
                        "修改自动落到草稿(关闭页面也不丢);点 💾 保存 才正式入 yaml + 触发默认模型刷新"
                    ).classes("text-caption text-grey-6").style("font-size: 11px;")
        dlg.open()

    def _render_right_detail(s: _PlatformState) -> None:
        # ========== Header 卡片：logo + 名称 + 标签 + 启用开关 ==========
        with ui.row().classes("items-center w-full no-wrap q-mb-sm").style(
            "gap: 12px; padding: 10px 14px; "
            "background: linear-gradient(180deg, #f9fafb 0%, #ffffff 100%); "
            "border: 1px solid #e5e7eb; border-radius: 8px;"
        ):
            _render_provider_avatar(s.meta, size=44)
            col = ui.column().classes("col-grow").style(
                "gap: 2px; min-width: 0;"
            )
            with col:
                ui.label(s.meta.display_name).classes("text-h6").style(
                    "line-height: 1.15; font-weight: 600;"
                )
                tag_row = ui.row().classes("items-center no-wrap").style(
                    "gap: 6px; margin-top: 2px;"
                )
                with tag_row:
                    ui.label(s.pid).classes("text-caption").style(
                        "font-family: ui-monospace, Menlo, monospace; "
                        "color: #6b7280;"
                    )
                    for tag in s.meta.tags:
                        ui.html(
                            f'<span style="display:inline-block;padding:1px 8px;'
                            f'border-radius:10px;background:#eef2ff;color:#4338ca;'
                            f'font-size:11px;line-height:1.5;">{_html_escape(tag)}</span>'
                        )

            def _on_enable_sw(e):
                # 厂商启用切换:只改内存 + debounce 落草稿(避免连续点击多次写盘)
                s.enabled = bool(e.value)
                _save_draft_state_debounced(s)

            # 自定义服务商显示删除按钮；内置目录条目不可删除（但可通过"禁用"+保存 达到同样效果）
            is_custom = s.pid not in _PROVIDER_BY_ID
            if is_custom:
                ui.button(
                    icon="delete_outline",
                    on_click=lambda: _confirm_delete_custom(s),
                ).props("flat round dense color=grey-7").tooltip(
                    "删除此自定义服务商（仅影响本次会话，点击「全部保存」才会落盘）"
                )

            ui.switch(text="启用", value=s.enabled, on_change=_on_enable_sw)

        # ========== 连接参数表单（紧凑栅格）==========
        with ui.card().classes("w-full q-mb-sm").props("flat bordered").style(
            "padding: 12px 14px;"
        ):
            ui.label("连接参数").classes("text-subtitle2 q-mb-xs").style(
                "color: #374151;"
            )
            with ui.grid(columns=2).classes("w-full").style("gap: 8px;"):
                type_select = ui.select(
                    PLATFORM_TYPE_CHOICES,
                    value=s.platform_type or "openai",
                    label="平台类型",
                    with_input=True,
                ).props("dense outlined").classes("w-full")
                type_select.tooltip("platform_type：决定后端用哪个分支解析请求/响应。")

                concur_input = ui.number(
                    label="并发数",
                    value=int(s.api_concurrencies),
                    min=1, max=1000, step=1,
                ).props("dense outlined").classes("w-full")
                concur_input.tooltip("api_concurrencies：同一平台最大并发请求数。")

                base_input = ui.input(
                    label="API 地址",
                    value=s.api_base_url,
                    placeholder=s.meta.default_api_base or "https://...",
                ).props("dense outlined").classes("col-span-2")
                base_input.tooltip("api_base_url：OpenAI 兼容端点，通常以 /v1 结尾。")

                # API Key 输入框 + "去申请 API Key"按钮(若厂商有 apply_key_url)
                # 用户在没 key 时一眼看到入口直接跳官方控制台,不用问"哪里办 key"
                with ui.row().classes("items-end w-full no-wrap col-span-2").style(
                    "gap: 8px;"
                ):
                    key_input = ui.input(
                        label="API Key",
                        value=s.api_key,
                        password=True,
                        password_toggle_button=True,
                    ).props("dense outlined").classes("col-grow")
                    # 该厂商有官方申请页 → 显示一个 link 按钮
                    apply_url = (s.meta.apply_key_url or "").strip()
                    if apply_url:
                        # 用 ui.link 比 ui.button 更轻量,并自带新标签页打开
                        ui.button(
                            "去申请 API Key", icon="open_in_new",
                        ).props("dense flat color=primary size=sm").on(
                            "click",
                            lambda _e=None, _u=apply_url:
                                ui.run_javascript(f"window.open('{_u}', '_blank')"),
                        ).tooltip(
                            f"在新标签页打开 {apply_url}"
                        ).style("flex: 0 0 auto;")
                    elif (s.meta.install_guide_url or "").strip():
                        # 本地推理服务 → 显示安装指南入口
                        guide_url = s.meta.install_guide_url.strip()
                        ui.button(
                            "查看安装指南", icon="open_in_new",
                        ).props("dense flat color=primary size=sm").on(
                            "click",
                            lambda _e=None, _u=guide_url:
                                ui.run_javascript(f"window.open('{_u}', '_blank')"),
                        ).tooltip(
                            f"在新标签页打开 {guide_url}"
                        ).style("flex: 0 0 auto;")

                proxy_input = ui.input(
                    label="HTTP 代理（可选）",
                    value=s.api_proxy,
                    placeholder="http://127.0.0.1:7890",
                ).props("dense outlined").classes("col-span-2")

            # 辅助开关单独一行，避免挤占按钮
            with ui.row().classes("items-center w-full no-wrap q-mt-xs").style(
                "gap: 8px;"
            ):
                auto_switch = ui.switch(
                    text="自动探测模型",
                    value=bool(s.auto_detect_model),
                )
                auto_switch.tooltip(
                    "auto_detect_model：开启后，后端会定时调用 /models 刷新可用模型。"
                    "本地部署（Ollama / Xinference / vLLM）推荐开启。"
                )
                ui.label("(Ollama / Xinference / vLLM 建议开启)").classes(
                    "text-caption text-grey-6"
                ).style("font-size: 11px;")

        # ========== 状态 + 验证按钮 ==========
        with ui.row().classes("items-center w-full no-wrap q-mb-sm").style(
            "gap: 10px; padding: 8px 12px; "
            "background: #f9fafb; border-radius: 6px; "
            "border: 1px solid #e5e7eb;"
        ):
            status_icon = ui.icon(
                "check_circle" if s.validated else "help",
                size="20px",
            ).classes("text-positive" if s.validated else "text-grey-6")
            status_label = ui.label(
                s.validate_message or "尚未验证，点击右侧按钮连接并拉取模型"
            ).classes("text-caption col-grow").style(
                "color: #4b5563; min-width: 0; "
                "overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
            )

            ui.button(
                "验证并获取模型", icon="refresh",
                on_click=lambda: _do_fetch(s),
            ).props("unelevated dense color=primary").style("flex: 0 0 auto;")

        def _collect() -> None:
            s.platform_type = str(type_select.value or "openai")
            s.api_base_url = (base_input.value or "").strip()
            s.api_key = (key_input.value or "").strip()
            s.api_proxy = (proxy_input.value or "").strip()
            try:
                s.api_concurrencies = int(concur_input.value or 5)
            except (TypeError, ValueError):
                s.api_concurrencies = 5
            s.auto_detect_model = bool(auto_switch.value)

        def _autosave_on_change(_e: Any = None) -> None:
            """每次任一字段变化都触发:写内存 + 落草稿 yaml。

            草稿 yaml 是用户配置的"暂存层" — 即使现在切到别的厂商 / 关闭页面 /
            重启进程,下次打开仍能恢复到当前编辑状态。这是"配置永不丢失"的核心。
            """
            _collect()
            # debounce — onChange 在 NiceGUI input 已经 debounce 200-400ms,
            # 这里再合并一次,API key 长串粘贴不会触发多次写盘
            _save_draft_state_debounced(s)

        # 给所有可编辑字段挂 onChange — NiceGUI 的 update:model-value 在用户
        # 停止输入约 200-400ms 后触发(debounced),已经足够轻量。
        for _w in (base_input, key_input, proxy_input, concur_input):
            try:
                _w.on("update:model-value", _autosave_on_change)
            except Exception:  # noqa: BLE001
                pass
        try:
            type_select.on("update:model-value", _autosave_on_change)
        except Exception:  # noqa: BLE001
            pass
        try:
            auto_switch.on("update:model-value", _autosave_on_change)
        except Exception:  # noqa: BLE001
            pass

        def _do_fetch(state: _PlatformState) -> None:
            _collect()
            status_label.set_text("正在连接并拉取模型清单...")
            status_icon.props("name=sync").classes("text-grey-6", remove="text-positive text-negative")

            res = fetch_models(state)
            if not res["ok"]:
                state.validated = False
                state.validate_message = res["message"]
                status_icon.props("name=error").classes("text-negative", remove="text-positive text-grey-6")
                status_label.set_text(res["message"])
                return

            state.validated = True
            state.validate_message = res["message"]
            new_count = 0
            # 获取即启用 — 用户期望"拉到的模型立即可用",省去 100+ 个手工勾选
            # 若用户不想用某模型,在下方清单里取消勾选即可
            for m in res["models"]:
                mid = m["id"]
                if mid not in state.models:
                    state.models[mid] = {
                        "group": m["guessed_group"],
                        "enabled": True,  # 默认启用 (从 False 改为 True)
                    }
                    new_count += 1
            state.enabled = True  # 厂商整体启用
            status_icon.props("name=check_circle").classes("text-positive", remove="text-negative text-grey-6")
            total = len(state.models)
            status_label.set_text(
                f"{res['message']};已默认启用 {new_count} 个新模型(共 {total} 个),"
                f"如需排除可在下方取消勾选"
            )
            # 拉取后立即落草稿 — 即使用户立刻切走/关弹窗,模型清单也不丢
            try:
                _save_draft_state(state)
            except Exception as e:  # noqa: BLE001
                logger.debug("autosave after fetch failed: %r", e)
            # 渲染清单/左栏 — 100+ 模型时 NiceGUI 偶发异常导致 dialog 崩;
            # 用 try/except 兜底,失败时给 notify 提示,不让 dialog 异常关闭
            try:
                _render_inventory_section()
            except Exception as e:  # noqa: BLE001
                logger.exception("[_do_fetch] render inventory failed")
                ui.notify(
                    f"渲染模型清单失败({type(e).__name__});"
                    f"数据已保存,关闭弹窗重开即可",
                    type="warning", timeout=5000,
                )
            try:
                _render_left()
            except Exception as e:  # noqa: BLE001
                logger.debug("[_do_fetch] render left failed: %r", e)

        # ========== 模型清单 ==========
        with ui.card().classes("w-full").props("flat bordered").style(
            "padding: 12px 14px;"
        ):
            with ui.row().classes("items-center w-full no-wrap q-mb-xs").style(
                "gap: 6px;"
            ):
                ui.icon("inventory_2", size="18px").classes("text-grey-7")
                ui.label("模型清单").classes("text-subtitle2").style(
                    "color: #374151;"
                )
                ui.label("按类型分组 · 只有勾选启用的模型会写入配置").classes(
                    "text-caption text-grey-6"
                )

            inventory_container = ui.column().classes("w-full").style("gap: 4px;")

        def _render_inventory_section() -> None:
            inventory_container.clear()
            with inventory_container:
                # 手动加一行
                with ui.row().classes("items-end w-full q-mb-sm").style("gap: 6px;"):
                    new_id = ui.input(
                        label="手动添加模型 ID",
                        placeholder="如 deepseek-chat / bge-m3",
                    ).props("dense outlined").classes("col-grow")
                    new_group = ui.select(
                        {g[0]: g[1] for g in MODEL_GROUPS},
                        value="llm_models",
                        label="分组",
                    ).props("dense outlined").classes("col-shrink").style("min-width: 170px;")

                    def _add_custom():
                        mid = (new_id.value or "").strip()
                        if not mid:
                            ui.notify("请先输入模型 ID", type="warning")
                            return
                        # user_added=True：手动加入的模型；用于在列表上展示「手动」chip
                        # 以及（将来若做黑名单）区别对待。仅做 UI 侧 meta 信息，不会
                        # 写到 yaml（to_yaml_item 只取 group/enabled）。
                        s.models[mid] = {
                            "group": str(new_group.value or "llm_models"),
                            "enabled": True,
                            "user_added": True,
                        }
                        new_id.set_value("")
                        ui.notify(f"已添加模型 {mid}，点击顶部「全部保存」后落盘。", type="positive")
                        _render_inventory_section()
                        _render_left()

                    ui.button("添加", icon="add", on_click=_add_custom).props(
                        "dense unelevated color=primary"
                    )

                grouped: Dict[str, List[str]] = {g: [] for g in _GROUP_KEYS}
                for mid, meta in s.models.items():
                    g = meta.get("group") or "llm_models"
                    if g not in grouped:
                        g = "llm_models"
                    grouped[g].append(mid)

                total = sum(len(v) for v in grouped.values())
                if total == 0:
                    ui.label(
                        "尚无模型。点击右上角「验证并获取模型」或手动添加。"
                    ).classes("text-grey-7 q-mt-sm")
                    return

                # 102 题(深度重构):**删除"≤60 同步 mount" fast path**。
                #
                # 旧实现:total ≤ 60 走 _render_inventory_groups,所有 expansion
                # default-opened,同步 for 循环 mount 所有行。30-60 模型 × 6-8
                # 组件/行 = 180-480 组件一次同步 mount,WS ack 超时 → connection
                # lost → 浏览器整页 reload 跳回 /dashboard。
                #
                # 现在:**任意数量**模型都走 _render_inventory_groups_batched,
                # 用 ui.timer 链式分批 mount(每批 30 行 + 50ms yield),WS 心跳
                # 全程不间断。即使 5-10 模型也只多 50ms 延迟,用户感知不到。
                #
                # 同时:summary card 阈值从 60 降到 30 — 30+ 模型默认显示 summary,
                # 把"查看清单"作为 opt-in,避免大厂商首次打开 dialog 仍要等 ~1s
                # 分批 mount 才能交互(用户期望"立即可点 ✓ 启用全部 + 保存")。
                if total > 30:
                    enabled_total = sum(
                        1 for m_meta in s.models.values() if m_meta.get("enabled")
                    )
                    with ui.row().classes("items-center w-full q-pa-md").style(
                        "background: #f0fdf4; border: 1px solid #a7f3d0; "
                        "border-radius: 6px; gap: 12px; margin-top: 8px;"
                    ):
                        ui.icon("check_circle", size="22px").classes("text-positive")
                        with ui.column().style("gap: 2px; flex: 1;"):
                            ui.label(f"已加载 {total} 个模型").style(
                                "font-weight: 600; font-size: 14px;"
                            )
                            ui.label(
                                f"全部默认启用({enabled_total} 个);"
                                f"无需逐个勾选,直接 💾 保存即可。"
                                f"如要排除部分,点右侧按钮分批展开"
                            ).classes("text-caption text-grey-7").style(
                                "font-size: 11px;"
                            )

                        def _show_full_list() -> None:
                            """分批渲染整张清单(避免一次性塞 100+ 组件)。"""
                            inventory_container.clear()
                            with inventory_container:
                                _render_inventory_groups_batched(s, grouped)

                        ui.button(
                            "查看 / 调整清单", icon="expand_more",
                            on_click=_show_full_list,
                        ).props("dense outlined color=primary").style("flex: 0 0 auto;")
                    return

                # ≤ 30 模型 — 也走分批渲染(消灭同步 mount fast path)
                _render_inventory_groups_batched(s, grouped)

        # 102 题:删除 _render_inventory_groups(老同步实现,已无调用方)。
        # 任何模型数都走 _render_inventory_groups_batched(下面),消灭同步 mount。

        def _bulk_set_enabled(state: _PlatformState, value: bool) -> None:
            """全部启用 / 全部停用 — 不重渲清单(switch 视觉自更新);只
            落草稿 + notify。100+ 模型时勾选全部不再"逐个点 switch"。"""
            for mid in state.models:
                state.models[mid]["enabled"] = bool(value)
            try:
                _save_draft_state_debounced(state)
            except Exception:  # noqa: BLE001
                pass
            # 重渲清单 — switch 才能反映 enabled 状态
            _render_inventory_section()
            ui.notify(
                f"已{'启用' if value else '停用'}全部 {len(state.models)} 个模型",
                type="positive",
            )

        def _render_inventory_groups_batched(
            state: _PlatformState,
            grouped: Dict[str, List[str]],
        ) -> None:
            """**分组列表 + 按需 lazy mount**(103 题深度重构)。

            旧实现一次性把所有 expansion 都 default-opened,把所有行 flatten
            进 30 行/批的 timer 链。100 模型 × 7 quasar 组件/行 = 700 组件,即便
            分批,**每批 30 行 ≈ 210 组件**仍接近 NiceGUI WS 单帧 ack 极限,
            网络一抖就触发 connection lost,浏览器整页 reload 跳回 /dashboard。

            现在:
                * **expansion 默认全部折叠**(value=False)— 同步 mount 只 5 个
                  expansion 头(空 default slot 占位),~10 个组件,绝不会卡 WS
                * 每个 expansion 注册 ``on('show', ...)`` — 用户**点开时**才
                  分批 mount 该组的行(每批 10 行 × 7 = 70 组件,50ms 间隔)
                * 任意时刻同时只 mount 一组,push 量稳定可控
                * 顶部加"返回概览 / 全部启用 / 全部停用"工具栏 — 解决"无法关闭"
                  痛点,以及大厂商 100+ 模型 bulk 操作场景

            性能:
                * 进入清单视图:~10 组件 push,< 50ms
                * 用户展开某组:每 50ms push 70 组件,30 行组共需 ~150ms 完成
                * 不再有"瞬时 push 数百组件"压垮 WS 的可能
            """
            # ===== 顶部工具栏:返回 + 批量操作 =====
            with ui.row().classes("items-center w-full q-mb-sm").style(
                "padding: 6px 10px; background: #f9fafb; "
                "border: 1px solid #e5e7eb; border-radius: 6px; gap: 6px;"
            ):
                ui.button(
                    "返回概览", icon="arrow_back",
                    on_click=lambda: _render_inventory_section(),
                ).props("flat dense color=primary")
                ui.space()
                total = sum(len(v) for v in grouped.values())
                enabled_total = sum(
                    1 for m in state.models.values() if m.get("enabled")
                )
                ui.label(f"已启用 {enabled_total} / {total}").classes(
                    "text-caption text-grey-7"
                ).style("font-size: 11px;")
                ui.button(
                    "全部启用", icon="check_box",
                    on_click=lambda _e=None: _bulk_set_enabled(state, True),
                ).props("dense unelevated color=positive size=sm").tooltip(
                    "把所有模型一键启用 — 之后可单独取消"
                )
                ui.button(
                    "全部停用", icon="check_box_outline_blank",
                    on_click=lambda _e=None: _bulk_set_enabled(state, False),
                ).props("dense flat color=grey-7 size=sm").tooltip(
                    "把所有模型一键停用 — 通常先停用再挑选少数启用"
                )

            # ===== 分组 expansion(默认全部折叠,展开时才 mount 行)=====
            for group_key, group_label, group_icon in MODEL_GROUPS:
                items = grouped[group_key]
                if not items:
                    continue
                enabled_count = sum(1 for m in items if state.models[m]['enabled'])

                exp = ui.expansion(
                    f"{group_label}({enabled_count} / {len(items)})",
                    icon=group_icon,
                    value=False,  # **默认全部折叠** — 减少首屏组件
                ).classes("w-full").props("dense")

                sorted_items = sorted(
                    items,
                    key=lambda mid: (0 if state.models[mid].get("enabled") else 1,
                                     mid.lower()),
                )

                # default slot 放占位 label(展开后被分批替换)
                with exp:
                    placeholder = ui.label(
                        f"展开后加载 {len(items)} 项..."
                    ).classes("text-caption text-grey-6 q-pa-sm")

                # 注册 on:show — 用户点开时才 mount 行,且分批 mount
                _register_expansion_lazy_mount(
                    state=state,
                    exp_w=exp,
                    placeholder=placeholder,
                    items=sorted_items,
                )

        def _register_expansion_lazy_mount(
            *,
            state: _PlatformState,
            exp_w: Any,
            placeholder: Any,
            items: List[str],
        ) -> None:
            """给 expansion 注册 on:show — 用户首次展开时按 batch 渲染该组的行。

            111 题深度调优(用户反馈"展开 expansion 又被赶到 /dashboard"):

            * **每行真实组件数远超 7**:``ui.select`` 内含 7 个 group options +
              内部 ``q-menu``/``q-list``/``q-item`` × 7,加上 switch/button/tooltip,
              单行 vnode ~15-20 个。10 行/批 = 150-200 vnode,接近 WS 单帧极限。
              降 ``BATCH_SIZE = 4`` (4 行 × 18 ≈ 72 vnode/批,远低于阈值)
            * **BATCH_DELAY 50→80ms**:给 NiceGUI 多 1 帧 ack 缓冲
            * **首次 mount 推后 80ms**:让 quasar ``@show`` 动画跑完(150-200ms),
              避免 mount 与动画交叠双重负担

            闭包变量 ``_mounted`` 防止多次展开/折叠重复 mount。
            """
            _mounted = {"done": False}
            BATCH_SIZE = 4
            BATCH_DELAY = 0.08
            FIRST_MOUNT_DELAY = 0.08

            def _do_mount() -> None:
                if _mounted["done"]:
                    return
                _mounted["done"] = True
                try:
                    placeholder.delete()
                except Exception:  # noqa: BLE001
                    pass

                cursor = {"i": 0}
                total = len(items)

                def _next_batch() -> None:
                    end = min(cursor["i"] + BATCH_SIZE, total)
                    for j in range(cursor["i"], end):
                        try:
                            with exp_w:
                                _render_inventory_row(state, items[j])
                        except Exception as e:  # noqa: BLE001
                            logger.debug(
                                "lazy_expansion mount row %s failed: %r",
                                items[j], e,
                            )
                    cursor["i"] = end
                    if cursor["i"] < total:
                        try:
                            ui.timer(BATCH_DELAY, _next_batch, once=True)
                        except Exception:  # noqa: BLE001
                            pass

                try:
                    ui.timer(BATCH_DELAY, _next_batch, once=True)
                except Exception:  # noqa: BLE001
                    # timer 不可用 → 同步 fallback
                    while cursor["i"] < total:
                        _next_batch()

            def _on_show(_e=None) -> None:
                # **关键**:展开事件触发时 quasar 正在播放 @show 动画
                # (~150-200ms),如果立即 mount 行,动画 + mount 同时挤 WS,
                # 网络一抖就 connection lost。把 mount 推迟到动画快结束。
                if _mounted["done"]:
                    return
                try:
                    ui.timer(FIRST_MOUNT_DELAY, _do_mount, once=True)
                except Exception:  # noqa: BLE001
                    _do_mount()

            try:
                exp_w.on("show", _on_show)
            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "expansion.on('show') bind failed: %r — fallback to default-opened",
                    e,
                )
                # 兜底:如果 on 绑定失败(NiceGUI 版本不支持)→ 立即 mount
                _do_mount()

        def _render_inventory_row(state: _PlatformState, mid: str) -> None:
            meta = state.models.setdefault(mid, {"group": "llm_models", "enabled": False})
            with ui.row().classes("items-center w-full no-wrap q-py-none").style("gap: 8px;"):
                ui.icon("chevron_right", size="16px").classes("text-grey-5")
                ui.label(mid).classes("text-body2").style(
                    "flex: 1 1 auto; min-width: 0; overflow: hidden; "
                    "text-overflow: ellipsis;"
                )
                # 「手动」chip：区分 user_added=True 的模型（即手动添加，非 /v1/models 拉取）
                # 让用户在管理清单时能一眼分辨出"自己添加的"与"供应商返回的"。
                if meta.get("user_added"):
                    ui.html(
                        '<span style="display:inline-flex;align-items:center;'
                        'padding:1px 8px;font-size:11px;line-height:1.4;'
                        'color:#1d4ed8;background:#dbeafe;border-radius:999px;'
                        'font-weight:600;white-space:nowrap;flex:0 0 auto;">手动</span>'
                    )

                def _on_group(e, model_id=mid) -> None:
                    state.models[model_id]["group"] = str(e.value or "llm_models")
                    _save_draft_state_debounced(state)  # 模型分组变化合并落草稿

                ui.select(
                    {g[0]: g[1] for g in MODEL_GROUPS},
                    value=meta.get("group") or "llm_models",
                    on_change=_on_group,
                ).props("dense borderless").classes("col-shrink").style("min-width: 150px;")

                def _on_enable(e, model_id=mid) -> None:
                    """切换单个模型启用 — 只更新内存 + 落草稿,不触发任何重渲。

                    100+ 模型时,每次勾选都重渲 _render_left + _render_defaults
                    会让用户感到明显卡顿(每次切换 mount 几百个 Quasar 组件)。
                    实测:模型清单视图本身不需要重渲(switch 自身视觉已更新);
                    左栏是隐藏占位(display:none);默认模型选择器只在
                    [💾 保存] 后才需要刷新候选,而非每勾选一次都刷。
                    """
                    state.models[model_id]["enabled"] = bool(e.value)
                    # debounce 合并:100+ 模型快速逐个勾选时只写最后 1 次盘
                    _save_draft_state_debounced(state)

                ui.switch(
                    value=bool(meta.get("enabled", False)),
                    on_change=_on_enable,
                ).props("dense")

                def _do_remove(model_id=mid, model_meta=meta) -> None:
                    """真正执行删除：从内存清单移除 + 重渲清单(让该行消失)。

                    去掉 _render_left / _render_defaults — 隐藏占位无需重渲,
                    默认模型选择器在保存后才需刷新。
                    """
                    state.models.pop(model_id, None)
                    _save_draft_state(state)
                    _render_inventory_section()  # 视觉上要让删除的那行消失
                    # 如果是"已保存过的"模型（已在 yaml 里），提醒用户去保存；
                    # 纯内存里新加的模型则轻量提示即可。
                    if model_meta.get("user_added") or state.validated:
                        ui.notify(
                            f"已从清单移除 {model_id}，点击顶部「全部保存」后才会从配置文件删除。",
                            type="info",
                            timeout=3500,
                        )
                    else:
                        ui.notify(f"已移除 {model_id}", type="positive")

                def _confirm_remove(model_id=mid) -> None:
                    """删除前弹确认——保护用户不误点 × 丢失成组清单。"""
                    with ui.dialog() as d, ui.card().style(
                        "min-width: 320px; max-width: 400px;"
                    ):
                        ui.label(f"删除模型「{model_id}」？").classes(
                            "text-subtitle1"
                        ).style("font-weight: 600;")
                        ui.label(
                            "仅影响本次会话；需点击顶部「全部保存」后才会真正从 "
                            "model_settings.yaml 中删除。"
                        ).classes("text-caption text-grey-7").style(
                            "line-height: 1.5;"
                        )
                        with ui.row().classes("q-mt-md justify-end w-full").style(
                            "gap: 8px;"
                        ):
                            def _cancel() -> None:
                                d.close()

                            def _ok() -> None:
                                d.close()
                                _do_remove()

                            ui.button("取消", on_click=_cancel).props(
                                "flat color=grey-7 dense"
                            )
                            ui.button(
                                "确认删除", icon="delete_outline", on_click=_ok,
                            ).props("unelevated color=negative dense")
                    d.open()

                ui.button(
                    icon="delete_outline", on_click=_confirm_remove,
                ).props(
                    "flat round dense size=sm color=grey-7"
                ).classes("chayuan-model-del-btn").tooltip(
                    "删除此模型（保存后生效）"
                )

        _render_inventory_section()

    # -----------------------------------------------------------------------
    # 底部:对话参数(历史轮数 / 最大 token / 温度)—— 紧凑三列
    # -----------------------------------------------------------------------
    # 58 题:_providers_only 模式跳过 — 对话参数已迁到 ④ 默认模型 tab,避免重复
    # legacy 模式仍渲染,以兼容旧调用方;_save_all 在 chat_inputs 为空时自动跳过这三个 key
    if not _providers_only:
        def _load_chat_params() -> Dict[str, Any]:
            load = yaml_store.load_yaml(_FILE)
            doc = load.doc if isinstance(load.doc, dict) else {}
            raw_max = doc.get("MAX_TOKENS")
            return {
                "HISTORY_LEN": doc.get("HISTORY_LEN", 3),
                "MAX_TOKENS": raw_max if isinstance(raw_max, int) else None,
                "TEMPERATURE": doc.get("TEMPERATURE", 0.7),
            }

        _chat_cur = _load_chat_params()
        with ui.card().classes("w-full q-mt-sm").props("flat bordered").style(
            "padding: 10px 14px; background: #fafbfc;"
        ):
            with ui.row().classes("items-center w-full no-wrap q-mb-xs").style("gap: 8px;"):
                ui.icon("chat", size="18px").classes("text-grey-7")
                ui.label("对话参数").classes("text-subtitle2").style("color: #374151;")
                ui.label("（全局默认，作用于所有对话；可被模型单独覆写）").classes(
                    "text-caption text-grey-6"
                )
            with ui.grid(columns=3).classes("w-full").style("gap: 8px;"):
                _hist = ui.number(
                    label="默认历史轮数 (HISTORY_LEN)",
                    value=int(_chat_cur["HISTORY_LEN"] or 0),
                    min=0, max=50, step=1,
                ).props("dense outlined").classes("w-full")
                _hist.tooltip("每次对话附带的历史轮数；0 表示不带历史。")
                chat_inputs["HISTORY_LEN"] = _hist

                _maxt = ui.number(
                    label="最大 Token (MAX_TOKENS)",
                    value=_chat_cur["MAX_TOKENS"],
                    min=0, step=64,
                    placeholder="留空使用模型默认",
                ).props("dense outlined clearable").classes("w-full")
                _maxt.tooltip(
                    "模型最长生成长度；留空=使用模型默认；填 0 等同留空。"
                )
                chat_inputs["MAX_TOKENS"] = _maxt

                _temp = ui.number(
                    label="温度 (TEMPERATURE)",
                    value=float(_chat_cur["TEMPERATURE"] or 0.7),
                    min=0.0, max=2.0, step=0.05,
                    format="%.2f",
                ).props("dense outlined").classes("w-full")
                _temp.tooltip("采样温度：0 更确定；值越大越发散（一般 0.2–1.2）。")
                chat_inputs["TEMPERATURE"] = _temp

    # -----------------------------------------------------------------------
    # 图像向量化模型卡片（位于对话参数下方、全部保存按钮上方）
    #   - 管理 CLIP / SigLIP / Chinese-CLIP / DINOv2 / ResNet 等模型的下载、
    #     离线上传、smoke 测试、删除、磁盘占用；
    #   - 按能力分组展示（跨模态 vs 仅视觉）；
    #   - 只有通过 smoke 测试的模型才会出现在新建图像知识库的下拉中。
    # -----------------------------------------------------------------------
    # P5: 图像向量化模型独立面板已移除 — 改由"模型广场" capability=image-embedding
    # 统一管理(下载 / 配置 / 切换),与对话/嵌入/重排等模型同框
    # render_image_model_card 函数仍保留在 image_model_panel.py 供历史调用,
    # 但本页不再渲染。用户在广场"图像嵌入" chip 下可以做同样的事。

    # -----------------------------------------------------------------------
    # 底部操作条:右下角「全部保存」
    # 61 题:_providers_only 模式跳过 — 每个厂商配置 dialog 已有独立 💾 保存,
    # 重复的"全部保存"令人困惑;legacy 模式仍渲染以兼容旧调用方
    # -----------------------------------------------------------------------
    if not _providers_only:
        with ui.row().classes("w-full justify-end q-mt-sm").style("gap: 8px;"):
            ui.button(
                "全部保存",
                icon="save",
                on_click=lambda: _do_save_all(),
            ).props("color=primary unelevated")

    # -----------------------------------------------------------------------
    # 初次渲染
    # -----------------------------------------------------------------------
    # 100 题:**providers_only 模式跳过这三个初次渲染**。
    # main_row 已经 display:none(line 2128-2130),defaults_container 已
    # display:none(line 2113)— 用户根本看不见这些内容。但 _render_right()
    # 同步调 _render_right_detail(770 行,enabled provider 100+ 模型时一次
    # mount 数百个组件)= 真正的 connection lost 元凶:
    #
    #   * NiceGUI 把这些组件**序列化推送到 client**(即使 display:none)
    #   * quasar JSON payload 单帧塞不下 → WS ack 超时
    #   * 浏览器判定 client 死 → 整页 reload 回到 /dashboard
    #
    # providers_only 模式下,UI 只用 hero strip + 点击触发的独立 dialog
    # (内部已用 ui.timer 异步 mount,见 _open_provider_config_dialog),
    # 旧版双栏目录 + 详情面板根本不渲染才对。
    if not _providers_only:
        _render_defaults()
        _render_left()
        _render_right()

    # -----------------------------------------------------------------------
    # 填充第三行（模型厂商横向卡片）。注意必须延迟到 selection / states 都
    # 准备好才能渲染，否则 state_lookup 拿不到 enabled 状态。
    # -----------------------------------------------------------------------
    try:
        from chayuan.server.config_panel.provider_hero_strip import (
            render_provider_hero_strip,
        )

        def _state_lookup(pid: str) -> Tuple[bool, bool, int]:
            for s in states:
                if s.pid != pid:
                    continue
                # configured = api_key 非空
                api_key = (s.api_key or "").strip() if hasattr(s, "api_key") else ""
                # 模型数 = 各 group 加总
                n = 0
                for grp in (
                    "llm_models", "embed_models", "rerank_models",
                    "text2image_models", "image2text_models",
                    "speech2text_models", "text2speech_models",
                ):
                    n += len(getattr(s, grp, []) or [])
                return bool(s.enabled), bool(api_key), n
            return False, False, 0

        def _on_pick(pid: str) -> None:
            # P3 重构: 点击 hero 卡片不再切换内嵌右栏(已隐藏),改弹独立 dialog
            # 把右栏的「连接配置 + 模型清单 + 启用切换」等表单原样塞进 dialog,
            # 一行不丢
            _open_provider_config_dialog(pid)

        def _on_add_custom() -> None:
            try:
                _open_add_provider_dialog()
            except Exception as e:  # noqa: BLE001
                logger.warning("open add provider dialog failed: %s", e)

        # 本地模型行(kind="local") — 在模型厂商之上;同源渲染,完全复用回调
        try:
            with local_hero_anchor:
                render_provider_hero_strip(
                    ui,
                    providers=PROVIDER_CATALOG,
                    state_lookup=_state_lookup,
                    on_pick=_on_pick,
                    on_add_custom=_on_add_custom,
                    limit=8,
                    kind="local",
                )
        except Exception as e:  # noqa: BLE001
            logger.exception("render local hero strip failed: %s", e)

        # 云厂商展示:
        #   * legacy ``_mode='full'``      = 8 张 hero strip + "更多"对话框(老 UI)
        #   * 57 题 ``_mode='providers_only'`` = 分组网格(推荐/国内/国外/聚合 全部展示)
        with hero_strip_container:
            if _providers_only:
                _render_cloud_providers_grouped(
                    ui,
                    providers=PROVIDER_CATALOG,
                    state_lookup=_state_lookup,
                    on_pick=_on_pick,
                    on_add_custom=_on_add_custom,
                )
            else:
                render_provider_hero_strip(
                    ui,
                    providers=PROVIDER_CATALOG,
                    state_lookup=_state_lookup,
                    on_pick=_on_pick,
                    on_add_custom=_on_add_custom,
                    limit=8,
                    kind="cloud",
                )
    except Exception as e:  # noqa: BLE001
        logger.exception("render provider hero strip failed: %s", e)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


__all__ = [
    "render_model_settings_page",
    "fetch_models",
    "PROVIDER_CATALOG",
    "MODEL_GROUPS",
    # 57 题 P2:云分组导出(用于 ② 模型厂商 tab 与单测)
    "group_cloud_providers",
]
