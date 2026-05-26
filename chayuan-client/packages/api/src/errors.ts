/**
 * 业务错误统一类型。
 *
 * 后端 chayuan-server 大量端点返回 {code, msg, data}：code != 0 视为业务错误。
 * REST 层（fetch）非 2xx 则是 HTTP 错误。
 *
 * 业务代码统一抛 BizError，包含足够信息让 UI 决定 toast / 重试 / 跳登录。
 */

export type ErrorKind =
  | 'http' // 网络/HTTP
  | 'biz' // 后端 code != 0
  | 'auth' // 401 且刷新失败
  | 'forbidden' // 403
  | 'timeout' // AbortController fired timeout
  | 'parse' // JSON 解析失败
  | 'unknown';

export interface BizErrorOptions {
  kind: ErrorKind;
  status?: number;
  code?: number;
  detail?: unknown;
  cause?: unknown;
  url?: string;
  traceId?: string;
}

export class BizError extends Error {
  readonly kind: ErrorKind;
  readonly status?: number;
  readonly code?: number;
  readonly detail?: unknown;
  readonly url?: string;
  readonly traceId?: string;

  constructor(message: string, opts: BizErrorOptions) {
    super(message);
    this.name = 'BizError';
    this.kind = opts.kind;
    this.status = opts.status;
    this.code = opts.code;
    this.detail = opts.detail;
    this.url = opts.url;
    this.traceId = opts.traceId;
    if (opts.cause !== undefined) (this as { cause?: unknown }).cause = opts.cause;
  }

  static auth(url?: string, traceId?: string): BizError {
    return new BizError('未登录或登录已过期', { kind: 'auth', status: 401, url, traceId });
  }

  static http(status: number, message: string, url?: string, traceId?: string): BizError {
    return new BizError(message, { kind: 'http', status, url, traceId });
  }

  static biz(code: number, message: string, detail?: unknown, traceId?: string): BizError {
    return new BizError(message, { kind: 'biz', code, detail, traceId });
  }
}

export function isBizError(e: unknown): e is BizError {
  return e instanceof Error && e.name === 'BizError';
}
