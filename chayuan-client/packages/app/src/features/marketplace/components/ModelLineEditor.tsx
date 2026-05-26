/**
 * ModelLineEditor —— 一类模型的"按行编辑"列表。
 *
 * 取代 ModelTypeAccordion 内部的单一 textarea:
 *   - 每行一个 model_id input + ✕ 删除按钮
 *   - 底部 "+ 添加模型" 按钮新增空行,自动 focus
 *   - 失焦时去重 + trim;空行不入库
 *   - 受控 value: string[]; onChange 把当前 ids 完整回传父级
 *
 * 设计:
 *   - 不再做内存 vs 受控的双状态机;UI 全程受控,父级单向数据流。
 *   - 用 nanoid 生成行 react key 避免"重命名时整行 unmount/remount"导致 input 失焦。
 *   - 模型 id 允许任意字符(包括 / 和 :),仅前后 trim;不做硬校验。
 */

import { Button, Input, cn } from '@chayuan/ui';
import { ArrowRightLeft, Plus, X } from 'lucide-react';
import * as React from 'react';
import { createPortal } from 'react-dom';

interface Row {
  /** 稳定 key —— react 列表 key 用,不参与业务 */
  rid: string;
  value: string;
}

let _rid = 0;
const nextRid = (): string => `r${(++_rid).toString(36)}_${Date.now().toString(36)}`;

/** "移到其他类型" 菜单项 — 父级提供时,每行渲染移动按钮 + 选项菜单 */
export interface MoveTarget {
  /** 目标类型 key(等于 ModelType,但本组件保持 type 上的弱耦合,用 string 即可) */
  key: string;
  /** 菜单项展示标签 */
  label: string;
}

export interface ModelLineEditorProps {
  /** 当前模型 id 列表(去重 / 已 trim 的稳定形态) */
  value: string[];
  /** 用户编辑后的列表(去重 / 已 trim);父级直接当 next state */
  onChange(next: string[]): void;
  /** 占位符示例(每个 ModelType 不同;一行一个 id) */
  placeholder?: string;
  /** 用空状态文案替代 "暂无模型" */
  emptyHint?: string;
  /**
   * 内置 model id 集合(catalog ``default_models`` 派生)。命中行不渲染删除按钮,
   * 防止误删 yaml seed 自带的主流模型。用户自加的 id 不在此处,正常可删。
   */
  lockedIds?: ReadonlySet<string>;
  /**
   * "移到其他类型" 菜单项;给定时每行右侧出现 ⇆ 按钮,点击展开菜单选目标。
   * 父级负责跨类型搬运(从本组件 value 删 + 加到目标类型 value);
   * 本组件只 emit ``onMoveToType`` 事件,不自己改 value。
   */
  moveTargets?: ReadonlyArray<MoveTarget>;
  /** 用户从菜单选了某个目标 → 父级在 ModelsByType 上做跨类型搬运 */
  onMoveToType?(modelId: string, targetKey: string): void;
  className?: string;
}

/** 把外部 value[] 同步进内部 rows;只在 value 集合变化时重建 rid,
 *  避免每次输入都重置 react key 导致 input 失焦。 */
function useSyncedRows(value: string[]): [Row[], React.Dispatch<React.SetStateAction<Row[]>>] {
  const [rows, setRows] = React.useState<Row[]>(() => value.map((v) => ({ rid: nextRid(), value: v })));
  // 当外部 value 与当前 rows.value 不一致(例如父级用"一键填入"覆盖)→ 重建
  const sig = React.useMemo(() => value.join(''), [value]);
  const rowsSig = React.useMemo(() => rows.map((r) => r.value).join(''), [rows]);
  React.useEffect(() => {
    if (sig !== rowsSig) {
      setRows(value.map((v) => ({ rid: nextRid(), value: v })));
    }
    // 故意只看 sig:rowsSig 仅用作"是否需要同步"哨兵
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);
  return [rows, setRows];
}

/** 去重 + trim + 过滤空 —— 受控对外的稳定形态 */
function normalize(rows: Row[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const r of rows) {
    const v = r.value.trim();
    if (!v || seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

export const ModelLineEditor: React.FC<ModelLineEditorProps> = ({
  value,
  onChange,
  placeholder,
  emptyHint = '暂无模型,点击下方"+ 添加模型"开始',
  lockedIds,
  moveTargets,
  onMoveToType,
  className,
}) => {
  const [rows, setRows] = useSyncedRows(value);
  // 新增行后,把焦点交给那行的 input
  const pendingFocusRid = React.useRef<string | null>(null);

  const commit = (nextRows: Row[]) => {
    setRows(nextRows);
    onChange(normalize(nextRows));
  };

  const onAdd = () => {
    const rid = nextRid();
    pendingFocusRid.current = rid;
    setRows((prev) => [...prev, { rid, value: '' }]);
    // 不立即 onChange;空行不入库,等用户敲了字再触发
  };

  const onRemove = (rid: string) => {
    commit(rows.filter((r) => r.rid !== rid));
  };

  const onEdit = (rid: string, v: string) => {
    const next = rows.map((r) => (r.rid === rid ? { ...r, value: v } : r));
    commit(next);
  };

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      {rows.length === 0 ? (
        <p className="px-1 py-2 text-[11px] text-[var(--cy-text-tertiary)]">{emptyHint}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map((r) => (
            <ModelRow
              key={r.rid}
              row={r}
              autoFocus={pendingFocusRid.current === r.rid}
              onCommitFocus={() => {
                if (pendingFocusRid.current === r.rid) pendingFocusRid.current = null;
              }}
              placeholder={placeholder}
              onChange={(v) => onEdit(r.rid, v)}
              onRemove={() => onRemove(r.rid)}
              locked={lockedIds?.has(r.value.trim()) ?? false}
              moveTargets={moveTargets}
              onMoveToType={
                onMoveToType
                  ? (target) => {
                      const id = r.value.trim();
                      if (!id) return;
                      // 先把本类型这一行删掉(走 commit 通知父级);父级 onMoveToType
                      // 再把同 id 加到目标类型的数组里。两步分别 onChange,React batch 合并。
                      commit(rows.filter((x) => x.rid !== r.rid));
                      onMoveToType(id, target);
                    }
                  : undefined
              }
            />
          ))}
        </ul>
      )}
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={onAdd}
        className="h-7 self-start text-[11px]"
      >
        <Plus className="h-3 w-3" />
        添加模型
      </Button>
    </div>
  );
};

const ModelRow: React.FC<{
  row: Row;
  autoFocus?: boolean;
  onCommitFocus?(): void;
  placeholder?: string;
  onChange(v: string): void;
  onRemove(): void;
  /** 内置模型行 = 不渲染 X 删除按钮(catalog 默认带的不让删) */
  locked?: boolean;
  moveTargets?: ReadonlyArray<MoveTarget>;
  onMoveToType?(target: string): void;
}> = ({
  row, autoFocus, onCommitFocus, placeholder,
  onChange, onRemove, locked, moveTargets, onMoveToType,
}) => {
  const ref = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (autoFocus && ref.current) {
      ref.current.focus();
      onCommitFocus?.();
    }
  }, [autoFocus, onCommitFocus]);

  // "移到其他类型" 菜单 — 自包含的弹出菜单(不引外部 Popover 依赖);
  // 点外部 / 选完一项 / Esc 都关闭。
  //
  // 菜单走 React Portal 挂到 document.body 用 ``position: fixed``,绕开
  // ModelTypeAccordion 容器的 ``overflow-hidden``(那是为了裁圆角带的)
  // 否则下拉会被裁掉一半。
  const [moveOpen, setMoveOpen] = React.useState(false);
  const moveRootRef = React.useRef<HTMLDivElement>(null);
  const moveBtnRef = React.useRef<HTMLButtonElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);
  const [menuPos, setMenuPos] = React.useState<{ top: number; left: number } | null>(null);

  // 计算菜单位置:贴在按钮下方右对齐(菜单 w-40 = 10rem,按 1rem=16px → 160px)
  const computeMenuPos = React.useCallback(() => {
    const btn = moveBtnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const menuWidth = 160;
    const left = Math.max(8, Math.min(window.innerWidth - menuWidth - 8, r.right - menuWidth));
    setMenuPos({ top: r.bottom + 4, left });
  }, []);

  React.useEffect(() => {
    if (!moveOpen) {
      setMenuPos(null);
      return;
    }
    computeMenuPos();
    const onDoc = (e: MouseEvent) => {
      if (moveRootRef.current?.contains(e.target as Node)) return;
      if (menuRef.current?.contains(e.target as Node)) return;
      setMoveOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMoveOpen(false);
    };
    const onScrollOrResize = () => computeMenuPos();
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', onScrollOrResize);
    // capture 监听任意祖先滚动(scroll 事件不冒泡,改用 capture)
    window.addEventListener('scroll', onScrollOrResize, true);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onScrollOrResize);
      window.removeEventListener('scroll', onScrollOrResize, true);
    };
  }, [moveOpen, computeMenuPos]);

  const showMoveBtn = !!moveTargets?.length && !!onMoveToType && !!row.value.trim();

  return (
    <li className="flex items-center gap-1.5">
      <Input
        ref={ref}
        value={row.value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 font-mono text-xs"
      />
      {showMoveBtn && (
        <div ref={moveRootRef} className="flex-shrink-0">
          <button
            ref={moveBtnRef}
            type="button"
            onClick={() => setMoveOpen((v) => !v)}
            aria-label="移到其他类型"
            title="移到其他类型(分类错了可以手动改)"
            className={cn(
              'inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-brand-600)]',
              moveOpen && 'bg-[var(--cy-surface-2)] text-[var(--cy-brand-600)]',
            )}
          >
            <ArrowRightLeft className="h-3.5 w-3.5" />
          </button>
          {moveOpen && menuPos && typeof document !== 'undefined' && createPortal(
            <div
              ref={menuRef}
              role="menu"
              style={{ position: 'fixed', top: menuPos.top, left: menuPos.left, zIndex: 1000 }}
              className="w-40 overflow-hidden rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] py-1 shadow-[var(--cy-shadow-md)]"
            >
              <p className="px-2.5 py-1 text-[10px] uppercase tracking-wide text-[var(--cy-text-tertiary)]">
                移到其他类型
              </p>
              {moveTargets!.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => {
                    setMoveOpen(false);
                    onMoveToType?.(t.key);
                  }}
                  className="block w-full px-2.5 py-1.5 text-left text-xs text-[var(--cy-text-primary)] transition-colors hover:bg-[var(--cy-brand-50)] hover:text-[var(--cy-brand-700)]"
                >
                  {t.label}
                </button>
              ))}
            </div>,
            document.body,
          )}
        </div>
      )}
      {locked ? (
        // 内置模型 — 占位保留布局对齐(等同 X 按钮宽度),用户能识别是"不让删"
        <span
          className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-[10px] text-[var(--cy-text-tertiary)]"
          title="内置模型,不可删除"
        >
          内置
        </span>
      ) : (
      <button
        type="button"
        onClick={onRemove}
        aria-label="删除该模型"
        className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-[var(--cy-text-tertiary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-red-600"
      >
        <X className="h-3.5 w-3.5" />
      </button>
      )}
    </li>
  );
};
