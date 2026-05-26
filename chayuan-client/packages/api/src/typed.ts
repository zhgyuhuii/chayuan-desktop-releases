/**
 * 契约同步桥:openapi-typescript 生成的 paths 类型从这里导出。
 *
 * 工作流:
 *   1. 启动 chayuan-server: uvicorn ... --port 62581
 *   2. 跑 `pnpm gen:api`(默认连 127.0.0.1:62581)
 *   3. packages/api/generated/types.ts 出现
 *   4. 业务侧通过 `ApiBody<'/auth/login','post'>` 取强类型
 *
 * 没有跑 codegen 时 paths 是 Record<string, unknown> 兜底,让编译不挂掉;
 * 运行 codegen 后,业务侧请直接 `import type { paths } from '@chayuan/api/generated/types'`
 * 取索引,比这层 wrapper 更精确。
 */

type FallbackPaths = Record<string, unknown>;

export type ApiPaths = FallbackPaths;

/** 占位类型;codegen 跑过后业务侧直接走 generated/types。 */
export type ApiBody<_P extends keyof ApiPaths, _M extends string> = unknown;
export type ApiResponse<
  _P extends keyof ApiPaths,
  _M extends string,
  _S extends number = 200,
> = unknown;
