/**
 * 平台配置弹窗。
 *
 * 功能:
 *   - 表单字段:platform_type / api_base_url / api_key (mask) / api_proxy /
 *     api_concurrencies / enabled / auto_detect_model
 *   - 模型清单:llm_models 文本(每行一个);disabled_models(黑名单)
 *   - 「连通测试」:调 modelPlatform.test → 显示 reachable + detected 模型数;
 *     成功后可一键「填入检测到的模型」
 *   - 「保存」:PATCH 后**无需重启**,卡片立即转彩
 *   - 「删除」:DELETE 后,DB 行移除,yaml seed 仍可作为兜底
 */

import type {
  PlatformConfig,
  PlatformCreate,
  PlatformPatch,
  PlatformTestResult,
} from '@chayuan/api';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  Input,
  Switch,
  Textarea,
  cn,
} from '@chayuan/ui';
import { Eye, EyeOff, RefreshCw, Save, Sparkles, Trash2, Wand2, Wifi } from 'lucide-react';
import * as React from 'react';
import { useTranslation } from '../../../i18n';
import { notifyInfo, notifySuccess, reportError } from '../../../store/errorDialog';
import {
  useCreatePlatform,
  useDeletePlatform,
  useTestPlatform,
  useUpdatePlatform,
} from '../../../store/modelPlatform';
import { modelPlatform } from '@chayuan/api';
import { MODEL_TYPES, type ModelType } from '../lib/modelTypes';
import { EMPTY_MODELS_BY_TYPE, ModelTypeAccordion, type ModelsByType } from './ModelTypeAccordion';
import { AssistantBrandLogo } from '../../../components/AssistantBrandLogo';
import { getPlatform } from '@chayuan/platform-shared';

const PLATFORM_TYPES: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'custom openai', label: 'OpenAI 兼容(自定义)' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'oneapi', label: 'OneAPI' },
  { value: 'fastchat', label: 'FastChat' },
  { value: 'xinference', label: 'Xinference' },
  { value: 'ollama', label: 'Ollama' },
];

/** 本地推理后端 — 不需要 API Key 即可探测/调用 */
const KEYLESS_PLATFORM_TYPES: ReadonlySet<string> = new Set([
  'ollama',
  'xinference',
  'fastchat',
]);

function isKeylessType(t: string): boolean {
  return KEYLESS_PLATFORM_TYPES.has(t);
}

export interface PlatformSettingsDialogProps {
  /** 非 null 即打开弹窗 */
  platform: PlatformConfig | null;
  onOpenChange(open: boolean): void;
  /**
   * 平台是否已经持久化到 DB:
   *   - true(默认):dialog 走"已存在,PATCH 更新"的路径
   *   - false:dialog 来自 catalog "未配置" 卡片,保存/测试时要先 POST create
   * 后端的 /admin/model_platforms/{name}/test 只查 DB,所以新厂商必须先 create
   * 才能 test,否则会拿到 "platform 'xxx' not found"。
   */
  existsInDb?: boolean;
}

interface DraftState {
  platform_type: string;
  api_base_url: string;
  api_key: string;
  api_key_dirty: boolean; // 用户改过 key 才下发(否则 PATCH 跳过)
  api_proxy: string;
  api_concurrencies: number;
  enabled: boolean;
  auto_detect_model: boolean;
  /** 8 类模型清单 —— 取代旧的 llm_models_text 单 textarea */
  models: ModelsByType;
  disabled_models_text: string;
  description: string;
}

const MASK = '••••••••';

function platformToDraft(p: PlatformConfig): DraftState {
  const models: ModelsByType = { ...EMPTY_MODELS_BY_TYPE };
  for (const meta of MODEL_TYPES) {
    models[meta.key] = [...((p[meta.field] as string[] | undefined) ?? [])];
  }
  return {
    platform_type: p.platform_type,
    api_base_url: p.api_base_url,
    api_key: p.api_key ? MASK : '',
    api_key_dirty: false,
    api_proxy: p.api_proxy,
    api_concurrencies: p.api_concurrencies,
    enabled: p.enabled,
    auto_detect_model: p.auto_detect_model,
    models,
    disabled_models_text: (p.disabled_models ?? []).join('\n'),
    description: p.description ?? '',
  };
}

/** 判断 draft 与 platform 配置是否有差异(决定是否需要 PATCH) */
function isFieldDirty(p: PlatformConfig, d: DraftState): boolean {
  if (d.platform_type !== p.platform_type) return true;
  if (d.api_base_url.trim() !== p.api_base_url) return true;
  if (d.api_proxy.trim() !== p.api_proxy) return true;
  if (d.api_concurrencies !== p.api_concurrencies) return true;
  if (d.enabled !== p.enabled) return true;
  if (d.auto_detect_model !== p.auto_detect_model) return true;
  if ((d.description ?? '').trim() !== (p.description ?? '')) return true;
  return false;
}

/** 从 draft 拼出 create payload — name 必带,api_key 强制下发(create 用) */
function draftToCreate(name: string, d: DraftState): PlatformCreate {
  return {
    platform_name: name,
    ...draftToPatch(d),
    api_key: d.api_key, // create 必带 key,不依赖 api_key_dirty
  };
}

function draftToPatch(d: DraftState): PlatformPatch {
  const patch: PlatformPatch = {
    platform_type: d.platform_type,
    api_base_url: d.api_base_url.trim(),
    api_proxy: d.api_proxy.trim(),
    api_concurrencies: Number.isFinite(d.api_concurrencies) ? Math.max(1, d.api_concurrencies) : 5,
    enabled: d.enabled,
    auto_detect_model: d.auto_detect_model,
    disabled_models: d.disabled_models_text
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean),
    description: d.description.trim(),
  };
  // 8 类模型 → 后端 8 个数组字段，统一通过 MODEL_TYPES 派生
  for (const meta of MODEL_TYPES) {
    (patch as Record<string, unknown>)[meta.field] = d.models[meta.key] ?? [];
  }
  // 仅当用户改过 api_key 才下发,避免 mask 被回写
  if (d.api_key_dirty) patch.api_key = d.api_key;
  return patch;
}

export const PlatformSettingsDialog: React.FC<PlatformSettingsDialogProps> = ({
  platform,
  onOpenChange,
  existsInDb = true,
}) => {
  const { t } = useTranslation();
  const open = !!platform;
  const [draft, setDraft] = React.useState<DraftState | null>(null);
  const [showKey, setShowKey] = React.useState(false);
  const [testResult, setTestResult] = React.useState<PlatformTestResult | null>(null);
  /**
   * 跟踪本次 dialog 生命周期内"是否已 create 过"。
   * 初始 = props.existsInDb;一旦在 onTest/onSave 里 create 成功就翻 true,
   * 后续 onTest/onSave 走 update 分支,避免重复 create 报 409。
   */
  const [persisted, setPersisted] = React.useState<boolean>(existsInDb);

  const createMut = useCreatePlatform();
  const updateMut = useUpdatePlatform();
  const deleteMut = useDeletePlatform();
  const testMut = useTestPlatform();
  // 自动拉取(blur 触发)态:防止重复并发 + 给 input 显 loading spinner
  const [autoFetching, setAutoFetching] = React.useState(false);
  // 厂商简介 AI 流式生成态:isPending 期间禁用按钮 + 显示 loading;abortRef 用于关 dialog 时取消
  const [describing, setDescribing] = React.useState(false);
  const describeAbortRef = React.useRef<AbortController | null>(null);
  // 上次自动拉取用过的 api_key,避免同一个 key 重复 blur 反复 fetch
  const lastAutoKey = React.useRef<string>('');
  // 「默认开启」守卫:配好 API key + 模型后,自动把总开关打开**一次**,免得用户
  // 配完忘了开。之后用户手动关掉不会被翻回去;原本就 enabled、或原平台已是
  // 「有 key + 有模型」的可用态(其 enabled=false 可能是用户故意停用)都不自动碰。
  const autoEnabledRef = React.useRef(false);

  // 打开时把 platform 灌成 draft
  React.useEffect(() => {
    if (platform) {
      setDraft(platformToDraft(platform));
      setTestResult(null);
      setShowKey(false);
      setPersisted(existsInDb);
      autoEnabledRef.current = false;
    } else {
      setDraft(null);
    }
  }, [platform, existsInDb]);

  // 「默认开启」:draft 达到「有 API key + 模型清单非空」时,自动开总开关一次。
  React.useEffect(() => {
    if (!draft || autoEnabledRef.current) return;
    // 已开 / 原平台本就是可用态 → 不自动碰,仅置位守卫
    const platformWasUsable =
      !!platform &&
      !!platform.api_key &&
      MODEL_TYPES.some(
        (meta) => ((platform[meta.field] as string[] | undefined) ?? []).length > 0,
      );
    if (draft.enabled || platformWasUsable) {
      autoEnabledRef.current = true;
      return;
    }
    const hasKey = draft.api_key.trim().length > 0;
    const hasModels = MODEL_TYPES.some(
      (meta) => (draft.models[meta.key] ?? []).length > 0,
    );
    if (hasKey && hasModels) {
      autoEnabledRef.current = true;
      setDraft((d) => (d ? { ...d, enabled: true } : d));
    }
  }, [draft, platform]);

  const set = <K extends keyof DraftState>(k: K, v: DraftState[K]) => {
    setDraft((d) => (d ? { ...d, [k]: v } : d));
  };

  const onSave = async () => {
    if (!platform || !draft) return;
    try {
      if (!persisted) {
        if (!draft.api_key) {
          notifyInfo('请先填入 API Key', '新厂商至少需要 API Key 才能保存');
          return;
        }
        await createMut.mutateAsync(draftToCreate(platform.platform_name, draft));
        setPersisted(true);
      } else {
        await updateMut.mutateAsync({ name: platform.platform_name, patch: draftToPatch(draft) });
      }
      notifySuccess(`${platform.platform_name} 已保存,即时生效`);
      onOpenChange(false);
    } catch (e) {
      reportError(e, '保存失败');
    }
  };

  const onDelete = async () => {
    if (!platform) return;
    const ok = await getPlatform().dialog.confirm(
      `删除厂商「${platform.platform_name}」?(yaml seed 仍可作为兜底)`,
      { title: '删除厂商' },
    );
    if (!ok) return;
    try {
      await deleteMut.mutateAsync(platform.platform_name);
      notifySuccess(`${platform.platform_name} 已删除`);
      onOpenChange(false);
    } catch (e) {
      reportError(e, '删除失败');
    }
  };

  /**
   * 测试连通 — 后端 /admin/model_platforms/{name}/test 只查 DB 里的配置,
   * 所以必须先把当前 draft 同步到 DB 才能让测试拿到正确的 api_key/base。
   *
   * 流程:
   *   1) 未持久化(catalog "未配置" 卡)→ 必填 api_key,然后 create
   *   2) 已持久化但用户改过 api_key(api_key_dirty)/base/type → 先 PATCH update
   *   3) 否则直接 test
   *   4) 跑 test,把结果写到 testResult
   */
  const onTest = async () => {
    if (!platform || !draft) return;
    try {
      if (!persisted) {
        // 本地厂商(ollama / xinference / fastchat)不强制要求 API Key
        if (!draft.api_key && !isKeylessType(draft.platform_type)) {
          notifyInfo('请先填入 API Key', '新厂商必须先填 API Key 才能测试连通');
          return;
        }
        await createMut.mutateAsync(draftToCreate(platform.platform_name, draft));
        setPersisted(true);
        setDraft((d) => (d ? { ...d, api_key_dirty: false } : d));
      } else if (draft.api_key_dirty || isFieldDirty(platform, draft)) {
        await updateMut.mutateAsync({
          name: platform.platform_name,
          patch: draftToPatch(draft),
        });
        setDraft((d) => (d ? { ...d, api_key_dirty: false } : d));
      }
      const r = await testMut.mutateAsync(platform.platform_name);
      setTestResult(r);
    } catch (e) {
      reportError(e, '测试失败');
    }
  };

  /**
   * 模型清单右上"刷新"按钮 — 用户主动重新探测可用模型,不依赖 key blur 触发。
   * 对所有厂商可用(本地厂商无 key 也能跑);流程同 onTest 但额外把 detected
   * 直接合并进 draft.models,免去再点"一键填入"。
   */
  const onRefreshModels = async () => {
    if (!platform || !draft) return;
    if (autoFetching) return;
    setAutoFetching(true);
    try {
      if (!persisted) {
        if (!draft.api_key && !isKeylessType(draft.platform_type)) {
          notifyInfo('请先填入 API Key', '新厂商必须先填 API Key 才能拉取模型');
          return;
        }
        await createMut.mutateAsync(draftToCreate(platform.platform_name, draft));
        setPersisted(true);
        setDraft((d) => (d ? { ...d, api_key_dirty: false } : d));
      } else if (draft.api_key_dirty || isFieldDirty(platform, draft)) {
        await updateMut.mutateAsync({
          name: platform.platform_name,
          patch: draftToPatch(draft),
        });
        setDraft((d) => (d ? { ...d, api_key_dirty: false } : d));
      }
      const r = await testMut.mutateAsync(platform.platform_name);
      setTestResult(r);
      if (r.reachable) {
        setDraft((prev) => {
          if (!prev) return prev;
          const merged: ModelsByType = { ...prev.models };
          let added = 0;
          for (const meta of MODEL_TYPES) {
            const detected = (r.detected[meta.field] ?? []) as string[];
            if (!detected.length) continue;
            const before = new Set(prev.models[meta.key] ?? []);
            const next = [...new Set([...(prev.models[meta.key] ?? []), ...detected])];
            merged[meta.key] = next;
            added += next.filter((m) => !before.has(m)).length;
          }
          if (added === 0) {
            notifyInfo('未发现新模型', '当前模型清单已与服务端探测结果一致');
          } else {
            notifySuccess(`已刷新模型清单 — 新增 ${added} 个`);
          }
          return { ...prev, models: merged };
        });
      } else {
        reportError(
          new Error(r.error || '探测失败,请检查 base_url 与网络'),
          '刷新模型清单失败',
        );
      }
    } catch (e) {
      reportError(e, '刷新模型清单失败');
    } finally {
      setAutoFetching(false);
    }
  };

  /**
   * API Key blur 后自动拉取 + 归类模型(无需用户手点"测试连通"+"一键填入")。
   *
   * 触发条件:
   *   1) 用户改过 key(api_key_dirty)
   *   2) key 长度 >=8(粗判,过滤误触)
   *   3) 与上次 auto-fetch 的 key 不同(防同 key 多次 blur 重拉)
   *   4) 当前没有 in-flight 的 fetch
   *
   * 流程: 同 onTest 一致 — 未持久化先 create / 已持久化先 update → POST /test → autofill
   */
  const onAutoFetch = async () => {
    if (!platform || !draft) return;
    if (autoFetching) return;
    const key = draft.api_key.trim();
    if (!draft.api_key_dirty) return;
    if (!key || key.length < 8 || key === MASK) return;
    if (key === lastAutoKey.current) return;
    lastAutoKey.current = key;
    setAutoFetching(true);
    try {
      if (!persisted) {
        await createMut.mutateAsync(draftToCreate(platform.platform_name, draft));
        setPersisted(true);
        setDraft((d) => (d ? { ...d, api_key_dirty: false } : d));
      } else if (draft.api_key_dirty || isFieldDirty(platform, draft)) {
        await updateMut.mutateAsync({
          name: platform.platform_name,
          patch: draftToPatch(draft),
        });
        setDraft((d) => (d ? { ...d, api_key_dirty: false } : d));
      }
      const r = await testMut.mutateAsync(platform.platform_name);
      setTestResult(r);
      // 探测成功 → 直接合并到 draft.models;失败保持现状,让用户点测试看错因
      if (r.reachable) {
        setDraft((prev) => {
          if (!prev) return prev;
          const merged: ModelsByType = { ...prev.models };
          for (const meta of MODEL_TYPES) {
            const detected = (r.detected[meta.field] ?? []) as string[];
            if (!detected.length) continue;
            const before = merged[meta.key] ?? [];
            merged[meta.key] = [...new Set([...before, ...detected])];
          }
          return { ...prev, models: merged };
        });
        notifySuccess(`已自动拉取到 ${platform.platform_name} 的 ${countDetected(r)} 个模型`);
      }
    } catch (e) {
      // 失败时清掉 lastAutoKey 让用户改 key 后重试有效
      lastAutoKey.current = '';
      reportError(e, '自动拉取模型失败,请检查 base_url 与 api_key');
    } finally {
      setAutoFetching(false);
    }
  };

  /** 把本次 detected 全 8 类模型按类型并入 draft.models（去重保留旧条目） */
  const onAutoFill = () => {
    if (!testResult || !draft) return;
    const merged: ModelsByType = { ...draft.models };
    let total = 0;
    for (const meta of MODEL_TYPES) {
      const detected = (testResult.detected[meta.field] ?? []) as string[];
      if (!detected.length) continue;
      const before = merged[meta.key] ?? [];
      const next = [...new Set([...before, ...detected])];
      merged[meta.key] = next;
      total += next.length - before.length;
    }
    if (total === 0) {
      notifySuccess(t('modelMarket.settings.autoFillEmpty'));
      return;
    }
    set('models', merged);
    notifySuccess(t('modelMarket.settings.autoFillSuccess', { n: total }));
  };

  /**
   * AI 一键写厂商简介 —— SSE 流式接收 LLM token,逐字写入 ``draft.description``。
   *
   * 后端: ``POST /admin/model_platforms/{name}/describe/stream``,选一个已配齐 key
   * 的 LLM 厂商(skip 自身防循环依赖)生成中文 hero 副标题(2-3 句话,≤120 字)。
   *
   * 前提:厂商必须已落库(persisted=true)— 否则后端按 platform_name 查不到 display_name。
   *
   * 用户预期:点击 → 输入框开始打字 → 几秒钟内出现完整简介,可以再手编 → 点保存。
   */
  const onAiDescribe = async () => {
    if (!platform || !draft) return;
    if (!persisted) {
      notifyInfo('请先保存厂商', 'AI 生成简介需要厂商已落库,先保存再使用');
      return;
    }
    if (describing) return;
    // 同一个 dialog 重复点 → 取消上一次,接最新
    describeAbortRef.current?.abort();
    const ctl = new AbortController();
    describeAbortRef.current = ctl;
    setDescribing(true);
    // 清空原描述再开始流式,避免拼接到旧文案后面
    setDraft((d) => (d ? { ...d, description: '' } : d));
    try {
      await modelPlatform.describeStream(
        platform.platform_name,
        {
          onChunk: (text) => {
            setDraft((d) => (d ? { ...d, description: (d.description || '') + text } : d));
          },
          onDone: () => {
            notifySuccess('AI 已生成简介,可手动编辑后保存');
          },
        },
        { signal: ctl.signal },
      );
    } catch (e) {
      if ((e as { name?: string })?.name === 'AbortError' || ctl.signal.aborted) return;
      reportError(e, 'AI 生成简介失败 — 请确保已配置任意一个有 LLM 的厂商');
    } finally {
      setDescribing(false);
      if (describeAbortRef.current === ctl) describeAbortRef.current = null;
    }
  };

  // 关 dialog / 卸载 → 取消进行中的 SSE,避免后台继续累积写入已被丢弃的 draft
  React.useEffect(() => {
    if (!open) describeAbortRef.current?.abort();
  }, [open]);
  React.useEffect(() => () => describeAbortRef.current?.abort(), []);

  /** 把 detected 转成 ModelType -> count 给 accordion 显示"新增 N" 角标 */
  const detectedCounts = React.useMemo<Partial<Record<ModelType, number>>>(() => {
    if (!testResult || !draft) return {};
    const out: Partial<Record<ModelType, number>> = {};
    for (const meta of MODEL_TYPES) {
      const detected = (testResult.detected[meta.field] ?? []) as string[];
      if (!detected.length) continue;
      const before = new Set(draft.models[meta.key] ?? []);
      const newOnes = detected.filter((m) => !before.has(m));
      if (newOnes.length) out[meta.key] = newOnes.length;
    }
    return out;
  }, [testResult, draft]);

  /** 把后端注入的 ``builtin_models`` (key=PlatformConfig 字段名) 转成
   *  ``ModelType -> Set<string>``,给 ModelTypeAccordion 派发到对应 ModelLineEditor;
   *  命中 set 的行 = catalog 默认带的内置模型,UI 不渲染删除按钮。 */
  const builtinByType = React.useMemo<Partial<Record<ModelType, ReadonlySet<string>>>>(() => {
    const src = (platform?.builtin_models ?? {}) as Partial<Record<string, string[]>>;
    const out: Partial<Record<ModelType, ReadonlySet<string>>> = {};
    for (const meta of MODEL_TYPES) {
      const list = src[meta.field];
      if (list && list.length) out[meta.key] = new Set(list);
    }
    return out;
  }, [platform]);

  if (!platform || !draft) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex max-h-[88vh] w-[92vw] max-w-2xl flex-col gap-0 overflow-hidden p-0"
        hideClose
      >
        <header className="flex items-center justify-between border-b border-[var(--cy-border-subtle)] px-5 py-3">
          <div className="min-w-0">
            <DialogTitle asChild>
              <h2 className="truncate text-base font-semibold text-[var(--cy-text-primary)]">
                配置 {platform.platform_name}
              </h2>
            </DialogTitle>
            <DialogDescription className="text-[11px] text-[var(--cy-text-tertiary)]">
              保存后即时生效,无需重启服务
            </DialogDescription>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {/* 平台类型 */}
          <Field label="平台类型" hint="决定连通测试与模型探测策略">
            <select
              value={draft.platform_type}
              onChange={(e) => set('platform_type', e.target.value)}
              className="h-9 w-full rounded-md border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-3 text-sm focus-visible:border-[var(--cy-brand-400)] focus-visible:outline-none"
            >
              {PLATFORM_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Base URL" hint="OpenAI 兼容路径,例如 https://api.deepseek.com/v1">
            <Input
              value={draft.api_base_url}
              placeholder="https://api.deepseek.com/v1"
              onChange={(e) => set('api_base_url', e.target.value)}
            />
          </Field>

          <Field
            label="API Key"
            optional={isKeylessType(draft.platform_type)}
            hint={
              isKeylessType(draft.platform_type)
                ? '本地推理后端通常不需要 Key,留空即可'
                : autoFetching
                  ? '正在自动拉取模型清单…'
                  : '失焦后自动拉取并归类该厂商的可用模型'
            }
          >
            <div className="relative">
              <Input
                type={showKey ? 'text' : 'password'}
                value={draft.api_key}
                placeholder={isKeylessType(draft.platform_type) ? '可留空(本地后端无需 Key)' : 'sk-...'}
                onFocus={(e) => {
                  // 第一次聚焦时,如果是 mask,清空让用户重新输入
                  if (!draft.api_key_dirty && draft.api_key === MASK) {
                    set('api_key', '');
                    set('api_key_dirty', true);
                    requestAnimationFrame(() => e.target.select());
                  } else if (!draft.api_key_dirty) {
                    set('api_key_dirty', true);
                  }
                }}
                onChange={(e) => {
                  set('api_key', e.target.value);
                  if (!draft.api_key_dirty) set('api_key_dirty', true);
                }}
                onBlur={() => {
                  // 用户填了 key 后失焦 → 自动 create+test+autofill,无需手点按钮
                  void onAutoFetch();
                }}
                className="pr-9"
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)]"
                aria-label={showKey ? '隐藏' : '显示'}
              >
                {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
              {autoFetching && (
                <span className="absolute right-9 top-1/2 -translate-y-1/2 text-[var(--cy-text-tertiary)]">
                  <AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" />
                </span>
              )}
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="HTTP 代理(可选)">
              <Input
                value={draft.api_proxy}
                placeholder="http://127.0.0.1:7890"
                onChange={(e) => set('api_proxy', e.target.value)}
              />
            </Field>
            <Field label="单模型并发上限">
              <Input
                type="number"
                min={1}
                value={draft.api_concurrencies}
                onChange={(e) => set('api_concurrencies', Math.max(1, Number(e.target.value) || 1))}
              />
            </Field>
          </div>

          {/* 自动检测开关 + 测试连通按钮 — 同一行左右对称
              左:开关 + 标题 + 简短副标(单行,长则截断)
              右:[测试连通] 固定宽度,与左侧描述区视觉等量,不再让开关行独占整行
              测试结果 / 一键填入仍在下方独立块展示,不挤同一行 */}
          <div className="flex items-center gap-3 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2">
            <Switch
              checked={draft.auto_detect_model}
              onCheckedChange={(v) => set('auto_detect_model', v)}
            />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-[var(--cy-text-primary)]">自动检测可用模型</p>
              <p className="truncate text-[10px] text-[var(--cy-text-tertiary)]" title="启用后,服务侧周期性探测厂商可用模型清单(适合 ollama / xinference)">
                周期性探测(ollama / xinference)
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-8 shrink-0 px-3"
              onClick={onTest}
              disabled={testMut.isPending || createMut.isPending || updateMut.isPending}
            >
              {testMut.isPending || createMut.isPending || updateMut.isPending ? (
                <AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" />
              ) : (
                <Wifi className="h-3.5 w-3.5" />
              )}
              {!persisted ? '保存并测试' : '测试连通'}
            </Button>
          </div>

          {testResult && (
            <div
              className={cn(
                'rounded-md border p-3 text-xs',
                testResult.reachable
                  ? 'border-emerald-200 bg-emerald-50/60 text-emerald-800'
                  : 'border-red-200 bg-red-50/60 text-red-800',
              )}
            >
              <p className="font-medium">{testResult.reachable ? '✅ 连通正常' : '❌ 连通失败'}</p>
              {testResult.error && (
                <p className="mt-1 break-words text-[11px] opacity-80">{testResult.error}</p>
              )}
              {testResult.reachable && (
                <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px]">
                  {MODEL_TYPES.map((meta) => {
                    const arr = (testResult.detected[meta.field] ?? []) as string[];
                    if (!arr.length) return null;
                    return (
                      <span
                        key={meta.key}
                        className="inline-flex items-center gap-1 rounded-full bg-white/70 px-2 py-0.5 ring-1 ring-emerald-200"
                      >
                        <meta.icon className="h-3 w-3" />
                        {t(`${meta.i18nKey}.label`)}
                        <span className="font-medium tabular-nums">{arr.length}</span>
                      </span>
                    );
                  })}
                </div>
              )}
              {testResult.reachable && Object.keys(testResult.detected).length > 0 && (
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-2 h-7 text-xs"
                  onClick={onAutoFill}
                >
                  <Wand2 className="h-3 w-3" /> 一键填入检测到的模型
                </Button>
              )}
            </div>
          )}

          <Field
            label={t('modelMarket.settings.models.label')}
            hint={t('modelMarket.settings.models.hint')}
            action={
              <Button
                size="sm"
                variant="outline"
                className="h-7 px-2 text-[11px]"
                onClick={onRefreshModels}
                disabled={autoFetching}
                title={
                  isKeylessType(draft.platform_type)
                    ? '重新探测厂商可用模型(本地后端无需 Key)'
                    : '重新探测厂商可用模型(需先填好 base_url 和 API Key)'
                }
              >
                <RefreshCw className={cn('h-3 w-3', autoFetching && 'animate-spin')} />
                {autoFetching ? '刷新中…' : '刷新'}
              </Button>
            }
          >
            <ModelTypeAccordion
              value={draft.models}
              onChange={(next) => set('models', next)}
              detected={detectedCounts}
              builtin={builtinByType}
            />
          </Field>

          <Field
            label={t('modelMarket.settings.blacklist.label')}
            hint={t('modelMarket.settings.blacklist.hint')}
          >
            <Textarea
              rows={3}
              value={draft.disabled_models_text}
              onChange={(e) => set('disabled_models_text', e.target.value)}
              className="font-mono text-xs"
              placeholder={t('modelMarket.settings.blacklist.placeholder')}
            />
          </Field>

          {/* 厂商描述放在底部 — 旁边带 AI 生成按钮:点击后流式写入,可再手编 */}
          <Field label="厂商描述(hero / 卡片副标题)" optional>
            <div className="flex items-center gap-2">
              <Input
                value={draft.description}
                placeholder="混合推理架构,更高的思考效率"
                onChange={(e) => set('description', e.target.value)}
                disabled={describing}
              />
              <Button
                size="sm"
                variant="outline"
                onClick={onAiDescribe}
                disabled={describing || !persisted}
                title={
                  persisted
                    ? '用 LLM 流式生成简介(2-3 句话,可再编辑)'
                    : '先保存厂商再用 AI 生成简介'
                }
              >
                {describing ? (
                  <AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                AI 生成
              </Button>
            </div>
          </Field>
        </div>

        <footer className="flex items-center justify-between border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-5 py-3">
          {persisted ? (
            <Button
              size="sm"
              variant="ghost"
              className="text-destructive"
              onClick={onDelete}
              disabled={deleteMut.isPending}
            >
              <Trash2 className="h-3.5 w-3.5" /> 删除
            </Button>
          ) : (
            <span className="text-[11px] text-[var(--cy-text-tertiary)]">
              新厂商 · 保存后可测试
            </span>
          )}
          <div className="flex items-center gap-2">
            {/* 测试连通已移到「自动检测」开关同行右侧,这里页脚只保留「取消 / 总开关 / 保存」 */}
            <Button size="sm" variant="ghost" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            {/* 总开关 — 紧挨「保存」前:配好 API key + 模型后会自动打开,保存即生效 */}
            <span className="text-[11px] text-[var(--cy-text-tertiary)]">总开关</span>
            <Switch checked={draft.enabled} onCheckedChange={(v) => set('enabled', v)} />
            <Button
              size="sm"
              onClick={onSave}
              disabled={updateMut.isPending || createMut.isPending}
            >
              {updateMut.isPending || createMut.isPending ? (
                <AssistantBrandLogo running className="h-3.5 w-3.5 rounded-full" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              保存(立即生效)
            </Button>
          </div>
        </footer>
      </DialogContent>
    </Dialog>
  );
};

function countDetected(r: PlatformTestResult): number {
  let n = 0;
  for (const meta of MODEL_TYPES) {
    const arr = (r.detected[meta.field] ?? []) as string[];
    n += arr.length;
  }
  return n;
}

const Field: React.FC<{
  label: string;
  hint?: string;
  optional?: boolean;
  /** 字段标题右侧操作槽(如"刷新"按钮) — 与 hint 同位置,优先 action,有 hint 则降到下一行 */
  action?: React.ReactNode;
  children: React.ReactNode;
}> = ({ label, hint, optional, action, children }) => (
  <div className="space-y-1">
    <div className="flex items-baseline justify-between gap-2">
      <label className="text-xs font-medium text-[var(--cy-text-secondary)]">
        {label}
        {optional && (
          <span className="ml-1 text-[10px] text-[var(--cy-text-tertiary)]">(可选)</span>
        )}
      </label>
      <div className="flex items-center gap-2">
        {hint && <span className="text-[10px] text-[var(--cy-text-tertiary)]">{hint}</span>}
        {action}
      </div>
    </div>
    {children}
  </div>
);
