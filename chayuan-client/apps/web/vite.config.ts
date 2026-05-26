import path from 'node:path';
import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { type ProxyOptions, defineConfig, loadEnv } from 'vite';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pkg = (name: string) => path.resolve(__dirname, `../../packages/${name}/src/index.ts`);

/**
 * @chayuan/* workspace 包统一别名到源码 src/index.ts。
 * 解决 Windows + pnpm + Vite 走 symlink 链时偶发的
 * "Failed to resolve import '@chayuan/i18n'" 等。
 *
 * `@chayuan/ui/styles.css` 必须显式 alias:pnpm + Vite 在某些环境下
 * 不会按 package.json#exports 解析 subpath,导致整套 Tailwind 不注入。
 * subpath 必须排在裸路径正则前面,Vite 按声明序匹配。
 */
const chayuanAliases = [
  {
    find: '@chayuan/ui/styles.css',
    replacement: path.resolve(__dirname, '../../packages/ui/src/styles/globals.css'),
  },
  { find: /^@chayuan\/api$/, replacement: pkg('api') },
  { find: /^@chayuan\/app$/, replacement: pkg('app') },
  { find: /^@chayuan\/design-tokens$/, replacement: pkg('design-tokens') },
  { find: /^@chayuan\/i18n$/, replacement: pkg('i18n') },
  { find: /^@chayuan\/observability$/, replacement: pkg('observability') },
  { find: /^@chayuan\/platform-shared$/, replacement: pkg('platform-shared') },
  { find: /^@chayuan\/platform-web$/, replacement: pkg('platform-web') },
  { find: /^@chayuan\/transport$/, replacement: pkg('transport') },
  { find: /^@chayuan\/ui$/, replacement: pkg('ui') },
];

/**
 * 后端反代清单。
 *
 * ⚠️ /chat 不能整段代理:它跟前端 SPA 路由 /chat/$conversationId 冲突,
 * 浏览器硬刷新 /chat/<uuid> 会被 Vite 转给 FastAPI,直接返
 * `{"detail":"Not Found"}` 把人吓一跳。
 * 改为只列后端 chat 真实子路径,其余 /chat/* 落 index.html 给前端 router 处理。
 *
 * nginx 反代同样要按这份清单做精确路径匹配,不能 `location /chat/`。
 */
const BACKEND_PROXY_PREFIXES = [
  '/admin',
  '/auth',
  '/annotation',
  // chat:精确到子路径,避免吃掉 SPA 路由
  '/chat/chat/completions',
  '/chat/v2/chat',
  '/chat/conversations',
  '/cli',
  '/knowledge_base',
  '/knowledge_source',
  '/knowledge_universe',
  // 训练数据挂载 — chayuan-client 数据挂载 tab 走它
  '/data-mounts',
  // 运行时探针(框架卡片用)
  '/runtime',
  '/tools',
  '/v1',
  '/api/v1/mcp_connections',
  '/governance',
  '/storage',
  '/modality',
  '/image_models',
  '/model_registry',
  '/openapi',
  '/server',
  '/health',
  '/other',
  '/img',
  '/media',
  // /static/model_logos/<file> 服务于模型广场 catalog 的厂商 logo
  '/static',
];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const backend = env.VITE_BACKEND_URL || 'http://127.0.0.1:62581';

  const proxy = Object.fromEntries(
    BACKEND_PROXY_PREFIXES.map((p) => [
      p,
      {
        target: backend,
        changeOrigin: true,
        ws: true,
        bypass(req) {
          const accept = req.headers.accept || '';
          if (req.method === 'GET' && accept.includes('text/html')) return '/index.html';
        },
      } satisfies ProxyOptions,
    ]),
  );

  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: chayuanAliases },
    // workspace alias 把 @chayuan/app 指到 src，Vite 在源码做静态扫描时偶尔会
    // 漏掉里面 dynamic import('mammoth' / 'xlsx' / 'shiki') 这种延迟加载依赖。
    // 显式加进 include 强制 dev 启动时预打包；pnpm install 后立即可用，
    // 用户不再看到「请先 pnpm install」误报。
    optimizeDeps: {
      include: ['mammoth', 'xlsx', 'shiki', 'dompurify'],
    },
    server: {
      host: true,
      port: 5173,
      strictPort: false,
      proxy,
    },
    preview: {
      host: true,
      port: 5173,
      proxy,
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ['react', 'react-dom'],
            ui: ['@chayuan/ui'],
          },
        },
      },
    },
    define: {
      __APP_VERSION__: JSON.stringify(env.VITE_APP_VERSION || '0.1.0'),
    },
  };
});
