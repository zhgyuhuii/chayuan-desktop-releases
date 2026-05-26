/**
 * McpEditDialog —— 新建 / 编辑 MCP 连接。
 *
 * 字段:
 *   server_name(必填) | transport(stdio/sse) | args[](数组,空白分隔)
 *   env(key=value,逐行) | timeout(秒) | description | enabled
 *
 * 行为:
 *   - mode='create' → POST /api/v1/mcp_connections/
 *   - mode='edit'   → PUT  /api/v1/mcp_connections/{id};也带"删除"按钮
 *   - 保存失败给 reportError;成功 onSaved(saved, action)
 *
 * 设计要点(对齐 KbAdminMenu 的编辑 dialog):
 *   - 简洁 6 字段
 *   - args 用一行 textarea 空白分隔(更直观)
 *   - env 用 KEY=VALUE 逐行(同 docker --env-file 风格)
 */

import * as React from 'react';
import { useMutation } from '@tanstack/react-query';
import { Trash2 } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Textarea,
  cn,
} from '@chayuan/ui';
import { mcp, type McpConnection, type McpConnectionInput } from '@chayuan/api';
import { reportError, notifySuccess } from '../../store/errorDialog';
import { AssistantBrandLogo } from '../../components/AssistantBrandLogo';
import { getPlatform } from '@chayuan/platform-shared';

export interface McpEditDialogProps {
  mode: 'create' | 'edit' | undefined;
  seed?: McpConnection;
  open: boolean;
  onOpenChange(open: boolean): void;
  onSaved?(saved: McpConnection | null, action: 'create' | 'edit' | 'delete'): void;
}

interface Form {
  server_name: string;
  transport: 'stdio' | 'sse';
  argsText: string;
  envText: string;
  timeout: number;
  description: string;
  enabled: boolean;
}

const EMPTY: Form = {
  server_name: '',
  transport: 'stdio',
  argsText: '',
  envText: '',
  timeout: 30,
  description: '',
  enabled: true,
};

export const McpEditDialog: React.FC<McpEditDialogProps> = ({
  mode,
  seed,
  open,
  onOpenChange,
  onSaved,
}) => {
  const [form, setForm] = React.useState<Form>(EMPTY);

  // 打开时按 mode/seed 灌值
  React.useEffect(() => {
    if (!open) return;
    if (mode === 'edit' && seed) {
      setForm({
        server_name: seed.server_name,
        transport: (seed.transport === 'sse' ? 'sse' : 'stdio'),
        argsText: (seed.args ?? []).join(' '),
        envText: Object.entries(seed.env ?? {})
          .map(([k, v]) => `${k}=${v}`)
          .join('\n'),
        timeout: seed.timeout || 30,
        description: seed.description ?? '',
        enabled: seed.enabled,
      });
    } else {
      setForm(EMPTY);
    }
  }, [open, mode, seed]);

  const buildPayload = (): McpConnectionInput => {
    const args = form.argsText
      .split(/\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const env: Record<string, string> = {};
    for (const line of form.envText.split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith('#')) continue;
      const idx = t.indexOf('=');
      if (idx <= 0) continue;
      const k = t.slice(0, idx).trim();
      const v = t.slice(idx + 1).trim();
      if (k) env[k] = v;
    }
    return {
      server_name: form.server_name.trim(),
      transport: form.transport,
      args,
      env,
      timeout: Number.isFinite(form.timeout) ? form.timeout : 30,
      description: form.description.trim(),
      enabled: form.enabled,
    };
  };

  const save = useMutation({
    mutationFn: async () => {
      const body = buildPayload();
      if (mode === 'edit' && seed) {
        return { conn: await mcp.update(seed.id, body), action: 'edit' as const };
      }
      return { conn: await mcp.create(body), action: 'create' as const };
    },
    onSuccess: ({ conn, action }) => onSaved?.(conn, action),
    onError: (e) => reportError(e, '保存失败'),
  });

  const del = useMutation({
    mutationFn: async () => {
      if (!seed) return;
      await mcp.delete(seed.id);
    },
    onSuccess: () => {
      notifySuccess('已删除');
      onSaved?.(null, 'delete');
    },
    onError: (e) => reportError(e, '删除失败'),
  });

  const canSave = form.server_name.trim().length > 0 && !save.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{mode === 'edit' ? '编辑 MCP 连接' : '新建 MCP 连接'}</DialogTitle>
          <DialogDescription>
            把外部进程 / SSE 服务暴露成 LLM 工具集;启用后会出现在 Composer 的"工具"面板。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <Field label="名称(唯一标识)*">
            <Input
              value={form.server_name}
              onChange={(e) => setForm({ ...form, server_name: e.target.value })}
              placeholder="例如 fs-readonly"
              required
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="协议">
              <div className="flex gap-2">
                {(['stdio', 'sse'] as const).map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setForm({ ...form, transport: t })}
                    className={cn(
                      'rounded-full border px-3 py-1 text-xs font-medium transition-colors',
                      form.transport === t
                        ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
                        : 'border-[var(--cy-border-default)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]',
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="超时(秒)">
              <Input
                type="number"
                value={form.timeout || ''}
                onChange={(e) => setForm({ ...form, timeout: Number.parseInt(e.target.value, 10) || 0 })}
                placeholder="30"
              />
            </Field>
          </div>

          <Field label="启动参数(空白分隔)">
            <Textarea
              rows={2}
              value={form.argsText}
              onChange={(e) => setForm({ ...form, argsText: e.target.value })}
              placeholder="例如:npx -y @modelcontextprotocol/server-filesystem /tmp"
              className="font-mono text-xs"
            />
          </Field>

          <Field label="环境变量(KEY=VALUE,每行一个)">
            <Textarea
              rows={3}
              value={form.envText}
              onChange={(e) => setForm({ ...form, envText: e.target.value })}
              placeholder="LOG_LEVEL=info\nFOO=bar"
              className="font-mono text-xs"
            />
          </Field>

          <Field label="描述">
            <Input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="可选"
            />
          </Field>

          <Field label="状态">
            <div className="flex gap-2">
              {[
                { v: true, label: '启用' },
                { v: false, label: '禁用' },
              ].map((it) => (
                <button
                  key={String(it.v)}
                  type="button"
                  onClick={() => setForm({ ...form, enabled: it.v })}
                  className={cn(
                    'rounded-full border px-3 py-1 text-xs transition-colors',
                    form.enabled === it.v
                      ? 'border-[var(--cy-brand-500)] bg-[var(--cy-brand-50)] text-[var(--cy-brand-600)]'
                      : 'border-[var(--cy-border-default)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]',
                  )}
                >
                  {it.label}
                </button>
              ))}
            </div>
          </Field>
        </div>

        <DialogFooter className="flex items-center justify-between">
          {mode === 'edit' && seed ? (
            <Button
              variant="ghost"
              className="text-destructive"
              onClick={async () => {
                const ok = await getPlatform().dialog.confirm(
                  `删除 MCP 连接「${seed.server_name}」?`,
                  { title: '删除 MCP 连接' },
                );
                if (ok) del.mutate();
              }}
              disabled={del.isPending || save.isPending}
            >
              {del.isPending ? <AssistantBrandLogo running className="mr-1.5 h-3.5 w-3.5 rounded-full" /> : <Trash2 className="mr-1.5 h-3.5 w-3.5" />}
              删除
            </Button>
          ) : (
            <span />
          )}
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={save.isPending}>
              取消
            </Button>
            <Button onClick={() => save.mutate()} disabled={!canSave}>
              {save.isPending ? <AssistantBrandLogo running className="mr-1.5 h-3.5 w-3.5 rounded-full" /> : null}
              保存
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="space-y-1">
    <label className="block text-xs font-medium text-[var(--cy-text-secondary)]">{label}</label>
    {children}
  </div>
);
