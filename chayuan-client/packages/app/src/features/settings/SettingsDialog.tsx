import * as React from 'react';
import { Sun, Moon, Monitor, RotateCcw, Settings as SettingsIcon, Cpu } from 'lucide-react';
import {
  Button,
  cn,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  SegmentedTabs,
} from '@chayuan/ui';
import { useSettingsStore, type ThemeMode } from '../../store/settings';
import { useAuthStore } from '../../store/auth';
import { notifySuccess } from '../../store/errorDialog';
import { configureClient } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import { AiPlatformPanel } from '../aiPlatform';
import { DataDirSwitchSection } from '../first-run/DataDirSwitchSection';
import { useSingleMachineMode } from '../first-run/useSingleMachineMode';

export interface SettingsDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
}

type TabKey = 'general' | 'aiPlatform';

const TAB_ITEMS = [
  { value: 'general' as const, label: '通用', icon: <SettingsIcon className="h-3.5 w-3.5" /> },
  { value: 'aiPlatform' as const, label: 'AI 平台', icon: <Cpu className="h-3.5 w-3.5" /> },
];

export const SettingsDialog: React.FC<SettingsDialogProps> = ({ open, onOpenChange }) => {
  const settings = useSettingsStore();
  const auth = useAuthStore();
  const [apiInput, setApiInput] = React.useState(settings.apiBaseOverride);
  const [tab, setTab] = React.useState<TabKey>('general');
  const singleMachine = useSingleMachineMode();

  const platform = getPlatform();
  const defaultApi = platform.runtime.defaultApiBase;

  const onApplyApi = () => {
    const trimmed = apiInput.trim();
    settings.setApiBaseOverride(trimmed);
    configureClient({ baseURL: trimmed || defaultApi });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn(tab === 'aiPlatform' ? 'max-w-3xl' : 'max-w-lg')}>
        <DialogHeader>
          <DialogTitle>设置</DialogTitle>
          <DialogDescription>偏好与连接配置 · AI 平台运行状态</DialogDescription>
        </DialogHeader>

        <SegmentedTabs
          items={TAB_ITEMS}
          value={tab}
          onChange={(v) => setTab(v as TabKey)}
          size="sm"
          className="mb-2"
        />

        {tab === 'aiPlatform' ? (
          <section className="max-h-[70vh] overflow-y-auto pr-1">
            <AiPlatformPanel />
          </section>
        ) : (
        <section className="space-y-3">
          <Field label="主题">
            <div className="flex gap-1">
              {THEME_OPTS.map((o) => {
                const Icon = o.icon;
                const active = settings.theme === o.value;
                return (
                  <Button
                    key={o.value}
                    variant={active ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => settings.setTheme(o.value)}
                    className={cn('flex-1 gap-1.5')}
                  >
                    <Icon className="h-4 w-4" />
                    {o.label}
                  </Button>
                );
              })}
            </div>
          </Field>

          <Field label={`字号 (${settings.fontSize}px)`}>
            <input
              type="range"
              min={12}
              max={18}
              value={settings.fontSize}
              onChange={(e) => settings.setFontSize(Number.parseInt(e.target.value, 10))}
              className="w-full"
            />
          </Field>

          {/* 单机模式不显示"后端 API"配置(由 Tauri sidecar 注入,改了就连不上);
              其它形态(web / 瘦客户端 / 远端部署)继续保留入口。 */}
          {!singleMachine && (
            <Field label="后端 API">
              <div className="flex items-center gap-2">
                <Input
                  value={apiInput}
                  placeholder={defaultApi || '同源 / 留空使用默认'}
                  onChange={(e) => setApiInput(e.target.value)}
                />
                <Button size="sm" variant="outline" onClick={onApplyApi}>
                  应用
                </Button>
              </div>
              <p className="mt-1 text-[11px] text-muted-foreground">
                留空使用 {defaultApi || '同源（vite 反代）'}
              </p>
            </Field>
          )}

          {/* 数据目录信息 + 切换向导 — 桌面端都显示(不再只限单机模式)。
              数据库 / 索引 / 模型缓存都落在这个目录,用户需要能看到 + 切换。
              DataDirSwitchSection 内部再处理"读取中 / 不支持 / 失败"各态。 */}
          {platform.kind === 'desktop' && (
            <Field label="数据目录">
              <DataDirSwitchSection />
            </Field>
          )}

          <Field label="上传遥测（Langfuse）">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={settings.telemetry}
                onChange={(e) => settings.setTelemetry(e.target.checked)}
              />
              <span>允许将匿名 trace / 反馈上传到 Langfuse；关闭后仅本地暂存</span>
            </label>
          </Field>

          {/* 单机模式隐藏账号段(CLAUDE.md §3.4:不暴露登录概念) */}
          {!singleMachine && (
            <Field label="账号">
              {auth.user ? (
                <div className="flex items-center justify-between text-sm">
                  <span>
                    {auth.user.username}
                    <span className="ml-1 text-xs text-muted-foreground">({auth.user.role})</span>
                  </span>
                  <Button size="sm" variant="outline" onClick={() => void auth.logout()}>
                    退出登录
                  </Button>
                </div>
              ) : (
                <span className="text-sm text-muted-foreground">未登录（游客模式）</span>
              )}
            </Field>
          )}

          <Field label="应用">
            <div className="flex items-center justify-between text-sm">
              <span>
                {platform.runtime.appName} · {platform.runtime.appVersion}
                <span className="ml-1 text-xs text-muted-foreground">({platform.kind})</span>
              </span>
              {platform.updater && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={async () => {
                    const upd = await platform.updater!.check();
                    if (upd.available) {
                      // 走 platform.dialog.confirm,不要裸 confirm() — Tauri 2 webview
                      // 把 window.confirm 重定向到 plugin:dialog|confirm,某些 2.x 版
                      // 即使授权 dialog:allow-confirm 也会 ACL 拒。
                      const ok = await platform.dialog.confirm(
                        `发现新版本 ${upd.version}。立即安装?`,
                        { title: '更新' },
                      );
                      if (ok) {
                        await platform.updater!.install();
                      }
                    } else {
                      notifySuccess('已是最新版本');
                    }
                  }}
                >
                  检查更新
                </Button>
              )}
            </div>
          </Field>
        </section>
        )}

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={async () => {
              const ok = await platform.dialog.confirm('恢复默认设置?', { title: '恢复默认' });
              if (!ok) return;
              settings.reset();
              setApiInput('');
              configureClient({ baseURL: defaultApi });
            }}
          >
            <RotateCcw className="h-4 w-4" />
            恢复默认
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="space-y-1">
    <label className="block text-xs font-medium text-muted-foreground">{label}</label>
    {children}
  </div>
);

const THEME_OPTS: Array<{ value: ThemeMode; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { value: 'light', label: '亮色', icon: Sun },
  { value: 'dark', label: '暗色', icon: Moon },
  { value: 'system', label: '跟随系统', icon: Monitor },
];
