/**
 * .xlsx / .xls 预览:SheetJS 解析 → 多 sheet tab + 表格渲染。
 *
 * - SheetJS 也是 dynamic import
 * - 大表(>5000 行)虚拟化用 react-virtual 之类的 — 当前先按 sheet 截断 1000 行,
 *   超过时显示 "仅显示前 1000 行,完整请下载"。后续可换 virtualized table。
 */

import { Download } from 'lucide-react';
import * as React from 'react';
import type { RendererProps } from '../types';
import { buildDownloadUrl } from '../useFileUrl';
import { ErrorRenderer } from './ErrorRenderer';
import { LoadingRenderer } from './LoadingRenderer';

const ROW_LIMIT = 1000;

interface SheetData {
  name: string;
  rows: unknown[][];
  totalRows: number;
}

export const XlsxRenderer: React.FC<RendererProps> = ({ file, fetchBlob, onReady, onError }) => {
  const [sheets, setSheets] = React.useState<SheetData[] | null>(null);
  const [active, setActive] = React.useState(0);
  const [err, setErr] = React.useState<Error | null>(null);

  React.useEffect(() => {
    let live = true;
    const ac = new AbortController();
    setSheets(null);
    setErr(null);
    void (async () => {
      try {
        const blob = await fetchBlob(ac.signal);
        const buf = await blob.arrayBuffer();
        // xlsx 已在 packages/app/package.json declared；静态字符串 import →
        // Vite pre-bundle 命中。老版用 modName + @vite-ignore 反而让 dev 解析失败。
        type WB = { SheetNames: string[]; Sheets: Record<string, unknown> };
        type XlsxMod = {
          read: (data: ArrayBuffer, opts: { type: 'array' }) => WB;
          utils: {
            sheet_to_json: (ws: unknown, opts: { header: 1; defval: string }) => unknown[][];
          };
        };
        // xlsx 历史 .d.ts 在不同安装环境表现不一，手动 assert 形状。
        const XLSX = (await import('xlsx')) as unknown as XlsxMod;
        if (!live) return;
        const wb = XLSX.read(buf, { type: 'array' });
        const data: SheetData[] = wb.SheetNames.map((name) => {
          const ws = wb.Sheets[name];
          const rows = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' });
          return { name, rows: rows.slice(0, ROW_LIMIT), totalRows: rows.length };
        });
        if (!live) return;
        setSheets(data);
        setActive(0);
        onReady?.();
      } catch (e) {
        if ((e as Error).name === 'AbortError') return;
        const err = e as Error;
        setErr(err);
        onError?.(err);
      }
    })();
    return () => {
      live = false;
      ac.abort();
    };
  }, [fetchBlob, onReady, onError]);

  const onDownload = async () => {
    const url = await buildDownloadUrl(file);
    const a = document.createElement('a');
    a.href = url;
    a.download = file.fileName;
    a.click();
  };

  if (err) return <ErrorRenderer error={err} />;
  if (sheets == null) return <LoadingRenderer label="解析 Excel…" />;
  if (sheets.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
        空工作簿
      </div>
    );
  }

  const cur = sheets[active]!;
  const truncated = cur.totalRows > cur.rows.length;
  // 第一行作为表头(SheetJS sheet_to_json header:1 不区分 header 行,UI 这里把第一行当 header)
  const header = (cur.rows[0] ?? []) as unknown[];
  const body = cur.rows.slice(1);
  const headerKey = (value: unknown, col: number) => `h-${col}-${fmtCell(value)}`;
  const rowKey = (row: unknown[], rowNo: number) =>
    `r-${rowNo + 1}-${row.map(fmtCell).join('\u0001')}`;
  const cellKey = (col: number) => `c-${col}-${fmtCell(header[col])}`;
  return (
    <div className="flex h-full flex-col bg-[var(--cy-surface-base)]">
      {/* sheet tabs */}
      <div className="flex items-center gap-1 overflow-x-auto border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-2 py-1.5">
        {sheets.map((s, i) => (
          <button
            key={s.name}
            type="button"
            onClick={() => setActive(i)}
            className={`whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              i === active
                ? 'bg-[var(--cy-surface-base)] text-[var(--cy-text-primary)] shadow-sm'
                : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]'
            }`}
            title={`${s.name} · ${s.totalRows} 行`}
          >
            {s.name}
          </button>
        ))}
      </div>
      {truncated && (
        <div className="flex items-center justify-between gap-2 border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-800">
          <span>
            共 {cur.totalRows} 行;为保护性能仅显示前 {ROW_LIMIT} 行,完整数据请下载查看。
          </span>
          <button
            type="button"
            onClick={onDownload}
            className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-900 hover:bg-amber-200"
          >
            <Download className="h-3 w-3" /> 下载
          </button>
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <table className="min-w-full border-separate border-spacing-0 text-xs">
          <thead className="sticky top-0 z-[1]">
            <tr>
              {header.map((h, i) => (
                <th
                  key={headerKey(h, i)}
                  className="border-b border-r border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-1.5 text-left font-semibold text-[var(--cy-text-primary)]"
                >
                  {fmtCell(h)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, ri) => (
              <tr key={rowKey(row, ri)} className="hover:bg-[var(--cy-surface-1)]">
                {Array.from({ length: header.length }, (_, ci) => row[ci]).map((c, ci) => (
                  <td
                    key={cellKey(ci)}
                    className="whitespace-pre-wrap break-words border-b border-r border-[var(--cy-border-subtle)] px-3 py-1 align-top text-[var(--cy-text-secondary)]"
                  >
                    {fmtCell(c)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

function fmtCell(v: unknown): string {
  if (v == null || v === '') return '';
  if (typeof v === 'number') return String(v);
  if (v instanceof Date) return v.toISOString().slice(0, 19).replace('T', ' ');
  return String(v);
}
