/**
 * 模型支持目录 — 给"模型选择器 ? 帮助"用。
 *
 * 真源:跟后端 ``modality/router/protocol.py`` 的 ``_PREFIX_TO_VENDOR`` /
 * ``_PREFIX_TO_CAP`` 表保持同步;新增 vendor / 模型族在这里补一条,UI 自动
 * 渲染到帮助面板,不必改组件。
 *
 * 结构:
 *   - 一个 capability(对话/文生图/...)对应一段 ``CapabilityHelp``
 *   - 每段下面一组 ``VendorEntry`` — 厂商名、官网、API key 申请页、举例模型 id
 *
 * 不强求详尽,挑各家**最主流**的模型族列。用户照这个 id 去配置面板里"AI 平台"
 * 一栏填,Connector 通过 ``classify_vendor(model)`` 把请求路由到对应上游。
 *
 * 注意:URL 必须官方一手地址(jump page 也行),禁止短链 / 镜像。
 */

import type { ModalityCapability } from '@chayuan/transport';

export interface VendorEntry {
  name: string;
  /** 官网 / 平台落地页 */
  home: string;
  /** API key 申请 / 控制台地址(没有就填同 home) */
  apiKey: string;
  /** 示例模型 id 列表(填配置面板时直接抄) */
  models: string[];
  /** 一句话说明 — 价格档位 / 中文优势 / 注意事项 */
  note?: string;
  /**
   * 对应「模型广场」厂商目录(`/admin/model_platforms/catalog`)里的 ``pid``。
   *
   * 填了这个字段,帮助面板该厂商行就会多出一个「去配置」按钮 —— 点击后跳到
   * ``/marketplace?configure=<platformId>`` 并自动弹出该厂商的 PlatformSettingsDialog
   * (表单已按 catalog 预填 base URL / 模型清单,用户只需填 API key)。
   *
   * 对应不上 marketplace catalog 的厂商(本地 RapidOCR / PaddleOCR、catalog 暂未收录的
   * Stability / Runway / Kling / Sora 等)留空,UI 不渲染「去配置」按钮,只保留外链。
   * pid 取值见服务端 ``config_panel/model_config.py`` 的 ``PROVIDER_CATALOG``。
   */
  platformId?: string;
}

export interface CapabilityHelp {
  cap: ModalityCapability;
  /** 这一段总标题(组件里取 ``getCapabilityMeta(cap).longLabel`` 也可,留这里更直观) */
  title: string;
  /** 段落上方的一行说明 */
  blurb: string;
  vendors: VendorEntry[];
}


const HELP: ReadonlyArray<CapabilityHelp> = [
  {
    cap: 'chat',
    title: '对话模型(Chat / LLM)',
    blurb:
      '通用文本对话与推理。市面所有大模型都属此类;选模型时注意上下文窗口、价格、中文质量。',
    vendors: [
      {
        name: 'OpenAI',
        home: 'https://platform.openai.com/',
        apiKey: 'https://platform.openai.com/api-keys',
        models: ['gpt-4o', 'gpt-4o-mini', 'o1-mini', 'gpt-4.1'],
        note: '国内需走代理或第三方中转;支持函数调用 / vision / 长上下文。',
        platformId: 'openai',
      },
      {
        name: 'Anthropic Claude',
        home: 'https://www.anthropic.com/',
        apiKey: 'https://console.anthropic.com/settings/keys',
        models: ['claude-opus-4', 'claude-sonnet-4', 'claude-haiku-4'],
        note: '长文本与代码任务强;国内需代理。',
        platformId: 'anthropic',
      },
      {
        name: '阿里云百炼(DashScope)',
        home: 'https://bailian.console.aliyun.com/',
        apiKey: 'https://bailian.console.aliyun.com/?apiKey=1',
        models: ['qwen-max', 'qwen-plus', 'qwen-turbo', 'qwen3-coder-plus'],
        note: '国内直连;中文质量好;同一 key 可调画图 / 语音 / 视频系列。',
        platformId: 'bailian',
      },
      {
        name: '深度求索 DeepSeek',
        home: 'https://www.deepseek.com/',
        apiKey: 'https://platform.deepseek.com/api_keys',
        models: ['deepseek-chat', 'deepseek-reasoner'],
        note: '性价比极高;reasoner 走思维链推理。',
        platformId: 'deepseek',
      },
      {
        name: '字节豆包(火山方舟 Ark)',
        home: 'https://www.volcengine.com/product/ark',
        apiKey: 'https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey',
        models: ['doubao-pro-4k', 'doubao-pro-32k', 'doubao-lite-32k'],
        note: '国内直连;按 endpoint 走,需要在控制台创建推理点。',
        platformId: 'volcengine',
      },
      {
        name: '智谱 GLM',
        home: 'https://bigmodel.cn/',
        apiKey: 'https://bigmodel.cn/usercenter/apikeys',
        models: ['glm-4-plus', 'glm-4-flash', 'glm-4-air'],
        platformId: 'zhipu',
      },
      {
        name: 'Moonshot 月之暗面 Kimi',
        home: 'https://www.moonshot.cn/',
        apiKey: 'https://platform.moonshot.cn/console/api-keys',
        models: ['moonshot-v1-32k', 'moonshot-v1-128k', 'kimi-k2'],
        note: '长上下文擅长。',
        platformId: 'moonshot',
      },
      {
        name: 'OpenRouter(聚合代理)',
        home: 'https://openrouter.ai/',
        apiKey: 'https://openrouter.ai/keys',
        models: ['openrouter/auto', 'anthropic/claude-3.5-sonnet'],
        note: '一份 key 调用各家模型;价格略高于直连。',
        platformId: 'openrouter',
      },
    ],
  },
  {
    cap: 'vl',
    title: '视觉对话(VL / Vision-Language)',
    blurb:
      '同时看图 + 看文,做问答 / 描述 / 提取。普通对话模型(GPT-4o / Claude / qwen-vl)也算这一类。',
    vendors: [
      {
        name: '阿里云百炼 Qwen-VL / Qwen-Omni',
        home: 'https://bailian.console.aliyun.com/',
        apiKey: 'https://bailian.console.aliyun.com/?apiKey=1',
        models: ['qwen-vl-max', 'qwen-vl-plus', 'qwen-omni-turbo', 'qwen-audio-turbo'],
        note: '中文 OCR / 图表推理强;omni 还能处理音频输入。',
        platformId: 'bailian',
      },
      {
        name: 'OpenAI GPT-4o / GPT-4.1 vision',
        home: 'https://platform.openai.com/',
        apiKey: 'https://platform.openai.com/api-keys',
        models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1'],
        platformId: 'openai',
      },
      {
        name: 'Anthropic Claude vision',
        home: 'https://console.anthropic.com/',
        apiKey: 'https://console.anthropic.com/settings/keys',
        models: ['claude-opus-4', 'claude-sonnet-4'],
        platformId: 'anthropic',
      },
    ],
  },
  {
    cap: 't2i',
    title: '文生图(Text-to-Image)',
    blurb: '一句话生成一张图,常用于配图 / 概念草图 / 头像。生成时长一般 5-15 秒。',
    vendors: [
      {
        name: '阿里云百炼 Qwen-Image / 万象',
        home: 'https://bailian.console.aliyun.com/',
        apiKey: 'https://bailian.console.aliyun.com/?apiKey=1',
        models: ['qwen-image-max', 'qwen-image-plus', 'wanx-v1', 'wanxiang-v1'],
        note: 'wanx 走异步任务,延迟稍长但单图质量更高。',
        platformId: 'bailian',
      },
      {
        name: 'OpenAI DALL·E / GPT-Image',
        home: 'https://platform.openai.com/docs/guides/images',
        apiKey: 'https://platform.openai.com/api-keys',
        models: ['dall-e-3', 'gpt-image-1'],
        platformId: 'openai',
      },
      {
        name: '字节豆包 Doubao Image',
        home: 'https://www.volcengine.com/product/ark',
        apiKey: 'https://console.volcengine.com/ark',
        models: ['doubao-seedream-3-0-t2i', 'doubao-pro-image-256k'],
        platformId: 'volcengine',
      },
      {
        name: 'Stability AI(可走 OpenRouter)',
        home: 'https://platform.stability.ai/',
        apiKey: 'https://platform.stability.ai/account/keys',
        models: ['stable-diffusion-3-5', 'stable-image-ultra'],
      },
    ],
  },
  {
    cap: 'image_edit',
    title: '图像编辑(Image Edit / Inpaint)',
    blurb: '上传参考图 + 指令对图做修改:换背景 / 改人物 / 移除物体 / 风格迁移。',
    vendors: [
      {
        name: '阿里云百炼 Qwen-Image-Edit',
        home: 'https://bailian.console.aliyun.com/',
        apiKey: 'https://bailian.console.aliyun.com/?apiKey=1',
        models: ['qwen-image-edit-plus', 'qwen-image-edit'],
        note: '原生中文指令理解最稳。',
        platformId: 'bailian',
      },
      {
        name: 'OpenAI GPT-Image / DALL·E edit',
        home: 'https://platform.openai.com/',
        apiKey: 'https://platform.openai.com/api-keys',
        models: ['gpt-image-1'],
        platformId: 'openai',
      },
    ],
  },
  {
    cap: 't2v',
    title: '文生视频(Text-to-Video)',
    blurb:
      '描述一段场景出一段短视频(5-15 秒)。**异步任务**,提交后排队 30-120 秒,任务不丢。',
    vendors: [
      {
        name: '阿里云百炼 Wanx / 万象 T2V',
        home: 'https://bailian.console.aliyun.com/',
        apiKey: 'https://bailian.console.aliyun.com/?apiKey=1',
        models: ['wanx-t2v-turbo', 'wanxiang-t2v-plus', 'wan-2.1-t2v'],
        platformId: 'bailian',
      },
      {
        name: 'OpenAI Sora',
        home: 'https://openai.com/sora',
        apiKey: 'https://platform.openai.com/api-keys',
        models: ['sora'],
        note: '当前需申请白名单。',
        platformId: 'openai',
      },
      {
        name: 'Runway',
        home: 'https://runwayml.com/',
        apiKey: 'https://dev.runwayml.com/',
        models: ['runway-gen-3', 'runway-gen-3-alpha'],
      },
      {
        name: 'Kling 可灵(快手)',
        home: 'https://kling.kuaishou.com/',
        apiKey: 'https://klingai.com/dev-center/auth',
        models: ['kling-v1', 'kling-pro'],
      },
    ],
  },
  {
    cap: 'tts',
    title: '语音合成(Text-to-Speech)',
    blurb: '把文本读出来,可选音色 + 语速;输出 mp3 / wav,直接播放或下载。',
    vendors: [
      {
        name: '阿里云百炼 Qwen-TTS / CosyVoice',
        home: 'https://bailian.console.aliyun.com/',
        apiKey: 'https://bailian.console.aliyun.com/?apiKey=1',
        models: ['qwen3-tts-flash', 'qwen-tts-latest', 'cosyvoice-v1', 'sambert-zhichu-v1'],
        note: 'Cherry / Serena 等多个中文音色;qwen3-tts 还有方言版(京片儿 / 上海话 / 四川话)。',
        platformId: 'bailian',
      },
      {
        name: 'OpenAI TTS',
        home: 'https://platform.openai.com/docs/guides/text-to-speech',
        apiKey: 'https://platform.openai.com/api-keys',
        models: ['tts-1', 'tts-1-hd', 'gpt-4o-mini-tts'],
        note: 'alloy / echo / fable / onyx / nova / shimmer 6 个标准音色。',
        platformId: 'openai',
      },
      {
        name: '字节豆包 TTS',
        home: 'https://www.volcengine.com/product/ark',
        apiKey: 'https://console.volcengine.com/ark',
        models: ['doubao-tts'],
        platformId: 'volcengine',
      },
    ],
  },
  {
    cap: 'asr',
    title: '语音识别(Speech-to-Text / ASR)',
    blurb:
      '上传一段录音转写为文字。短音频(<3 分钟)走同步;长录音需异步流式识别(后续接入)。',
    vendors: [
      {
        name: '阿里云百炼 Qwen-ASR / Paraformer',
        home: 'https://bailian.console.aliyun.com/',
        apiKey: 'https://bailian.console.aliyun.com/?apiKey=1',
        models: ['qwen3-asr-flash', 'qwen-asr-flash', 'paraformer-v2', 'paraformer-realtime-v2'],
        note: 'qwen3-asr-flash 同步 3 分钟内单文件;paraformer 实时流式。',
        platformId: 'bailian',
      },
      {
        name: 'OpenAI Whisper / gpt-4o-transcribe',
        home: 'https://platform.openai.com/docs/guides/speech-to-text',
        apiKey: 'https://platform.openai.com/api-keys',
        models: ['whisper-1', 'whisper-large-v3', 'gpt-4o-transcribe', 'gpt-4o-mini-transcribe'],
        platformId: 'openai',
      },
    ],
  },
  {
    cap: 'ocr',
    title: '图像识字(OCR)',
    blurb: '从图片 / 截图 / 扫描件里抽取文字。本端内置 RapidOCR sidecar,无需 API key。',
    vendors: [
      {
        name: 'RapidOCR(本地,默认)',
        home: 'https://github.com/RapidAI/RapidOCR',
        apiKey: '无需 — 桌面端已 bundle',
        models: ['rapidocr', 'pp-ocr-v4'],
        note: '中英文 + 表格;离线可用。',
      },
      {
        name: 'PaddleOCR',
        home: 'https://github.com/PaddlePaddle/PaddleOCR',
        apiKey: '无需 — 自建服务',
        models: ['paddleocr'],
      },
    ],
  },
];


export function listCapabilityHelp(): ReadonlyArray<CapabilityHelp> {
  return HELP;
}
