/**
 * 全局应用底部状态栏。
 *
 * 视觉:
 *   ┌─────────────────────────────────────────────────────────────────┐
 *   │ 北京智灵鸟科技中心出品 · aidooo.com   ●对话 ●嵌入 ●重排 ●ASR ●图 ●OCR │
 *   └─────────────────────────────────────────────────────────────────┘
 *
 * - 左:版权 + 主站链接
 * - 右:6 个本地能力运行状态点 + 中文 label
 *     · chat / embedding / rerank / asr / image-embedding — 来自 useLocalRuntimeStore.statuses
 *     · OCR — chayuan-server 进程内 onnxruntime,后端 reachable 即视为 OK
 *
 * 颜色规则:
 *   🟢 emerald  state=ready (运行中)
 *   🟡 amber    state=starting/stopping (过渡中)
 *   🔴 rose     state=failed 或 !reachable (异常)
 *   ⚫ neutral  state=stopped/null (未启动)
 */

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { cn } from '@chayuan/ui';
import type { LocalRuntimeCapability, RuntimeOcrHealth } from '@chayuan/api';
import { runtimeModels, runtimeOcr, serverCapabilityDefaults } from '@chayuan/api';
import { useLocalRuntimeStore } from '../../store/localRuntime';
import { HomeFooterMarquee } from '../home/HomeFooterMarquee';

interface CapabilityIndicator {
  key: LocalRuntimeCapability | 'ocr';
  label: string;
}

const INDICATORS: CapabilityIndicator[] = [
  { key: 'chat',             label: '对话' },
  { key: 'embedding',        label: '文本嵌入' },
  { key: 'rerank',           label: '重排' },
  { key: 'asr',              label: '语音识别' },
  // 图像向量化(image-embedding)已下线 —— 见 server: server_app.py、
  // local_runtime_registry.py 已注销其入口;底部状态点同步注销。
  // 要恢复:取消下行注释。
  // { key: 'image-embedding',  label: '图像嵌入' },
  { key: 'ocr',              label: 'OCR' },
];

// 三态(用户口径):
//   绿  ready       — 本地 sidecar 实测 ready 在跑
//   黄  configured  — 已配默认模型(本地装了 / 设置面板配了云端 default),本地未启动,不影响使用
//   红  unavailable — 既没本地服务跑着,又没配任何默认模型,完全不可用
// 另外两个过渡态保留:
//   pending  — starting/restarting,amber pulse
//   failed   — sidecar 报错(state==failed),rose 红 + 单独 hint
type Tone = 'ready' | 'pending' | 'failed' | 'configured' | 'unavailable' | 'missing';

const TONE_DOT: Record<Tone, string> = {
  ready:       'bg-emerald-500 shadow-[0_0_4px_rgba(16,185,129,0.55)]',
  pending:     'bg-amber-400  shadow-[0_0_4px_rgba(245,158,11,0.55)] animate-pulse',
  failed:      'bg-rose-500   shadow-[0_0_4px_rgba(244,63,94,0.55)]',
  configured:  'bg-amber-400  shadow-[0_0_4px_rgba(245,158,11,0.55)]',
  unavailable: 'bg-rose-500   shadow-[0_0_4px_rgba(244,63,94,0.55)]',
  missing:     'bg-zinc-300 ring-1 ring-zinc-400/70 ring-dashed',
};

const TONE_LABEL: Record<Tone, string> = {
  ready:       'text-emerald-700 dark:text-emerald-300',
  pending:     'text-amber-700  dark:text-amber-300',
  failed:      'text-rose-700   dark:text-rose-300',
  configured:  'text-amber-700  dark:text-amber-300',
  unavailable: 'text-rose-700   dark:text-rose-300',
  missing:     'text-zinc-400 dark:text-zinc-500 line-through decoration-zinc-400/60',
};

const TONE_HINT: Record<Tone, string> = {
  ready:       '运行中',
  pending:     '启停中…',
  failed:      '异常',
  configured:  '已配置可用,本地未启动',
  unavailable: '完全不可用 — 请去模型广场配置',
  missing:     '未随包(轻量版)',
};

/** indicator key → serverCapabilityDefaults 里对应的 cap 名。
 *  注意 image-embedding 对应后端 'clip',不是 'image-embedding'。 */
const DEFAULT_CAP_KEY: Partial<Record<LocalRuntimeCapability | 'ocr', string>> = {
  chat: 'chat',
  embedding: 'embedding',
  rerank: 'rerank',
  asr: 'asr',
  'image-embedding': 'clip',
  // ocr 没有云端默认概念,在 footer 走 ocrTone 单独算
};

/** 后端 install_info.bundled_caps 用的短名 ↔ footer indicator key 映射。
 *  后端用 'image' 表示图像嵌入,前端用 'image-embedding';其它一致。 */
const BACKEND_CAP_KEY: Record<LocalRuntimeCapability | 'ocr', string> = {
  chat: 'chat',
  embedding: 'embedding',
  rerank: 'rerank',
  asr: 'asr',
  'image-embedding': 'image',
  ocr: 'ocr',
};


export const AppFooter: React.FC = () => {
  const statuses = useLocalRuntimeStore((s) => s.statuses);
  const reachable = useLocalRuntimeStore((s) => s.reachable);
  const installInfo = useLocalRuntimeStore((s) => s.installInfo);
  const navigate = useNavigate();

  // 当前安装包随包嵌入的 cap 集合(后端 install_info.bundled_caps)。
  // 老服务端没返此字段 → 未知,所有 cap 都视为已随包(不打"未随包"标签),
  // 维持向后兼容。
  const bundledCaps = React.useMemo<Set<string> | null>(() => {
    if (!installInfo?.bundled_caps) return null;
    return new Set(installInfo.bundled_caps);
  }, [installInfo?.bundled_caps]);

  // 已配置(本地装了模型文件)的 cap 集合 — "配置即绿" 语义靠它。
  // 60s 拉一次 /runtime/models,按 capability 字段聚合;reachable=false 时清空避免误判。
  const [installedCaps, setInstalledCaps] = React.useState<Set<LocalRuntimeCapability>>(
    () => new Set(),
  );
  React.useEffect(() => {
    let cancelled = false;
    const pull = async () => {
      if (!reachable) {
        if (!cancelled) setInstalledCaps(new Set());
        return;
      }
      try {
        const { items } = await runtimeModels.list();
        const s = new Set<LocalRuntimeCapability>();
        for (const m of items) {
          // m.capability 是 string,narrow 到我们关心的 5 个
          const cap = m.capability as LocalRuntimeCapability;
          s.add(cap);
        }
        if (!cancelled) setInstalledCaps(s);
      } catch {
        // 接口报错不清状态,保留上次结果
      }
    };
    void pull();
    const t = window.setInterval(pull, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [reachable]);

  // 系统默认模型(设置面板 / 模型广场配置 → /admin/capability_defaults)。
  // 用 React Query 共享 ['serverCapabilityDefaults'] key:模型广场保存厂商时
  // (store/modelPlatform.ts applyPlatformWriteThrough)会 invalidate 这个 key,
  // 设置页 DefaultModelsSection + 本 footer 同步立即拿到新默认,不用等 60s 轮询。
  // 任何 cap 的 defaults[key] 非 null/空 → 视为"已配置可用",footer 显示黄色
  // (即使本地 sidecar 没启动,云端 OpenAI/百炼/智谱也能跑请求)。
  const { data: capDefaults } = useQuery({
    queryKey: ['serverCapabilityDefaults'],
    queryFn: () => serverCapabilityDefaults.list(),
    enabled: reachable,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const defaultsMap = capDefaults?.defaults ?? {};

  // OCR 健康检查:走 /runtime/ocr/health 真实探测(rapidocr pkg + ONNX 模型齐全)。
  // 30s 拉一次,后端 reachable 翻 false 时不重试避免狂打 404。
  const [ocrHealth, setOcrHealth] = React.useState<RuntimeOcrHealth | null>(null);
  React.useEffect(() => {
    let cancelled = false;
    const pull = async () => {
      if (!reachable) {
        if (!cancelled) setOcrHealth(null);
        return;
      }
      try {
        const h = await runtimeOcr.health();
        if (!cancelled) setOcrHealth(h);
      } catch {
        if (!cancelled) setOcrHealth({ state: 'failed', reason: 'OCR health endpoint 不可达' });
      }
    };
    void pull();
    const t = window.setInterval(pull, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [reachable]);

  // OCR 走独立 sidecar(rapidocr_server.py 跑 18380),没有云端 fallback —
  // 未就绪一律按"配置不完整"红色处理:sidecar 在 18380 监听 + rapidocr 包
  // 可 import + 模型齐才会 ready。后端 /runtime/ocr/health 真源。
  const ocrTone: Tone =
    !reachable ? 'failed'
    : ocrHealth?.state === 'ready' ? 'ready'
    : ocrHealth?.state === 'failed' ? 'failed'
    : 'unavailable';

  const onClickIndicator = (cap: LocalRuntimeCapability | 'ocr') => {
    // 跳到设置页的「本地模型服务」section,带 hash 锚到具体 capability row。
    // SettingsAsPage 会在 mount 时读 location.hash 并 scroll 到 #local-runtime-<cap>。
    void navigate({ to: '/settings', hash: `local-runtime-${cap}` });
  };

  return (
    <footer
      data-shell="footer"
      className={cn(
        'flex h-7 min-h-7 items-center justify-between gap-4 border-t px-3',
        'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]',
        'text-[11px] text-[var(--cy-text-tertiary)] select-none',
      )}
    >
      {/* 左:版权 + 主站 */}
      <div className="flex min-w-0 items-center gap-1.5 truncate">
        <span>北京智灵鸟科技中心 出品</span>
        <span aria-hidden="true" className="text-[var(--cy-text-tertiary)] opacity-60">·</span>
        <a
          href="https://aidooo.com"
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            'inline-flex items-center gap-0.5 font-medium',
            'text-[var(--cy-text-secondary)] hover:text-[var(--cy-text-primary)]',
            'transition-colors underline-offset-2 hover:underline',
          )}
        >
          aidooo.com
        </a>
      </div>

      {/* 右:6 个 capability 状态点 */}
      <div className="flex shrink-0 items-center gap-3">
        {INDICATORS.map((ind) => {
          // 三态优先级(按用户口径 "完全不可用=既没启动模型服务又没配默认模型"):
          //   1. 实际 ready → 绿 (运行中)
          //   2. 实际 starting/restarting → pending (启停中,amber pulse)
          //   3. 已配置(本地装了 / 设置面板/模型广场配了 default) → 黄
          //      — 即使本地 sidecar failed,只要有云端 default 就视为系统可用
          //   4. failed 且毫无 fallback → 红 (异常,sidecar 报错且没配默认)
          //   5. lite 包未随包 + 啥都没 → missing (灰斜线)
          //   6. 完全没配置 → unavailable (红 — 提示去模型广场)
          const backendCap = BACKEND_CAP_KEY[ind.key];
          const hasInstalled =
            ind.key !== 'ocr' && installedCaps.has(ind.key as LocalRuntimeCapability);
          const defaultCapKey = DEFAULT_CAP_KEY[ind.key];
          const hasCloudDefault =
            !!defaultCapKey && !!(defaultsMap[defaultCapKey] || '').toString().trim();
          const hasAnyConfig = hasInstalled || hasCloudDefault;
          const isBundleMissing =
            bundledCaps != null &&
            !bundledCaps.has(backendCap) &&
            ind.key !== 'ocr' &&
            !hasAnyConfig; // 任一手段可用就不再标 "未随包"

          let tone: Tone;
          if (ind.key === 'ocr') {
            tone = ocrTone;
          } else {
            const state = statuses[ind.key]?.state;
            // ready / starting / restarting 直接反映实时进程状态;
            // failed 让步给"有配置"判定 — 上游云端可走通时本地 sidecar 挂了不算系统不可用。
            if (state === 'ready') {
              tone = 'ready';
            } else if (state === 'starting' || state === 'restarting') {
              tone = 'pending';
            } else if (hasAnyConfig) {
              tone = 'configured';
            } else if (state === 'failed') {
              tone = 'failed';
            } else if (isBundleMissing) {
              tone = 'missing';
            } else {
              tone = 'unavailable';
            }
          }
          const hintOverride: string | null = null;

          const status =
            ind.key === 'ocr'
              ? (reachable ? { endpoint: '内嵌 (chayuan-server)' } : null)
              : statuses[ind.key];

          // 鼠标悬停 title:状态 + endpoint/model_id + 「点击跳设置」提示
          const titleParts: string[] = [`${ind.label}: ${hintOverride ?? TONE_HINT[tone]}`];
          // configured (黄) 时若本地 sidecar 实际 failed,提示用户本地还有问题但云端可走
          if (
            ind.key !== 'ocr' &&
            tone === 'configured' &&
            statuses[ind.key]?.state === 'failed'
          ) {
            titleParts.push('(本地 sidecar 启动失败,正走云端默认 — 不影响使用)');
            if (statuses[ind.key]?.last_error) {
              titleParts.push(`本地错误: ${statuses[ind.key]?.last_error}`);
            }
          }
          if (isBundleMissing) {
            titleParts.push('当前是轻量版安装包,该能力未随包嵌入。');
            titleParts.push('点击进设置 → 本地模型服务 → 下载模型后再启动。');
          }
          if (status && 'endpoint' in status && status.endpoint) {
            titleParts.push(String(status.endpoint));
          }
          if (ind.key !== 'ocr' && statuses[ind.key]?.model_id) {
            titleParts.push(`模型: ${statuses[ind.key]?.model_id}`);
          }
          if (ind.key === 'ocr' && ocrHealth?.reason) {
            titleParts.push(ocrHealth.reason);
          }
          if (!isBundleMissing) {
            titleParts.push('点击 → 设置页本地模型服务');
          }
          const title = titleParts.join('\n');

          return (
            <button
              type="button"
              key={ind.key}
              title={title}
              onClick={() => onClickIndicator(ind.key)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded px-1 py-0.5',
                '-mx-1 cursor-pointer transition-colors',
                'hover:bg-[var(--cy-surface-2)] focus-visible:outline-none',
                'focus-visible:ring-1 focus-visible:ring-[var(--cy-border-focus,#0ea5e9)]',
              )}
              aria-label={`${ind.label}: ${hintOverride ?? TONE_HINT[tone]},点击跳转到设置页本地模型服务`}
            >
              <span
                aria-hidden="true"
                className={cn('h-1.5 w-1.5 rounded-full transition-colors', TONE_DOT[tone])}
              />
              <span className={cn('whitespace-nowrap transition-colors', TONE_LABEL[tone])}>
                {ind.label}
              </span>
            </button>
          );
        })}
      </div>
    </footer>
  );
};

export default AppFooter;
