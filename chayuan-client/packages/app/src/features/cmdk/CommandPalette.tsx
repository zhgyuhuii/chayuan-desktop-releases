/**
 * ⌘K 命令面板。
 *
 * - 全局快捷键 ⌘K / Ctrl+K 唤起；ESC / Click outside 关闭。
 * - 命令注册中心：Pages（路由跳转）/ Models（切模型）/ Conversations（最近会话）/ Actions（清屏 / 设置 / 退出）
 * - O(1) prefix match + 模糊（lower-case + 子串）；命令多时也快。
 * - 命令通过 onSelect 派发；不直接耦合具体 store。
 */

import * as React from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  Input,
} from '@chayuan/ui';
import {
  MessageSquare,
  Settings as SettingsIcon,
  LogOut,
  Cpu,
  Plus,
  Library,
  Plug,
  Wrench,
  Search,
  ArrowRight,
} from 'lucide-react';
import { CONVERSATION_KEYS } from '../conversations/useConversations';
import type { ConversationView } from '../conversations/useConversations';
import { useComposerStore } from '../../store/composer';
import { useAuthStore } from '../../store/auth';
import { useLoginModalStore } from '../../store/loginModal';

interface Command {
  id: string;
  label: string;
  hint?: string;
  icon: React.ComponentType<{ className?: string }>;
  group: 'navigate' | 'model' | 'conversation' | 'action';
  keywords?: string;
  onSelect(): void | Promise<void>;
}

export const CommandPaletteRoot: React.FC = () => {
  const [open, setOpen] = React.useState(false);
  const [q, setQ] = React.useState('');
  const navigate = useNavigate();
  const qc = useQueryClient();
  const setActive = useComposerStore((s) => s.setActive);
  const setModel = useComposerStore((s) => s.setModel);
  const auth = useAuthStore();

  // 全局快捷键
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      // 在输入框里按 ⌘K 也允许（让 composer 用户能立即跳转）
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === '?' && !target?.matches('input,textarea,[contenteditable]')) {
        // 帮助键
        setOpen(true);
        setQ('帮助');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const conversations = (qc.getQueryData<ConversationView[]>(CONVERSATION_KEYS.list) ?? []).slice(0, 10);
  const models = qc.getQueryData<Array<{ id: string; platform_name?: string }>>(['v1.models']) ?? [];

  const commands: Command[] = React.useMemo(() => {
    const out: Command[] = [
      {
        id: 'nav.chat',
        label: '新对话',
        hint: '⌘ N',
        icon: Plus,
        group: 'action',
        keywords: 'new chat 新建',
        onSelect: () => navigate({ to: '/chat' }),
      },
      {
        id: 'nav.kb',
        label: '知识库管理',
        icon: Library,
        group: 'navigate',
        onSelect: () => navigate({ to: '/chat' }), // M2 后改 /kb
      },
      {
        id: 'nav.tools',
        label: '工具市场',
        icon: Wrench,
        group: 'navigate',
        onSelect: () => navigate({ to: '/chat' }),
      },
      {
        id: 'nav.mcp',
        label: 'MCP 连接',
        icon: Plug,
        group: 'navigate',
        onSelect: () => navigate({ to: '/chat' }),
      },
      {
        id: 'nav.settings',
        label: '设置',
        icon: SettingsIcon,
        group: 'navigate',
        onSelect: () => {
          // 当前 settings 是 dialog；走自定义事件让 Chrome 打开
          window.dispatchEvent(new CustomEvent('cy:open-settings'));
        },
      },
      ...(auth.isLoggedIn
        ? [
            {
              id: 'auth.logout',
              label: '退出登录',
              icon: LogOut,
              group: 'action' as const,
              onSelect: async () => {
                await auth.logout();
              },
            },
          ]
        : [
            {
              id: 'auth.login',
              label: '登录',
              icon: LogOut,
              group: 'action' as const,
              onSelect: () => {
                useLoginModalStore.getState().show();
              },
            },
          ]),
      ...models.map<Command>((m) => ({
        id: `model.${m.id}`,
        label: `切到模型 · ${m.id}`,
        hint: m.platform_name ?? '',
        icon: Cpu,
        group: 'model',
        keywords: `${m.id} ${m.platform_name ?? ''}`,
        onSelect: () => setModel(m.id),
      })),
      ...conversations.map<Command>((c) => ({
        id: `conv.${c.id}`,
        label: c.title,
        hint: new Date(c.updated_at).toLocaleDateString(),
        icon: MessageSquare,
        group: 'conversation',
        keywords: c.title,
        onSelect: () => {
          setActive(c.remote_id ?? c.id);
          navigate({ to: '/chat/$conversationId', params: { conversationId: c.remote_id ?? c.id } });
        },
      })),
    ];
    return out;
  }, [auth, conversations, models, navigate, setActive, setModel]);

  const filtered = React.useMemo(() => {
    if (!q.trim()) return commands;
    const lower = q.toLowerCase();
    return commands.filter(
      (c) => c.label.toLowerCase().includes(lower) || (c.keywords ?? '').toLowerCase().includes(lower),
    );
  }, [commands, q]);

  const grouped = React.useMemo(() => {
    const g: Record<Command['group'], Command[]> = { action: [], navigate: [], model: [], conversation: [] };
    for (const c of filtered) g[c.group].push(c);
    return g;
  }, [filtered]);

  // 键盘选择
  const [cursor, setCursor] = React.useState(0);
  React.useEffect(() => setCursor(0), [filtered.length]);

  const flat = filtered;
  const onSelect = async (i: number) => {
    const c = flat[i];
    if (!c) return;
    setOpen(false);
    setQ('');
    await c.onSelect();
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-xl gap-0 p-0">
        <DialogTitle className="sr-only">命令面板</DialogTitle>
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <Input
            autoFocus
            placeholder="搜索命令、模型、会话..."
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                setCursor((c) => Math.min(c + 1, flat.length - 1));
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setCursor((c) => Math.max(c - 1, 0));
              } else if (e.key === 'Enter') {
                e.preventDefault();
                void onSelect(cursor);
              }
            }}
            className="border-0 shadow-none focus-visible:ring-0"
          />
        </div>
        <div className="max-h-80 overflow-auto p-1">
          {Object.entries(grouped).map(([group, items]) =>
            items.length ? (
              <div key={group} className="py-1">
                <div className="px-2 py-1 text-[10px] font-semibold uppercase text-muted-foreground">
                  {GROUP_LABEL[group as Command['group']]}
                </div>
                {items.map((c, i) => {
                  const idx = flat.indexOf(c);
                  const Icon = c.icon;
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => void onSelect(idx)}
                      onMouseEnter={() => setCursor(idx)}
                      className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm ${
                        idx === cursor ? 'bg-accent' : ''
                      }`}
                    >
                      <span className="flex items-center gap-2">
                        <Icon className="h-4 w-4 text-muted-foreground" />
                        {c.label}
                      </span>
                      <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                        {c.hint}
                        {idx === cursor && <ArrowRight className="h-3 w-3" />}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null,
          )}
          {!flat.length && <div className="px-3 py-6 text-center text-sm text-muted-foreground">无匹配</div>}
        </div>
      </DialogContent>
    </Dialog>
  );
};

const GROUP_LABEL: Record<Command['group'], string> = {
  action: '动作',
  navigate: '导航',
  model: '模型',
  conversation: '最近会话',
};
