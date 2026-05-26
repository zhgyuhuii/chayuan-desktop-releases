/**
 * 由 path 派生稳定标题(用于 Tab / 历史)。
 *
 * 设计:不依赖 i18n;调用方拿到 key 后自己 t()。
 */

const STATIC: Record<string, string> = {
  '/home': 'nav.home',
  '/chat': 'nav.newChat',
  '/kb': 'nav.knowledge',
  '/marketplace': 'nav.modelMarket',
  '/history': 'nav.history',
  '/settings': 'nav.settings',
  '/mcp': 'mcp.title',
  '/tools': 'tools.title',
};

export function tabsStaticTitle(path: string): string | undefined {
  return STATIC[path];
}
