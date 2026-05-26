/**
 * 把 ``CustomToolSpec`` 适配成 ``ToolCard`` 形态,让自定义工具能复用
 * 同一套 ToolCard 渲染(保证视觉一致 + 减少前端重复)。
 *
 * 不可避免的差异:
 *   - ``builtin = false``     卡片右上角 ⚙️ 按钮父级会分流到 CustomToolFormDialog
 *   - ``fields  = []``        自定义工具没有 catalog 字段(参数都是 LLM 动态填的)
 *   - ``category = "custom"`` 走单独色块,与 catalog 内置分类区分开
 */
import type { CustomToolSpec, ToolCard } from '@chayuan/api';

export function customToCard(s: CustomToolSpec): ToolCard {
  const summary = `${s.method.toUpperCase()} ${s.url}`;
  return {
    key: s.name,
    title: s.title || s.name,
    icon: 'http',
    category: 'custom',
    summary,
    description: s.description || summary,
    tags: ['custom', s.method.toLowerCase()],
    docs_url: '',
    fields: [],
    defaults: {},
    enabled: !!s.enabled,
    description_override: null,
    builtin: false,
  };
}
