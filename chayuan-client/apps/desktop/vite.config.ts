import { defineConfig, loadEnv } from 'vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pkg = (name: string) =>
  path.resolve(__dirname, `../../packages/${name}/src/index.ts`);

/**
 * @chayuan/* workspace 包统一别名到源码 src/index.ts(同 apps/web)。
 * 解决 Windows + pnpm + Vite 走 symlink 链时偶发的解析失败。
 *
 * 注意:subpath alias 必须排在裸路径正则前面 — Vite 按声明序匹配。
 * `@chayuan/ui/styles.css` 不能走 package.json#exports:Tauri WebView2
 * + pnpm symlink 下 subpath 解析会偶发拿不到,导致 Tailwind 整片不生效。
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
  { find: /^@chayuan\/platform-tauri$/, replacement: pkg('platform-tauri') },
  { find: /^@chayuan\/transport$/, replacement: pkg('transport') },
  { find: /^@chayuan\/ui$/, replacement: pkg('ui') },
];

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: chayuanAliases },
    clearScreen: false,
    server: {
      port: 1420,
      strictPort: true,
      host: process.env.TAURI_DEV_HOST || false,
    },
    envPrefix: ['VITE_', 'TAURI_ENV_'],
    build: {
      target:
        process.env.TAURI_ENV_PLATFORM === 'windows' ? 'chrome105' : 'safari13',
      minify: !process.env.TAURI_ENV_DEBUG ? 'esbuild' : false,
      sourcemap: !!process.env.TAURI_ENV_DEBUG,
      outDir: 'dist',
    },
    define: {
      __APP_VERSION__: JSON.stringify(env.VITE_APP_VERSION || '0.1.0'),
    },
  };
});
