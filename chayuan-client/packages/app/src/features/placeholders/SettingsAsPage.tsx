/**
 * 设置页(参考图"设置")。
 *
 * 视觉:**不渲染顶部「系统设置」标题、不渲染各分组的 label**,
 * 只保留卡片本身和卡片内的逐行内容(用户在 2026-05-09 反馈中明确要求
 * 「这些列的名称都应该去掉」)。
 *
 * 结构(从上到下,每张卡都是无 label 的圆角容器):
 *
 *   1. 基础(主题 / 字号 / 语言)
 *   2. 本地模型服务(LocalRuntimeServicesSection — chat/embedding/rerank/asr/image-embedding 启停)
 *   3. 默认模型(聊天 / 文本嵌入 / 图像嵌入 / 重排)
 *   4. 知识中心(添加结构化数据库)
 *   5. 快捷工具(AI 键)
 *   6. 高级(本地模型对话 / 设备调优 / 教学)
 *   7. 关于(应用名 + 版本 + 检测更新)
 *
 * 与老实现的差异:
 *   - 删掉 ``<h1>{settings.title}</h1>`` 顶部大标题
 *   - 删掉 「个人设置」整个分组(单机版无登录概念,只有「本地用户」一行没意义)
 *   - 删掉 「更换声音」行(无后端能力,空按钮误导)
 *   - Section 的 ``label`` 不再渲染 ``<h2>``,但卡片视觉保留(圆角 + 边框)
 *   - 2026-05-16:删除「后端服务」「上传遥测」两行(单机版无业务价值),
 *     腾出位置插入「本地模型服务」分组
 */

import {
  type CapabilityDefaultsCandidate,
  type CapabilityDefaultsResponse,
  serverCapabilityDefaults,
} from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import { Button, Pill, Slider, Switch, cn } from '@chayuan/ui';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { Cpu, Monitor, Moon, RotateCcw, Sun } from 'lucide-react';
import * as React from 'react';
import { SUPPORTED_LOCALES, useTranslation } from '../../i18n';
import { notifySuccess, reportError } from '../../store/errorDialog';
import { type LocalePreference, type ThemeMode, useSettingsStore } from '../../store/settings';
import { LocalRuntimeServicesSection } from '../aiPlatform';
import { DataDirSwitchSection } from '../first-run/DataDirSwitchSection';

export const SettingsAsPage: React.FC = () => {
  const { t } = useTranslation();
  const settings = useSettingsStore();
  const platform = getPlatform();

  const [localModel, setLocalModel] = React.useState(false);
  const [deviceOpt, setDeviceOpt] = React.useState(true);
  const [teachMe, setTeachMe] = React.useState(true);

  // 处理 footer 状态灯跳转过来的 hash(#local-runtime-<cap>):
  // 先等本地模型 section 渲染完成,再 scrollIntoView。空轮询 ~10 次 (~500ms)
  // 足以覆盖 LocalRuntimeServicesSection 等异步数据填充后再挂载子 row 的场景。
  React.useEffect(() => {
    if (typeof window === 'undefined') return;
    const hash = window.location.hash.replace(/^#/, '');
    if (!hash.startsWith('local-runtime-')) return;
    let tries = 0;
    const tick = () => {
      const el = document.getElementById(hash);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // 给个轻量视觉强调,让用户清楚到底跳到哪一行
        el.classList.add('ring-2', 'ring-emerald-400', 'ring-offset-2');
        window.setTimeout(() => {
          el.classList.remove('ring-2', 'ring-emerald-400', 'ring-offset-2');
        }, 1600);
        return;
      }
      if (++tries < 10) window.setTimeout(tick, 50);
    };
    tick();
  }, []);

  return (
    <div className="mx-auto max-w-2xl px-6 py-8">
      {/* 基础 */}
      <Section>
        <Row
          title={t('settings.theme')}
          trailing={
            <div className="flex gap-1">
              {THEME_OPTS.map((o) => {
                const Icon = o.icon;
                const active = settings.theme === o.value;
                const labelKey =
                  o.value === 'light'
                    ? 'settings.themeLight'
                    : o.value === 'dark'
                      ? 'settings.themeDark'
                      : 'settings.themeSystem';
                return (
                  <Pill
                    key={o.value}
                    active={active}
                    tone={active ? 'ink' : 'outline'}
                    size="sm"
                    onClick={() => settings.setTheme(o.value)}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {t(labelKey)}
                  </Pill>
                );
              })}
            </div>
          }
        />
        <Row
          title={`${t('settings.fontSize')} (${settings.fontSize}px)`}
          trailing={
            <div className="w-40">
              <Slider
                min={12}
                max={22}
                step={1}
                value={[settings.fontSize]}
                onValueChange={([v]) => v !== undefined && settings.setFontSize(v)}
              />
            </div>
          }
        />
        <Row
          title={t('settings.language')}
          trailing={
            <select
              value={settings.locale}
              onChange={(e) => settings.setLocale(e.target.value as LocalePreference)}
              className={cn(
                'rounded-md border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-2 py-1 text-sm',
              )}
            >
              <option value="system">{t('language.system')}</option>
              {SUPPORTED_LOCALES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          }
        />
      </Section>

      {/* 本地模型服务(spec: 2026-05-16) */}
      <LocalRuntimeServicesSection />

      {/* 默认模型 — 模型广场配好厂商后,这里挑哪一个用做默认 */}
      <DefaultModelsSection />

      {/* AI 键已下线(2026-05-24);触发链路待重做,不再在设置页暴露 */}

      {/* 高级(M4 占位 — 后端缺对应能力,前端开关只持久化本地偏好) */}
      <Section>
        <Row
          title="显示底部状态栏"
          description="主界面底部显示版权 + 6 个本地模型能力的运行状态点;关闭后腾出 28px 给主区"
          trailing={
            <Switch
              checked={settings.showAppFooter}
              onCheckedChange={(b) => settings.setShowAppFooter(b)}
            />
          }
        />
        <Row
          title={t('settings.localModel')}
          description={t('settings.localModelDesc')}
          trailing={<Switch checked={localModel} onCheckedChange={setLocalModel} />}
        />
        <Row
          title={t('settings.deviceOptimize')}
          description={t('settings.deviceOptimizeDesc')}
          trailing={<Switch checked={deviceOpt} onCheckedChange={setDeviceOpt} />}
        />
        <Row
          title={t('settings.teachMe')}
          description={t('settings.teachMeDesc')}
          trailing={<Switch checked={teachMe} onCheckedChange={setTeachMe} />}
        />
      </Section>

      {/* 数据目录 — 桌面端显示当前数据目录 + 切换向导。
          数据库 / 向量索引 / 上传文件 / 模型缓存都存在这个目录下。 */}
      {platform.kind === 'desktop' && (
        <Section>
          <Row title="数据目录" description="数据库、向量索引、上传文件、模型缓存都存放在这里">
            <div className="mt-3">
              <DataDirSwitchSection />
            </div>
          </Row>
        </Section>
      )}

      {/* 关于 */}
      <Section>
        <Row
          title={`${platform.runtime.appName} · ${platform.runtime.appVersion}`}
          description={platform.kind}
          trailing={
            platform.updater && (
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  // biome-ignore lint/style/noNonNullAssertion: 上面 platform.updater 真值守卫已确保非空
                  const upd = await platform.updater!.check();
                  if (upd.available) {
                    // 走 platform.dialog.confirm — 详见 ImageToTextPage 同处注释
                    const ok = await platform.dialog.confirm(
                      `发现新版本 ${upd.version}。立即安装?`,
                      { title: '更新' },
                    );
                    if (ok) {
                      // biome-ignore lint/style/noNonNullAssertion: 同上
                      await platform.updater!.install();
                    }
                  } else {
                    notifySuccess('已是最新版本');
                  }
                }}
              >
                {t('settings.checkUpdate')}
              </Button>
            )
          }
        />
      </Section>

      <div className="mt-8 flex justify-end">
        <Button
          variant="ghost"
          size="sm"
          onClick={async () => {
            const ok = await platform.dialog.confirm('恢复默认设置?', { title: '恢复默认' });
            if (!ok) return;
            settings.reset();
          }}
        >
          <RotateCcw className="h-4 w-4" />
          恢复默认
        </Button>
      </div>
    </div>
  );
};

/**
 * 设置页分组容器 — **不渲染 ``<h2>`` 标题**(用户要求)。
 * 仅保留圆角边框 + 浅色背景,把内部 Row 视觉成一组。
 */
const Section: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <section className="mt-6 first:mt-0">
    <div className="overflow-hidden rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]">
      {children}
    </div>
  </section>
);

const Row: React.FC<{
  title: string;
  description?: string;
  trailing?: React.ReactNode;
  children?: React.ReactNode;
}> = ({ title, description, trailing, children }) => (
  <div className="flex items-start gap-4 border-b border-[var(--cy-border-subtle)] px-4 py-3 last:border-b-0">
    <div className="min-w-0 flex-1">
      <p className="text-sm font-medium text-[var(--cy-text-primary)]">{title}</p>
      {description && (
        <p className="mt-0.5 text-xs text-[var(--cy-text-tertiary)]">{description}</p>
      )}
      {children}
    </div>
    {trailing && <div className="flex-shrink-0">{trailing}</div>}
  </div>
);

const THEME_OPTS: Array<{
  value: ThemeMode;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { value: 'light', icon: Sun },
  { value: 'dark', icon: Moon },
  { value: 'system', icon: Monitor },
];

// 设置页"默认模型"分组 — 与模型广场保持单向数据流:
// 广场配好厂商/模型 → list() 候选刷新 → 这里挑选 → setMany() 写盘 → 业务读真源。
//
// 显示哪几类:聊天 / 文本嵌入 / 重排 三类高频。
// 图像嵌入(image-embedding / clip)已下线(唯一依赖 PyTorch 的 sidecar 退役,
// 图像功能改走文档库 OCR + 文本嵌入),不再在设置页暴露。其它(t2i/t2v/tts/asr/ocr)
// 留在 RuntimeCenter 全量面板。
const VISIBLE_CAPS: Array<{ cap: string; label: string; hint: string }> = [
  { cap: 'chat', label: '聊天模型', hint: '用于问答 / 写作 / 多轮对话' },
  { cap: 'embedding', label: '文本向量模型', hint: '用于知识库索引和语义检索' },
  { cap: 'rerank', label: '结果重排模型', hint: '检索后再排一次,让最相关的结果排到前面' },
];

/** 候选 model id 短显:本地 scanner 路径(models/bundled/chat/Qwen3-...-GGUF)
 *  只取最后一段 + 去 -GGUF 后缀;云厂商裸 id (deepseek-v4-flash) 原样。 */
function shortModelLabel(id: string): string {
  if (!id.includes('/')) return id;
  const tail = id.split('/').filter(Boolean).pop() ?? id;
  return tail.replace(/-(gguf|GGUF)$/, '');
}

function fmtSizeMB(bytes: number): string {
  if (!bytes || bytes <= 0) return '';
  const mb = bytes / 1024 / 1024;
  if (mb < 1024) return `${mb.toFixed(0)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

/** platform_name 归一到 group key + 显示 label。本地 sidecar(local / local-*)
 *  归一到「本地模型」;云厂商按 platform_name 各自一组。platform_name 缺失 →「其它」。 */
function platformGroupKeyForSettings(p: string | null | undefined): { key: string; label: string } {
  if (!p) return { key: '__other__', label: '其它' };
  if (p === 'local' || p.startsWith('local-')) {
    return { key: '__local__', label: '本地模型' };
  }
  return { key: p, label: p };
}

function groupCandidatesByPlatform(
  candidates: CapabilityDefaultsCandidate[],
): Array<{ label: string; items: CapabilityDefaultsCandidate[] }> {
  const map = new Map<string, { label: string; items: CapabilityDefaultsCandidate[] }>();
  for (const c of candidates) {
    // local_index 来源(scanner 直出)兜底走「本地模型」组,即使后端忘了带 platform_name
    const effectivePlatform =
      c.platform_name ?? (c.source === 'local_index' ? 'local' : undefined);
    const { key, label } = platformGroupKeyForSettings(effectivePlatform);
    const entry = map.get(key) ?? { label, items: [] };
    entry.items.push(c);
    map.set(key, entry);
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => {
      if (a === '__local__') return -1;
      if (b === '__local__') return 1;
      if (a === '__other__') return 1;
      if (b === '__other__') return -1;
      return a.localeCompare(b);
    })
    .map(([, v]) => ({
      label: v.label,
      items: v.items.slice().sort((x, y) => x.id.localeCompare(y.id)),
    }));
}

const DefaultModelsSection: React.FC = () => {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery<CapabilityDefaultsResponse>({
    queryKey: ['serverCapabilityDefaults'],
    queryFn: () => serverCapabilityDefaults.list(),
    staleTime: 30_000,
  });

  const onPick = React.useCallback(
    async (cap: string, modelId: string) => {
      try {
        const res = await serverCapabilityDefaults.setMany({ [cap]: modelId });
        const r = res.results?.[cap];
        if (r?.ok) {
          notifySuccess(
            '已设为默认',
            `${VISIBLE_CAPS.find((c) => c.cap === cap)?.label ?? cap} → ${modelId}`,
          );
          await qc.invalidateQueries({ queryKey: ['serverCapabilityDefaults'] });
        } else {
          reportError(new Error(r?.error || '保存失败'), '保存默认模型失败');
        }
      } catch (e) {
        reportError(e, '保存默认模型失败');
      }
    },
    [qc],
  );

  return (
    <Section>
      {error ? (
        <Row
          title="无法读取默认模型"
          description={`后端服务可能未就绪;${(error as Error)?.message ?? error}`}
        />
      ) : isLoading || !data ? (
        <Row title="加载中…" description="正在读取已配置的厂商和模型清单" />
      ) : (
        VISIBLE_CAPS.map(({ cap, label, hint }) => {
          const candidates = data.candidates[cap] ?? [];
          const current = data.defaults[cap] ?? '';
          if (candidates.length === 0) {
            return (
              <Row
                key={cap}
                title={label}
                description={`${hint} · 暂无可选模型`}
                trailing={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void navigate({ to: '/marketplace' })}
                  >
                    <Cpu className="h-3.5 w-3.5" />
                    去模型广场
                  </Button>
                }
              />
            );
          }
          // 按 platform_name 分组渲染 <optgroup>:本地 sidecar(local / local-<cap>)
          // 归一到「本地模型」首位,云厂商(deepseek / qwen / openrouter / ...)各自一组按字母序。
          // 本地路径式 id 短化显示,云厂商 id 原样。
          const groups = groupCandidatesByPlatform(candidates);
          return (
            <Row
              key={cap}
              title={label}
              description={hint}
              trailing={
                <select
                  value={current}
                  onChange={(e) => void onPick(cap, e.target.value)}
                  className={cn(
                    'min-w-[180px] max-w-[260px] rounded-md border border-[var(--cy-border-default)]',
                    'bg-[var(--cy-surface-base)] px-2 py-1 text-sm',
                  )}
                  data-testid={`settings-default-${cap}`}
                >
                  {!current && (
                    <option value="" disabled>
                      请选择
                    </option>
                  )}
                  {groups.map((g) => (
                    <optgroup key={g.label} label={g.label}>
                      {g.items.map((c) => (
                        <option key={c.id} value={c.id}>
                          {shortModelLabel(c.id)}
                          {c.size_bytes ? ` · ${fmtSizeMB(c.size_bytes)}` : ''}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
              }
            />
          );
        })
      )}
    </Section>
  );
};
