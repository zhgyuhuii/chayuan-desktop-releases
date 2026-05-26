/**
 * CSP 安全的运行时 @keyframes 注入。
 *
 * 为什么需要这个
 * --------------
 * 主页特效(喜鹊 / 星空 / 祥云 / 光带 / 粒子 / 卡片光环 / 徽章光晕…)的 CSS
 * 动画靠 @keyframes 驱动。旧实现是在 React 组件里渲染 ``<style>{kf}</style>``
 * 这种"运行时由 JS 创建的内联 <style> 元素"。
 *
 * 这在 ``pnpm dev`` 下没问题(Vite dev server 不下发 CSP),但**打包后**全挂:
 *
 *   - Tauri 2 打包时会处理 ``tauri.conf.json`` 的 ``app.security.csp``,
 *     给 ``script-src`` / ``style-src`` **追加一个 cryptographic nonce**,
 *     并把 ``index.html`` 里**静态**的 <style>/<script> 改写带上该 nonce
 *     (所以 index.html 里的启动 splash 动画在打包后照样动)。
 *   - 按 CSP Level 3 规范:一旦 ``style-src`` 出现 nonce-source,
 *     ``'unsafe-inline'`` 会被**忽略**。于是我们在配置里写的 ``'unsafe-inline'``
 *     在打包版里其实失效了。
 *   - React 在页面加载后由 JS 动态创建的 <style> 元素拿不到那个 nonce,
 *     于是被 CSP 拦掉 → @keyframes 根本没注册 → 所有靠关键帧的动画全死。
 *     喜鹊编队容器初始 ``left:0;top:0``,全靠 keyframes translate 移动,
 *     keyframes 没生效就卡在左上角 (0,0) —— 正是观察到的现象。
 *
 * 解决方式
 * --------
 * 改用 CSSOM 接口注册关键帧:``CSSStyleSheet`` + ``insertRule`` +
 * ``document.adoptedStyleSheets``。CSP 的 ``style-src`` 只管 <style> **元素**
 * 和 ``style=""`` **属性**,**不管**通过 CSSOM 编程式插入的样式规则 —— 所以
 * 这条路不受 nonce / unsafe-inline 影响,打包后照常生效,也无需放宽 CSP。
 *
 * 对 ``document.adoptedStyleSheets`` 不可用的老环境(理论上不会命中,
 * 打包用的 WebView2 / WKWebView 都支持)留了 <style> 兜底。
 */

/** 进程内单例的可构造样式表,homeFx 所有静态关键帧都注册到这里。 */
let sharedSheet: CSSStyleSheet | null = null;
/** 已注册过的关键帧/规则去重 key,避免重复 insertRule。 */
const registered = new Set<string>();

function supportsConstructable(): boolean {
  return (
    typeof document !== 'undefined' &&
    typeof CSSStyleSheet !== 'undefined' &&
    'adoptedStyleSheets' in Document.prototype &&
    'replaceSync' in CSSStyleSheet.prototype
  );
}

function getSharedSheet(): CSSStyleSheet | null {
  if (!supportsConstructable()) return null;
  if (sharedSheet) return sharedSheet;
  sharedSheet = new CSSStyleSheet();
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sharedSheet];
  return sharedSheet;
}

/**
 * 注册一段 CSS 文本(可含多条 @keyframes / @media 规则)。
 *
 * - ``dedupeKey`` 相同的 CSS 只注册一次,组件多实例 / 重渲染都安全。
 * - 走 CSSOM,不创建 <style> 元素,不受 CSP style-src 限制。
 * - 返回 ``false`` 表示当前环境用不了 CSSOM 路径,调用方应回退到 <style>。
 */
export function registerKeyframes(cssText: string, dedupeKey: string): boolean {
  const sheet = getSharedSheet();
  if (!sheet) return false;
  if (registered.has(dedupeKey)) return true;
  registered.add(dedupeKey);
  // 一段 CSS 文本里可能有多条规则;CSSStyleSheet 没有"追加文本"接口,
  // 用 insertRule 逐条加。先把整段塞进一个临时 sheet 解析,再把每条规则
  // 搬到共享 sheet —— 这样能正确处理 @keyframes / @media 等各种 at-rule。
  try {
    const parser = new CSSStyleSheet();
    parser.replaceSync(cssText);
    for (const rule of Array.from(parser.cssRules)) {
      sheet.insertRule(rule.cssText, sheet.cssRules.length);
    }
    return true;
  } catch {
    // 某条规则解析失败时不应连累其它特效,回退让调用方走 <style>。
    registered.delete(dedupeKey);
    return false;
  }
}
