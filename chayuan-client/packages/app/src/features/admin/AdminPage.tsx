import * as React from 'react';
import { ExternalLink, Activity, Shield, Database, TrendingUp, Boxes, Server } from 'lucide-react';
import { Button, cn } from '@chayuan/ui';
import { deepLinkTrace } from '@chayuan/observability';
import { getPlatform } from '@chayuan/platform-shared';
import { useAuthStore } from '../../store/auth';
import { useLocalTraces, useOutboxStat } from './useLocalTraces';
import { useLfHealth, useLfPrompts, useLfTraces } from './useLfApi';
import { ScoreTrend } from './ScoreTrend';
import { PlatformAdmin } from './PlatformAdmin';
import { SystemServicesPage } from './SystemServicesPage';

export type AdminTab = 'traces' | 'outbox' | 'prompts' | 'scores' | 'platforms' | 'services';

export const AdminPage: React.FC<{ onClose(): void; tab?: AdminTab }> = ({ onClose, tab: tabProp }) => {
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = React.useState<AdminTab>(tabProp ?? 'traces');
  React.useEffect(() => {
    if (tabProp) setTab(tabProp);
  }, [tabProp]);

  if (!user || user.role !== 'admin') {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
        <Shield className="h-8 w-8" />
        <p>需要管理员权限才能访问</p>
        <Button variant="outline" size="sm" onClick={onClose}>
          返回
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b px-6 py-3">
        <h2 className="text-lg font-semibold">Admin · 可观测性</h2>
        <Button variant="ghost" size="sm" onClick={onClose}>
          关闭
        </Button>
      </header>

      <div className="flex items-center gap-1 border-b px-2">
        <TabButton icon={Activity} label="最近 Trace" active={tab === 'traces'} onClick={() => setTab('traces')} />
        <TabButton icon={TrendingUp} label="Scores" active={tab === 'scores'} onClick={() => setTab('scores')} />
        <TabButton icon={Database} label="Outbox" active={tab === 'outbox'} onClick={() => setTab('outbox')} />
        <TabButton icon={Shield} label="Prompt" active={tab === 'prompts'} onClick={() => setTab('prompts')} />
        <TabButton icon={Boxes} label="模型平台" active={tab === 'platforms'} onClick={() => setTab('platforms')} />
        <TabButton icon={Server} label="系统服务" active={tab === 'services'} onClick={() => setTab('services')} />
      </div>

      <div className="flex-1 overflow-auto">
        {tab === 'traces' && <TraceList />}
        {tab === 'outbox' && <OutboxView />}
        {tab === 'scores' && <ScoreTrend />}
        {tab === 'prompts' && <PromptList />}
        {tab === 'platforms' && <PlatformAdmin />}
        {tab === 'services' && <SystemServicesPage />}
      </div>
    </div>
  );
};

const TabButton: React.FC<{
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  active: boolean;
  onClick: () => void;
}> = ({ icon: Icon, label, active, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    className={cn(
      'flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors',
      active ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:bg-muted',
    )}
  >
    <Icon className="h-4 w-4" />
    {label}
  </button>
);

const TraceList: React.FC = () => {
  // 先尝试调后端代理；失败 fallback 到本地聚合
  const remote = useLfTraces({ limit: 100 });
  const local = useLocalTraces();
  const health = useLfHealth();
  const open = (traceId: string) => {
    const url = deepLinkTrace(traceId);
    if (url) void getPlatform().shell.openExternal(url);
  };

  const remoteOk = remote.data?.data && remote.data.data.length > 0;
  if (remote.isLoading && !remote.data) {
    return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between border-b bg-muted/30 px-4 py-1.5 text-xs">
        <span>
          数据源：
          <span className="ml-1 font-medium">{remoteOk ? 'Langfuse 代理' : '本地聚合（fallback）'}</span>
        </span>
        <span
          className={cn(
            'rounded-full px-2 py-0.5 text-[10px] font-medium',
            health.data?.reachable ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700',
          )}
        >
          Langfuse {health.data?.reachable ? '在线' : '不可达'}
        </span>
      </div>
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-muted/60 text-xs">
          <tr>
            <th className="px-4 py-2 text-left">时间</th>
            <th className="px-4 py-2 text-left">名称 / 会话</th>
            <th className="px-4 py-2 text-left">用户</th>
            <th className="px-4 py-2 text-left">延迟</th>
            <th className="px-4 py-2 text-left">trace_id</th>
            <th className="px-4 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {remoteOk
            ? remote.data!.data.map((t) => (
                <tr key={t.id} className="border-b hover:bg-muted/30">
                  <td className="whitespace-nowrap px-4 py-2 text-xs text-muted-foreground">
                    {t.timestamp ? new Date(t.timestamp).toLocaleString() : '—'}
                  </td>
                  <td className="px-4 py-2">
                    {t.name ?? 'chat'}
                    {t.sessionId && <span className="ml-1 text-xs text-muted-foreground">· {t.sessionId.slice(0, 8)}</span>}
                  </td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">{t.userId ?? '—'}</td>
                  <td className="px-4 py-2 text-xs">{typeof t.latency === 'number' ? `${(t.latency * 1000).toFixed(0)}ms` : '—'}</td>
                  <td className="px-4 py-2 font-mono text-[10px]">{t.id.slice(0, 8)}…</td>
                  <td className="px-4 py-2 text-right">
                    <Button variant="ghost" size="icon" onClick={() => open(t.id)} aria-label="在 Langfuse 查看">
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Button>
                  </td>
                </tr>
              ))
            : local.data?.map((r) => (
                <tr key={r.traceId} className="border-b hover:bg-muted/30">
                  <td className="whitespace-nowrap px-4 py-2 text-xs text-muted-foreground">
                    {new Date(r.createdAt).toLocaleString()}
                  </td>
                  <td className="px-4 py-2">{r.conversationTitle ?? '—'}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">本地</td>
                  <td className="px-4 py-2 text-xs">—</td>
                  <td className="px-4 py-2 font-mono text-[10px]">{r.traceId.slice(0, 8)}…</td>
                  <td className="px-4 py-2 text-right">
                    <Button variant="ghost" size="icon" onClick={() => open(r.traceId)} aria-label="在 Langfuse 查看">
                      <ExternalLink className="h-3.5 w-3.5" />
                    </Button>
                  </td>
                </tr>
              ))}
          {!remoteOk && !local.data?.length && (
            <tr>
              <td colSpan={6} className="p-6 text-center text-sm text-muted-foreground">
                暂无 trace
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
};

const OutboxView: React.FC = () => {
  const q = useOutboxStat();
  return (
    <div className="space-y-3 p-6">
      <div className="rounded-lg border bg-card p-4">
        <div className="text-xs text-muted-foreground">待上传事件</div>
        <div className="mt-1 text-3xl font-bold">
          {q.data?.pending ?? 0}
          <span className="ml-2 text-xs text-muted-foreground">条</span>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          每 30s 自动 flush；如长期 &gt; 100，请检查 Langfuse 服务可达性。
        </p>
      </div>
    </div>
  );
};

const PromptList: React.FC = () => {
  const q = useLfPrompts({ limit: 100 });
  if (q.isLoading) return <div className="p-6 text-sm text-muted-foreground">加载中…</div>;
  if (q.isError) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        无法加载 prompt 列表（后端 /admin/prompts 不可达）。
      </div>
    );
  }
  const prompts = q.data?.data ?? [];
  if (!prompts.length) return <div className="p-6 text-sm text-muted-foreground">暂无 prompt</div>;
  return (
    <table className="w-full text-sm">
      <thead className="sticky top-0 bg-muted/60 text-xs">
        <tr>
          <th className="px-4 py-2 text-left">名称</th>
          <th className="px-4 py-2 text-left">版本</th>
          <th className="px-4 py-2 text-left">标签</th>
          <th className="px-4 py-2 text-left">最后更新</th>
        </tr>
      </thead>
      <tbody>
        {prompts.map((p) => (
          <tr key={p.name} className="border-b hover:bg-muted/30">
            <td className="px-4 py-2 font-medium">{p.name}</td>
            <td className="px-4 py-2">v{Math.max(0, ...(p.versions ?? [0]))}</td>
            <td className="px-4 py-2">
              {(p.labels ?? []).map((l) => (
                <span key={l} className="mr-1 rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
                  {l}
                </span>
              ))}
            </td>
            <td className="whitespace-nowrap px-4 py-2 text-xs text-muted-foreground">
              {p.lastUpdated ? new Date(p.lastUpdated).toLocaleString() : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};
