/**
 * 笔记编辑器的"简体 ↔ 繁体"切换工具。
 *
 * 用 opencc-js(OpenCC 数据,词组级转换,准确度远高于 zhconv 字级转换):
 *   - 'cn' → 'tw': 简体 → 台湾繁体(常见词组替换,如 软件→軟體 / 项目→項目)
 *   - 'tw' → 'cn': 台湾繁体 → 简体
 *
 * Converter 首次调用时才加载词典(~ 几十 KB),延迟初始化避免开屏 + 单测开销。
 */
import { Converter } from 'opencc-js';

export type ChineseScript = 'cn' | 'tw';

type ConvFn = (s: string) => string;

let _toSimplified: ConvFn | null = null;
let _toTraditional: ConvFn | null = null;

const toSimplified = (): ConvFn => {
  if (!_toSimplified) _toSimplified = Converter({ from: 'tw', to: 'cn' });
  return _toSimplified;
};
const toTraditional = (): ConvFn => {
  if (!_toTraditional) _toTraditional = Converter({ from: 'cn', to: 'tw' });
  return _toTraditional;
};

/** 走遍 ProseMirror JSON 树,把所有 type==='text' 节点的 text 字段过 fn。
 *  保留 marks / 嵌套结构 / 非文本节点(image / hardBreak / 段落属性等)。 */
function transformTextNodes(node: unknown, fn: ConvFn): unknown {
  if (!node || typeof node !== 'object') return node;
  const obj = node as Record<string, unknown>;
  if (obj.type === 'text' && typeof obj.text === 'string') {
    return { ...obj, text: fn(obj.text) };
  }
  if (Array.isArray(obj.content)) {
    return { ...obj, content: obj.content.map((c) => transformTextNodes(c, fn)) };
  }
  return obj;
}

/** Tiptap editor JSON 整体转 cn/tw。caller 负责拿到 editor 然后 setContent。
 *  返回新的 JSON,不直接 mutate editor — 调用方决定怎么提交(setContent / dispatch)。 */
export function convertEditorChinese(json: unknown, target: ChineseScript): unknown {
  const fn = target === 'cn' ? toSimplified() : toTraditional();
  return transformTextNodes(json, fn);
}

/** 短字符串便捷转换(测试 / 状态栏提示 / 文件名前缀等用)。 */
export function convertText(text: string, target: ChineseScript): string {
  if (!text) return text;
  const fn = target === 'cn' ? toSimplified() : toTraditional();
  return fn(text);
}

/** 检测系统语言偏好,决定笔记的中文形态默认值。
 *
 * 信号源(按优先级):
 *   1. localStorage 'cy.note.chinese-script'(用户上次选过的)
 *   2. navigator.languages / navigator.language 的 BCP 47 标签
 *      - 含 'Hant' / 'TW' / 'HK' / 'MO' → 'tw'(繁体)
 *      - 含 'Hans' / 'CN' / 'SG' / 'MY' → 'cn'(简体)
 *      - 任何其它(包括非中文系统、纯 'zh')→ 'cn'(笔记应用主诉求简体)
 *
 * 跟 chayuan i18n('zh-CN' / 'en' / 'ja' …)解耦:i18n 不区分繁简,这里
 * 单独检测 OS 层的 zh-Hant / zh-TW 信号。
 */
export function detectChineseScript(): ChineseScript {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const stored = window.localStorage.getItem('cy.note.chinese-script');
      if (stored === 'cn' || stored === 'tw') return stored;
    }
  } catch {
    // localStorage 在某些 sandbox 下不可用,fallthrough
  }
  const tags: string[] = [];
  if (typeof navigator !== 'undefined') {
    if (Array.isArray(navigator.languages)) tags.push(...navigator.languages);
    if (navigator.language) tags.push(navigator.language);
  }
  for (const tag of tags) {
    const t = tag.toLowerCase();
    if (
      t.includes('hant') || t.includes('-tw') || t.includes('-hk') || t.includes('-mo')
    ) {
      return 'tw';
    }
    if (t.includes('hans') || t.includes('-cn') || t.includes('-sg') || t.includes('-my') || t === 'zh') {
      return 'cn';
    }
  }
  // 非中文系统默认简体(笔记应用主流诉求)
  return 'cn';
}

/** 持久化用户的简繁偏好,下次启动 detectChineseScript 优先读这个。 */
export function persistChineseScript(script: ChineseScript): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem('cy.note.chinese-script', script);
    }
  } catch {
    // ignore
  }
}
