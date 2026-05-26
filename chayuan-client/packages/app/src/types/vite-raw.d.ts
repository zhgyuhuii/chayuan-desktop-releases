// Vite 的 `import xxx from './foo.md?raw'` 把文件作为字符串静态打包。
// 没有这条声明 TypeScript 不认识 `.md?raw` 这种 query 后缀的 module。
declare module '*.md?raw' {
  const content: string;
  export default content;
}

// SVG ?raw — KB 卡片用 simple-icons 的官方 DB logo 走 inline SVG 字符串,
// 运行时注入 ``fill="currentColor"`` 用品牌色 CSS 上色。
declare module '*.svg?raw' {
  const content: string;
  export default content;
}
