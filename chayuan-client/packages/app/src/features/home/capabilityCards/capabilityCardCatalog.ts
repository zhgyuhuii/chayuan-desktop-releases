/**
 * 主页"按模型类型开始对话"卡片目录。
 *
 * 单一真源:每张卡只声明 capability + 一句子标题 + 一句卡内 hint。
 * 名称 / 图标 / 配色全部从 ``modelCapabilityChip.getCapabilityMeta`` 取,
 * placeholder / submitLabel / hint 全部从 ``composerMode.getComposerMode``
 * 取,避免在主页里再维护一份会和 ChatComposer 飘移的文案。
 *
 * 顺序敏感:这就是主页网格的渲染顺序。把"用户最高频(对话 / 视觉)"放最前,
 * 把"重型异步(t2v)"放中间避免被主流挤掉,媒体输出收尾(tts / asr)。
 *
 * 不放 OCR / embedding / rerank:
 *   - OCR 已经有主页旧工具卡「图转文」入口,且本地 sidecar 永远 ready 不需要
 *     "未配置"分支,重复出现 UX 反而乱
 *   - embedding / rerank 是后台索引能力,在主页发起对话调用没意义
 */

import type { ModalityCapability } from '@chayuan/transport';

export interface CapabilityCardSpec {
  cap: ModalityCapability;
  /** 卡内主标题(覆盖 capabilityMeta.longLabel,允许更口语化) */
  title: string;
  /** 卡内副标题 — 一句话讲"这能干什么",不打开就能预判 */
  subtitle: string;
  /** 卡片底部一行"小贴士",举一个具体的输入示例,降低用户决策门槛 */
  hint: string;
  /**
   * 没有任何可用模型时的引导短语 — CapabilityCard 拼在"未配置"按钮 tooltip 里。
   * 不需要再写"去模型广场",按钮自带文字。
   */
  empty_hint: string;
}


export const HOME_CAPABILITY_CARDS: ReadonlyArray<CapabilityCardSpec> = [
  {
    cap: 'chat',
    title: '对话',
    subtitle: '通用聊天 + 推理 + 工具调用',
    hint: '示例:写一篇关于 RAG 的入门科普',
    empty_hint: '在「模型广场」接入任意一家云厂商或本地推理服务即可启用',
  },
  {
    cap: 'vl',
    title: '视觉对话',
    subtitle: '看图问答 / 图表解读 / 截图说明',
    hint: '示例:上传一张报表截图,问"哪一项同比增幅最大"',
    empty_hint: '接入 qwen-vl-max / GPT-4o / Claude vision 系列模型',
  },
  {
    cap: 't2i',
    title: '文生图',
    subtitle: '一句话生成插画 / 配图 / 概念草图',
    hint: '示例:雪山下的小屋,黄昏色调,水彩风',
    empty_hint: '接入 qwen-image-max / DALL·E / 字节豆包 SeedDream 等模型',
  },
  {
    cap: 'image_edit',
    title: '图像编辑',
    subtitle: '上传参考图 + 指令做修改',
    hint: '示例:把背景换成海边,人物保持不变',
    empty_hint: '接入 qwen-image-edit / GPT-Image-1 等图像编辑模型',
  },
  {
    cap: 't2v',
    title: '文生视频',
    subtitle: '描述一段场景生成 5-15 秒短视频',
    hint: '示例:小猫在草地上奔跑,慢镜头',
    empty_hint: '接入万象 wanx-t2v / Sora / Runway / 可灵 Kling 等',
  },
  {
    cap: 'tts',
    title: '语音合成',
    subtitle: '把文字读出来,可选音色 / 语速',
    hint: '示例:这是一份用 Cherry 音色朗读的稿件',
    empty_hint: '接入 qwen-tts / CosyVoice / OpenAI tts-1 等',
  },
  {
    cap: 'asr',
    title: '语音识别',
    subtitle: '上传一段录音转写为文字',
    hint: '示例:把一段会议录音转成文字稿',
    empty_hint: '接入 qwen3-asr-flash / Whisper / Paraformer 等',
  },
];
