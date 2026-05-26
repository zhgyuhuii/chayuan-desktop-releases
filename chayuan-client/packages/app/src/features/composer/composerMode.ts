/**
 * Composer 模式描述符 —— 把模型 capability 映射到 UI 联动配置。
 *
 * 关注分离:
 *   - capability 推断 → `@chayuan/transport` 的 `classifyModalityCapability`
 *     (前后端同一份真源)
 *   - UI 表现 → 本文件,各 capability 一个 `ComposerModeConfig`
 *
 * 联动维度(按重要性):
 *   1. badge 图标 / 配色 / 短标签 —— ComposerModelPill 用
 *   2. placeholder —— Textarea 占位文案,告诉用户该输入什么
 *   3. submitLabel —— 发送按钮文案("生成图像 ✨" / "合成语音 🔊")
 *   4. attachmentRule —— 附件按钮行为:可选/禁止/必填(image_edit 必填参考图)
 *   5. paramKind —— 是否需要弹出模式专属参数条(尺寸/时长/音色...)
 *
 * **chat 模式即默认**,所有 UI 行为保持现状,不破坏任何老对话功能。
 *
 * 用法:
 * ```tsx
 *   const cap = classifyModalityCapability(modelId);
 *   const mode = getComposerMode(cap);
 *   // <Pill icon={mode.icon} label={mode.label} className={mode.colorClass}/>
 *   // <textarea placeholder={mode.placeholder} />
 *   // <Button>{mode.submitLabel}</Button>
 * ```
 */

import type { LucideIcon } from 'lucide-react';
import {
  Brush,
  Clapperboard,
  Image as ImageIcon,
  ImagePlus,
  Languages,
  Layers,
  MessageSquare,
  Mic,
  Music2,
  ScanText,
  Sparkles,
  Volume2,
} from 'lucide-react';
import type { ModalityCapability } from '@chayuan/transport';

export type AttachmentRule =
  | 'optional'           // chat / vl:可选附件
  | 'forbidden'          // t2i / t2v / tts:不接附件
  | 'required-image'     // image_edit:必填参考图
  | 'required-audio';    // asr:必填音频

export type ParamKind =
  | 'none'
  | 'image_size_n'       // t2i / image_edit:尺寸 + 数量 + seed
  | 'video_size_dur'     // t2v:尺寸 + 时长 + 帧率
  | 'tts_voice'          // tts:音色 + 语速 + 采样率
  | 'asr_lang';          // asr:语言

export interface ComposerModeConfig {
  capability: ModalityCapability;
  label: string;                  // pill 上的短标签
  icon: LucideIcon;
  /** tailwind 配色 — pill 背景/边框/文字 */
  colorClass: string;
  /** 输入框占位 */
  placeholder: string;
  /** 发送按钮文案 */
  submitLabel: string;
  /** 附件按钮规则 */
  attachmentRule: AttachmentRule;
  /** 是否需要参数条 + 哪种 */
  paramKind: ParamKind;
  /** 给用户的一行说明,放在 pill 下方或 tooltip 里 */
  hint?: string;
  /** 该模式下不应显示的 composer pill(如非 chat 模式不显示 KB / 搜索) */
  hidePills?: ('knowledge' | 'search' | 'embed')[];
  /** 后端走哪条路径:'chat'=老 /v1/chat/completions, 'modality'=新 /v1/modality/completions */
  backendPath: 'chat' | 'modality';
}

// ─────────────────────────────────────────────────────────────
// 各 capability 的配置 —— 唯一真源,改文案/图标/颜色都在这里
// ─────────────────────────────────────────────────────────────

const CHAT_MODE: ComposerModeConfig = {
  capability: 'chat',
  label: '对话',
  icon: MessageSquare,
  colorClass: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800/60',
  placeholder: '输入消息…',
  submitLabel: '发送',
  attachmentRule: 'optional',
  paramKind: 'none',
  backendPath: 'chat',
};

const VL_MODE: ComposerModeConfig = {
  capability: 'vl',
  label: '视觉对话',
  icon: Layers,
  colorClass: 'bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-950/40 dark:text-violet-300 dark:border-violet-800/60',
  placeholder: '描述图片相关问题,可附图…',
  submitLabel: '发送',
  attachmentRule: 'optional',
  paramKind: 'none',
  hint: '多模态对话 — 支持上传图片',
  backendPath: 'chat',                // VL 走老 chat 路径(多模态 content 数组)
};

const T2I_MODE: ComposerModeConfig = {
  capability: 't2i',
  label: '文生图',
  icon: ImageIcon,
  colorClass: 'bg-pink-50 text-pink-700 border-pink-200 dark:bg-pink-950/40 dark:text-pink-300 dark:border-pink-800/60',
  placeholder: '描述要生成的图像,例如:雪山下的小屋,黄昏色调…',
  submitLabel: '生成图像 ✨',
  attachmentRule: 'forbidden',
  paramKind: 'image_size_n',
  hint: '约需 5-15 秒,生成结果在消息流中可下载',
  hidePills: ['knowledge', 'search'],
  backendPath: 'modality',
};

const IMAGE_EDIT_MODE: ComposerModeConfig = {
  capability: 'image_edit',
  label: '图像编辑',
  icon: Brush,
  colorClass: 'bg-fuchsia-50 text-fuchsia-700 border-fuchsia-200 dark:bg-fuchsia-950/40 dark:text-fuchsia-300 dark:border-fuchsia-800/60',
  placeholder: '描述对参考图的修改,例如:把背景换成海边…',
  submitLabel: '编辑图像 🖌',
  attachmentRule: 'required-image',
  paramKind: 'image_size_n',
  hint: '必须先上传一张参考图',
  hidePills: ['knowledge', 'search'],
  backendPath: 'modality',
};

const T2V_MODE: ComposerModeConfig = {
  capability: 't2v',
  label: '文生视频',
  icon: Clapperboard,
  colorClass: 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950/40 dark:text-rose-300 dark:border-rose-800/60',
  placeholder: '描述视频场景,例如:小猫在草地上奔跑…',
  submitLabel: '生成视频 ▶',
  attachmentRule: 'forbidden',
  paramKind: 'video_size_dur',
  hint: '⚠ 约需 30-90 秒,生成中可关闭对话框,任务在后台继续',
  hidePills: ['knowledge', 'search'],
  backendPath: 'modality',
};

const TTS_MODE: ComposerModeConfig = {
  capability: 'tts',
  label: '语音合成',
  icon: Volume2,
  colorClass: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800/60',
  placeholder: '输入要朗读的文本,支持多语言…',
  submitLabel: '合成语音 🔊',
  attachmentRule: 'forbidden',
  paramKind: 'tts_voice',
  hidePills: ['knowledge', 'search'],
  backendPath: 'modality',
};

const ASR_MODE: ComposerModeConfig = {
  capability: 'asr',
  label: '语音识别',
  icon: Mic,
  colorClass: 'bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-950/40 dark:text-cyan-300 dark:border-cyan-800/60',
  placeholder: '上传音频文件转写为文字,或附说明…',
  submitLabel: '开始转写',
  attachmentRule: 'required-audio',
  paramKind: 'asr_lang',
  hint: '必须先上传音频文件',
  hidePills: ['knowledge', 'search'],
  backendPath: 'modality',
};

const EMBEDDING_MODE: ComposerModeConfig = {
  capability: 'embedding',
  label: '文本向量',
  icon: Sparkles,
  colorClass: 'bg-zinc-100 text-zinc-700 border-zinc-300 dark:bg-zinc-900/40 dark:text-zinc-300 dark:border-zinc-700/60',
  placeholder: '不可直接对话 — embedding 模型用于检索流程',
  submitLabel: '不可用',
  attachmentRule: 'forbidden',
  paramKind: 'none',
  hint: 'embedding 是后台索引能力,不能在对话框直接调用。请到「设置 → 默认模型」配置。',
  hidePills: ['knowledge', 'search'],
  backendPath: 'modality',           // 真要发也会被后端 friendly 拒
};

const RERANK_MODE: ComposerModeConfig = {
  capability: 'rerank',
  label: '重排',
  icon: ImagePlus,
  colorClass: 'bg-zinc-100 text-zinc-700 border-zinc-300 dark:bg-zinc-900/40 dark:text-zinc-300 dark:border-zinc-700/60',
  placeholder: '不可直接对话 — rerank 模型用于检索流程',
  submitLabel: '不可用',
  attachmentRule: 'forbidden',
  paramKind: 'none',
  hint: 'rerank 是后台检索能力,不能在对话框直接调用。',
  hidePills: ['knowledge', 'search'],
  backendPath: 'modality',
};

const OCR_MODE: ComposerModeConfig = {
  capability: 'ocr',
  label: '图像识字',
  icon: ScanText,
  colorClass: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-800/60',
  placeholder: '上传图片,提取文字…',
  submitLabel: '识别文字',
  attachmentRule: 'required-image',
  paramKind: 'none',
  hidePills: ['knowledge', 'search'],
  backendPath: 'modality',
};

// 占位:语言识别小模型(目前没单独 capability,留接口)
const _UNUSED: LucideIcon = Languages;
const _UNUSED2: LucideIcon = Music2;
void _UNUSED; void _UNUSED2;

const MODE_TABLE: Record<ModalityCapability, ComposerModeConfig> = {
  chat: CHAT_MODE,
  vl: VL_MODE,
  t2i: T2I_MODE,
  image_edit: IMAGE_EDIT_MODE,
  t2v: T2V_MODE,
  tts: TTS_MODE,
  asr: ASR_MODE,
  embedding: EMBEDDING_MODE,
  rerank: RERANK_MODE,
  ocr: OCR_MODE,
};

/**
 * 主查询入口 —— 按 capability 返完整 UI 配置;未知 capability 落 chat 默认。
 *
 * **不要**直接 import 各 MODE_* 常量;UI 永远从 `getComposerMode(cap)` 拿,
 * 这样改文案 / 加 capability 都收敛在本文件,降低跨文件风险。
 */
export function getComposerMode(cap: ModalityCapability | string | null | undefined): ComposerModeConfig {
  if (!cap) return CHAT_MODE;
  return MODE_TABLE[cap as ModalityCapability] ?? CHAT_MODE;
}
