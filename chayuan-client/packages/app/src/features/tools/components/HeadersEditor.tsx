/**
 * 请求 headers 键值对编辑器(轻量,自定义工具用)。
 *
 * 不做模板变量,只支持静态字符串。常见场景:Authorization / Content-Type 等。
 * 真正变化的部分由参数(query/body)承载。
 */
import * as React from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button, Input } from '@chayuan/ui';

export interface HeadersEditorProps {
  value: Record<string, string>;
  onChange(next: Record<string, string>): void;
}

interface Row {
  /** 稳定 key,避免 input 重渲染失焦 */
  rid: string;
  k: string;
  v: string;
}

let _rid = 0;
const nextRid = () => `h${(++_rid).toString(36)}`;

export const HeadersEditor: React.FC<HeadersEditorProps> = ({ value, onChange }) => {
  // 受控:从 value 初始化为行数组,内部维护行级稳定 rid;onChange 时回拼成 dict
  const [rows, setRows] = React.useState<Row[]>(() =>
    Object.entries(value || {}).map(([k, v]) => ({ rid: nextRid(), k, v })),
  );

  // value 外部变化(切到另一个工具编辑)→ 重建行
  const lastValueRef = React.useRef(value);
  React.useEffect(() => {
    if (value !== lastValueRef.current) {
      lastValueRef.current = value;
      setRows(Object.entries(value || {}).map(([k, v]) => ({ rid: nextRid(), k, v })));
    }
  }, [value]);

  const flush = (next: Row[]) => {
    setRows(next);
    const out: Record<string, string> = {};
    for (const r of next) {
      const key = r.k.trim();
      if (!key) continue;
      out[key] = r.v;
    }
    lastValueRef.current = out;
    onChange(out);
  };

  const update = (idx: number, patch: Partial<Pick<Row, 'k' | 'v'>>) =>
    flush(rows.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  const remove = (idx: number) => flush(rows.filter((_, i) => i !== idx));
  const add = () => flush([...rows, { rid: nextRid(), k: '', v: '' }]);

  return (
    <div className="space-y-1.5">
      {rows.length > 0 && (
        <div className="space-y-1.5">
          {rows.map((r, i) => (
            <div key={r.rid} className="flex items-center gap-1.5">
              <Input
                value={r.k}
                placeholder="Authorization"
                onChange={(e) => update(i, { k: e.target.value })}
                className="h-7 flex-1 font-mono text-[11px]"
              />
              <span className="text-[10px] text-[var(--cy-text-tertiary)]">:</span>
              <Input
                value={r.v}
                placeholder="Bearer ..."
                onChange={(e) => update(i, { v: e.target.value })}
                className="h-7 flex-[2] font-mono text-[11px]"
              />
              <button
                type="button"
                onClick={() => remove(i)}
                aria-label="删除 header"
                className="inline-flex h-6 w-6 flex-shrink-0 items-center justify-center rounded text-[var(--cy-text-tertiary)] hover:bg-red-50 hover:text-red-600"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      )}
      <Button size="sm" variant="outline" onClick={add} className="h-7 text-[11px]">
        <Plus className="h-3 w-3" /> 添加 Header
      </Button>
    </div>
  );
};
