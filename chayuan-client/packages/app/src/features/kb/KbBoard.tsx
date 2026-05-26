/**
 * 知识库工作台 (/kb) — Phase 2 重写,数据源切到 /knowledge_universe/list。
 *
 * UE 状态机:
 *   PICKER          chatStarted=false → 主区=统一卡片,无 rail(默认)
 *   PICKER+OVERLAY  chatStarted=true && pickerOpen=true → picker overlay,按 X 收回
 *   THREAD+RAIL     chatStarted=true && pickerOpen=false → Thread + 右侧 chip 栏
 *
 * 卡片行为:
 *   - 点卡片主体 → 打开 KbDetailDialog(by ku_id)
 *   - 点左上角 checkbox → 多选 toggle(写 selectedKuIds)
 *   - kind 决定卡片图标 / 配色 / 渐变 / 计数文案
 *
 * 多选:
 *   - selectedKuIds 是 Phase 2 引入的统一选中态(string[],形如 'doc:foo' / 'src:42')
 *   - composer.setKuIds 同步把 doc:* 映射回 selectedKbs(kb_name 列表)兼容旧 chat backend
 *   - Phase 3 起 ChatComposer 直接走 universe.ask 用 ku_ids 分发
 */

import * as React from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  Boxes,
  CheckCheck,
  Database,
  Edit3,
  FileText,
  FileUp,
  Folder,
  ImageIcon,
  ImageUp,
  Images,
  LayoutGrid,
  Library,
  List as ListIcon,
  Loader2,
  Lock,
  NotebookPen,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X as XIcon,
} from 'lucide-react';
import {
  cn,
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@chayuan/ui';
import { getPlatform, uuid } from '@chayuan/platform-shared';
import { annotationApi, kb as kbApi, knowledgeSource, knowledgeUniverse, type AskResponse, type KuDetail, type KuItem, type KuKind } from '@chayuan/api';
import { MessageSquarePlus, LogOut } from 'lucide-react';
import { reportError } from '../../store/errorDialog';
import { KbAskResultPanel } from './KbAskResultPanel';
import { useComposerStore } from '../../store/composer';
import { useTabsStore } from '../../store/tabs';
import { useAuthStore } from '../../store/auth';
import { useKbBoardPrefs, type KbFilter, type KbLocalGroup, type KbSort, type KbView } from '../../store/kbBoardPrefs';
import { useTranslation } from '../../i18n';
import { ChatComposer } from '../composer/ChatComposer';
import { Thread } from '../chat/Thread';
import { useChayuanChat } from '../chat/useChayuanChat';
import { buildChatRequest } from '../chat/buildRequest';
import { notifyInfo } from '../../store/errorDialog';
import {
  upsertConversation,
  appendMessage,
} from '../conversations/persistence';
import { CONVERSATION_KEYS } from '../conversations/useConversations';
import { AddStructuredSourceDialog } from './AddStructuredSourceDialog';
import { KbDetailDialog } from './KbDetailDialog';
import { useKbImageSearch } from './shared/useKbImageSearch';
import { KbDropZone } from './upload/KbDropZone';
import { KbProgressBar } from './components/KbProgressBar';
import { CreateKbDialog } from './create/CreateKbDialog';
import { KbCard } from './KbCard';
import { EditKbInfoDialog } from './KbAdminMenu';
import { sourceIdFromKuId } from './SchemaScopePicker';
import { UploadTargetDialog } from '../notes/UploadTargetDialog';

// ──────────────────────────────────────────────────────────────
// kind 显示元数据
// ──────────────────────────────────────────────────────────────

interface KindMeta {
  icon: React.ComponentType<{ className?: string }>;
  /** 卡片 header 渐变(tailwind class)*/
  gradient: string;
  /** 列表行 icon 背景渐变 */
  listTint: string;
  label: string;
  unit: string;
}

const KIND_META: Record<KuKind, KindMeta> = {
  document: {
    icon: FileText,
    gradient: 'from-sky-500 to-indigo-600',
    listTint: 'from-sky-500 to-indigo-600',
    label: '文档',
    unit: '文件',
  },
  image: {
    icon: Images,
    gradient: 'from-pink-500 to-rose-600',
    listTint: 'from-pink-500 to-rose-600',
    label: '图像',
    unit: '图片',
  },
  structured: {
    icon: Database,
    gradient: 'from-emerald-500 to-teal-600',
    listTint: 'from-emerald-500 to-teal-600',
    label: '数据库',
    unit: '表',
  },
  vector: {
    icon: Boxes,
    gradient: 'from-amber-500 to-orange-600',
    listTint: 'from-amber-500 to-orange-600',
    label: '向量',
    unit: '集合',
  },
};

type ViewMode = KbView;

// ──────────────────────────────────────────────────────────────
// 主组件
// ──────────────────────────────────────────────────────────────

export const KbBoard: React.FC = () => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const activeConversationId = useComposerStore((s) => s.activeConversationId);
  const setActive = useComposerStore((s) => s.setActive);
  const modelId = useComposerStore((s) => s.modelId);
  const rewriteStrategy = useComposerStore((s) => s.rewriteStrategy);
  const searchMode = useComposerStore((s) => s.searchMode);
  const topK = useComposerStore((s) => s.topK);
  const selectedKuIds = useComposerStore((s) => s.selectedKuIds);
  const setKuIds = useComposerStore((s) => s.setKuIds);
  const userId = useAuthStore((s) => s.user?.id);

  const filter = useKbBoardPrefs((s) => s.filter);
  const setFilter = useKbBoardPrefs((s) => s.setFilter);
  const sort = useKbBoardPrefs((s) => s.sort);
  const setSort = useKbBoardPrefs((s) => s.setSort);
  const view = useKbBoardPrefs((s) => s.view);
  const setView = useKbBoardPrefs((s) => s.setView);
  const groups = useKbBoardPrefs((s) => s.groups);
  const groupByKuId = useKbBoardPrefs((s) => s.groupByKuId);
  const activeGroupId = useKbBoardPrefs((s) => s.activeGroupId);
  const setActiveGroup = useKbBoardPrefs((s) => s.setActiveGroup);
  const createGroup = useKbBoardPrefs((s) => s.createGroup);
  const assignToGroup = useKbBoardPrefs((s) => s.assignToGroup);
  const deleteGroup = useKbBoardPrefs((s) => s.deleteGroup);
  const [keyword, setKeyword] = React.useState('');
  // 类型筛选 chip:'' 表示全部 kind
  const [kindFilter, setKindFilter] = React.useState<KuKind | ''>('');
  const [addStructuredOpen, setAddStructuredOpen] = React.useState(false);
  // 新建知识库对话框:open + 默认 kind(从 Header 哪个按钮触发决定)
  const [createOpen, setCreateOpen] = React.useState<{ kind?: KuKind } | null>(null);
  const [chatStarted, setChatStarted] = React.useState(false);
  const [pickerOpen, setPickerOpen] = React.useState(true);
  // 详情 dialog 当前 ku_id(legacy fallback;主路径走独立 Tab)
  const [detailKuId, setDetailKuId] = React.useState<string | null>(null);
  const openTab = useTabsStore((s) => s.open);
  // 跨 KB ask 结果(Phase 3 新增):非 null 时主区渲染 KbAskResultPanel 而不是 Thread
  const [askQuery, setAskQuery] = React.useState<string>('');
  const [askResult, setAskResult] = React.useState<AskResponse | null>(null);
  // 提交时锁定的 ku_ids 快照(避免用户在 pending 中改选影响骨架渲染)
  const [askKuIds, setAskKuIds] = React.useState<string[]>([]);
  // 综合总结状态(deepseek/千问 风格:LLM 综合所有 block 写一段答案 + [N] 引用,
  // 默认折叠源面板,只展示最终答案)
  const [askSummary, setAskSummary] = React.useState<{
    text: string;
    pending: boolean;
    error?: string;
    evidenceCount?: number;
  }>({ text: '', pending: false });
  // 跨 KB 以图搜库:state + 进度
  const kbImgSearch = useKbImageSearch();
  const [contextMenu, setContextMenu] = React.useState<KbContextMenuState>(null);
  const [editKu, setEditKu] = React.useState<KuItem | null>(null);
  const [uploadTarget, setUploadTarget] = React.useState<null | { kind: 'document' | 'image' }>(null);

  const list = useQuery<KuItem[]>({
    queryKey: ['ku.list'],
    queryFn: () => knowledgeUniverse.list(),
    staleTime: 60 * 1000,
  });

  // 一次性清理:只移除后端已经不返回的 ku_id,保留 vector 项。
  React.useEffect(() => {
    const arr = list.data;
    if (!arr) return;
    const valid = new Set(arr.map((k) => k.ku_id));
    const cleaned = selectedKuIds.filter((id) => valid.has(id));
    if (cleaned.length !== selectedKuIds.length) {
      setKuIds(cleaned);
    }
  }, [list.data, selectedKuIds, setKuIds]);

  /**
   * 点 KB 卡片 → 在 TabBar 顶部新开 / 复用一个 Tab,而不是弹 Dialog。
   * Tab 路径 /kb/$kuId,page-registry 解析后渲染 KbDetailPage。
   * 标题用 KB display_name 让 TabBar 上一眼能区分。
   */
  const onOpenKb = React.useCallback(
    (kuId: string) => {
      const item = (list.data ?? []).find((k) => k.ku_id === kuId);
      const title = item?.display_name || item?.name || '知识库';
      openTab(`/kb/${encodeURIComponent(kuId)}`, { title, icon: 'library' });
    },
    [openTab, list.data],
  );

  const goToAiNote = () => {
    openTab('/notes/new', { title: 'AI 笔记', icon: 'pen' });
  };

  const onUploadTargetPick = (kuId: string) => {
    const path = `/kb/${encodeURIComponent(kuId)}`;
    const item = (list.data ?? []).find((k) => k.ku_id === kuId);
    const title = item?.display_name || item?.name || kuId;
    openTab(path, { title, icon: 'library' });
    setUploadTarget(null);
  };

  const onUploadTargetCreate = (kind: 'document' | 'image') => {
    setUploadTarget(null);
    setCreateOpen({ kind });
  };

  const items = React.useMemo<KuItem[]>(() => {
    let arr = list.data ?? [];
    if (activeGroupId === '__ungrouped__') arr = arr.filter((k) => !groupByKuId[k.ku_id]);
    else if (activeGroupId) arr = arr.filter((k) => groupByKuId[k.ku_id] === activeGroupId);
    // kind 过滤(优先级最高)
    if (kindFilter) arr = arr.filter((k) => k.kind === kindFilter);
    // visibility / ownership
    if (filter === 'mine') arr = arr.filter((k) => k.mine);
    else if (filter === 'public') arr = arr.filter((k) => k.visibility === 'public');
    else if (filter === 'recommended') {
      const pub = arr.filter((k) => k.visibility === 'public');
      const featured = pub.filter((k) => (k.count ?? 0) >= 5);
      arr = featured.length > 0 ? featured : pub;
    }
    if (keyword.trim()) {
      const q = keyword.toLowerCase();
      arr = arr.filter(
        (k) =>
          k.display_name.toLowerCase().includes(q) ||
          k.name.toLowerCase().includes(q) ||
          (k.description ?? '').toLowerCase().includes(q),
      );
    }
    if (sort === 'name') arr = [...arr].sort((a, b) => a.display_name.localeCompare(b.display_name));
    else if (sort === 'docs') arr = [...arr].sort((a, b) => (b.count ?? 0) - (a.count ?? 0));
    else arr = [...arr].sort((a, b) => (b.create_time ?? '').localeCompare(a.create_time ?? ''));
    // 以图搜库激活时:命中的 image KB 按 score 降序前置;其他保持当前顺序
    if (kbImgSearch.state.active && kbImgSearch.state.scores.size > 0) {
      const sc = kbImgSearch.state.scores;
      const matched: KuItem[] = [];
      const rest: KuItem[] = [];
      for (const it of arr) {
        if (sc.has(it.ku_id)) matched.push(it);
        else rest.push(it);
      }
      matched.sort((a, b) => (sc.get(b.ku_id) ?? 0) - (sc.get(a.ku_id) ?? 0));
      arr = [...matched, ...rest];
    }
    return arr;
  }, [list.data, activeGroupId, groupByKuId, filter, kindFilter, keyword, sort, kbImgSearch.state.active, kbImgSearch.state.scores]);

  const createKbGroup = React.useCallback(() => {
    const name = window.prompt('知识库分组名称');
    if (!name?.trim()) return null;
    return createGroup(name.trim());
  }, [createGroup]);

  const assignSelectedToGroup = React.useCallback((groupId: string | null) => {
    if (selectedKuIds.length === 0) return;
    assignToGroup(selectedKuIds, groupId);
  }, [assignToGroup, selectedKuIds]);

  const openKbContextMenu = React.useCallback((event: React.MouseEvent, item: KuItem) => {
    event.preventDefault();
    event.stopPropagation();
    setContextMenu({ x: event.clientX, y: event.clientY, target: { type: 'kb', item } });
  }, []);

  const openBlankContextMenu = React.useCallback((event: React.MouseEvent) => {
    event.preventDefault();
    setContextMenu({ x: event.clientX, y: event.clientY, target: { type: 'blank' } });
  }, []);

  const deleteKu = React.useCallback(async (item: KuItem) => {
    if (!item.mine) {
      reportError(new Error('只能删除自己创建的知识库'), '删除失败');
      return;
    }
    // 走 platform.dialog.confirm(ask),不要裸 window.confirm —
    // Tauri 2 把 window.confirm 重定向到 plugin:dialog|confirm,某些版本下报
    // "dialog.confirm not allowed. Command not found"(即使 caps 已 allow)。
    const ok = await getPlatform().dialog.confirm(
      `确定删除知识库「${item.display_name}」? 该操作不可恢复。`,
      { title: '删除知识库' },
    );
    if (!ok) return;
    try {
      if (item.kind === 'document') {
        await kbApi.delete(item.name);
      } else {
        const id = sourceIdFromKuId(item.ku_id);
        if (id == null) throw new Error('非法 ku_id');
        await knowledgeSource.delete(id);
      }
      assignToGroup([item.ku_id], null);
      setKuIds(selectedKuIds.filter((id) => id !== item.ku_id));
      void qc.invalidateQueries({ queryKey: ['ku.list'] });
    } catch (e) {
      reportError(e, '删除知识库失败');
    }
  }, [assignToGroup, qc, selectedKuIds, setKuIds]);

  const editDetail = React.useMemo<KuDetail | null>(() => {
    if (!editKu) return null;
    const base = { ku_id: editKu.ku_id, kind: editKu.kind, meta: editKu } as const;
    if (editKu.kind === 'document') return { ...base, kind: 'document', files: [] };
    if (editKu.kind === 'image') return { ...base, kind: 'image', sub_kind: 'image', items: [] };
    if (editKu.kind === 'structured') return { ...base, kind: 'structured', sub_kind: (editKu.sub_kind === 'mongo' || editKu.sub_kind === 'es' ? editKu.sub_kind : 'sql'), tables: [] };
    return { ...base, kind: 'vector', sub_kind: 'vector', collections: [] };
  }, [editKu]);

  /** 触发以图搜库:让用户选/拖图 → 跑 useKbImageSearch */
  const onSearchByImageAcrossKbs = React.useCallback(async () => {
    const imageKbs = (list.data ?? []).filter((k) => k.kind === 'image');
    if (imageKbs.length === 0) {
      notifyInfo('没有图像知识库', '当前账号下没有可搜索的图像类知识库');
      return;
    }
    try {
      const picked = await getPlatform().fs.pickFiles({
        multiple: false,
        accept: ['image/*'],
      });
      if (!picked.length) return;
      // kindFilter 自动切到 image,让命中和未命中的对比更直观
      if (kindFilter && kindFilter !== 'image') setKindFilter('image');
      void kbImgSearch.start(picked[0] as unknown as File, imageKbs);
    } catch (e) {
      console.error('[chayuan] 以图搜库失败:', e);
    }
  }, [list.data, kindFilter, kbImgSearch]);

  const selectedSet = React.useMemo(() => new Set(selectedKuIds), [selectedKuIds]);
  const allSelected = items.length > 0 && items.every((k) => selectedSet.has(k.ku_id));

  const toggleOne = (id: string) => {
    setKuIds(
      selectedSet.has(id)
        ? selectedKuIds.filter((x) => x !== id)
        : [...selectedKuIds, id],
    );
  };
  const toggleAll = () => {
    if (allSelected) setKuIds([]);
    else setKuIds(items.map((k) => k.ku_id));
  };
  const clearAll = () => setKuIds([]);

  // ── 聊天 ─────────────────────────────────────────────────
  const chat = useChayuanChat({
    conversationId: activeConversationId ?? undefined,
    buildRequest: ({ messages, query }) =>
      buildChatRequest(query, messages, useComposerStore.getState()),
  });
  const setChatMessages = chat.setMessages;

  // 跨 KB 提问:走 SSE 流式,完成一个 KB 推一个 block,前端增量替换骨架卡。
  // 用 ref 持有当前流的 AbortController(用户取消 / 切会话时中止)+ 一个 streaming bool 触发重渲。
  const askCtrlRef = React.useRef<AbortController | null>(null);
  const [askStreaming, setAskStreaming] = React.useState(false);

  const startAskStream = React.useCallback(async (q: string, ids: string[]) => {
    askCtrlRef.current?.abort();
    const ctrl = new AbortController();
    askCtrlRef.current = ctrl;
    setAskStreaming(true);
    setAskResult({ query: q, results: [] });
    setAskSummary({ text: '', pending: false });
    try {
      const effectiveTopK = topK ?? (searchMode === 'balanced' ? 8 : searchMode === 'precise' ? 3 : 5);
      const blocks: AskResponse['results'] = [];
      await knowledgeUniverse.askStream(q, ids, effectiveTopK, {
        onMeta: () => { /* total 已经在前端通过 askKuIds 知晓 */ },
        onBlock: (block) => {
          if (ctrl.signal.aborted) return;
          const blockIdx = blocks.findIndex((b) => b.ku_id === block.ku_id);
          if (blockIdx === -1) blocks.push(block);
          else blocks[blockIdx] = block;
          // 增量追加;若同 ku_id 重复(理论上不会),用最后一份覆盖
          setAskResult((prev) => {
            if (!prev) return { query: q, results: [block] };
            const idx = prev.results.findIndex((b) => b.ku_id === block.ku_id);
            if (idx === -1) return { ...prev, results: [...prev.results, block] };
            const next = [...prev.results];
            next[idx] = block;
            return { ...prev, results: next };
          });
        },
        onSummaryStart: () => {
          if (ctrl.signal.aborted) return;
          setAskSummary((prev) => ({ ...prev, pending: true }));
        },
        onSummary: (payload) => {
          if (ctrl.signal.aborted) return;
          setAskSummary({
            text: payload.text || '',
            pending: false,
            error: payload.ok ? undefined : (payload.error || '总结失败'),
            evidenceCount: payload.evidence_count,
          });
        },
        onError: (e) => {
          if (!ctrl.signal.aborted) reportError(e, '跨知识库提问失败');
        },
        onDone: () => {
          if (ctrl.signal.aborted) return;
          const response: AskResponse = { query: q, results: [...blocks] };
          void annotationApi.sampleFromKbAsk({
            query: q,
            ku_ids: ids,
            top_k: effectiveTopK,
            result: response as unknown as Record<string, unknown>,
            options: {
              source: 'KbBoard',
              model: modelId,
              rewrite: rewriteStrategy,
              search_mode: searchMode,
            },
          }).catch(() => {/* sampling is best effort */});
        },
      }, {
        signal: ctrl.signal,
        model: modelId,
        rewrite: rewriteStrategy,
        useHybrid: searchMode === 'balanced',
        useRerank: searchMode === 'balanced',
      });
    } finally {
      if (askCtrlRef.current === ctrl) {
        askCtrlRef.current = null;
        setAskStreaming(false);
      }
    }
  }, [modelId, rewriteStrategy, searchMode, topK]);

  // unmount 时取消
  React.useEffect(() => {
    return () => {
      askCtrlRef.current?.abort();
    };
  }, []);

  const stopAskStream = React.useCallback(() => {
    askCtrlRef.current?.abort();
  }, []);

  /**
   * 顶部搜索框 Enter / 点"搜内容"按钮 → 用关键词在所有可见 KB 里跑内容检索。
   *
   * 之前 keyword 只做"按 KB 名/描述本地过滤",用户反馈"无法命中库内容";
   * 这里把 keyword 同时当成跨 KB 检索 query —— 用 `items`(已应用 kind / 归属 /
   * 关键词过滤后的可见列表)的 ku_ids 作为检索范围,空时退回全量 list.data。
   * 检索结果走现成的 KbAskResultPanel,跟"选 KB → composer 提问"路径一致,UI 不
   * 再多一套。返回首页:KbAskResultPanel 自带返回按钮(setChatStarted(false))。
   */
  const onSubmitContentSearch = React.useCallback(() => {
    const q = keyword.trim();
    if (!q) return;
    if (askStreaming) return;  // 上次检索还在跑,避免重复触发
    const visibleIds = items.map((k) => k.ku_id);
    const fallbackIds = (list.data ?? []).map((k) => k.ku_id);
    const ids = visibleIds.length > 0 ? visibleIds : fallbackIds;
    if (ids.length === 0) {
      notifyInfo('还没有可检索的知识库');
      return;
    }
    setAskQuery(q);
    setAskResult(null);
    setAskKuIds(ids);
    setChatStarted(true);
    setPickerOpen(false);
    void startAskStream(q, ids);
  }, [keyword, askStreaming, items, list.data, startAskStream]);

  /** 用 selectedKuIds 的 kind 决定走哪条发送链:
   *  - 全是 document → 走原 chat(/chat/v2/chat 已有 KB 模式)
   *  - 含任何非 document → 走 universe.ask 跨类型聚合 */
  const itemsByKuId = React.useMemo(() => {
    const m = new Map<string, KuItem>();
    (list.data ?? []).forEach((k) => m.set(k.ku_id, k));
    return m;
  }, [list.data]);
  const hasNonDocSelected = React.useMemo(
    () => selectedKuIds.some((id) => itemsByKuId.get(id)?.kind && itemsByKuId.get(id)?.kind !== 'document'),
    [selectedKuIds, itemsByKuId],
  );

  const lastConvRef = React.useRef<string | null>(null);
  // 标记「由本页 onSend 首次发问刚创建」的会话 id —— 这种 setActive 不算「切到了
  // 另一个会话」,reset effect 必须跳过它,否则首次提问后 chatStarted 会被立刻
  // 重置回 false / pickerOpen 回 true,页面停在选择器、不跳到对话视图。
  const selfCreatedConvRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    const cid = activeConversationId;
    if (cid === lastConvRef.current) return;
    lastConvRef.current = cid;
    // 本页首发消息自建的会话:只同步 lastConvRef,不回退到 picker。
    if (cid && cid === selfCreatedConvRef.current) {
      selfCreatedConvRef.current = null;
      return;
    }
    setChatMessages((prev) => (prev.length === 0 ? prev : []));
    setChatStarted(false);
    setPickerOpen(true);
    setAskResult(null);
  }, [activeConversationId, setChatMessages]);

  const onSend = async (text: string) => {
    // 跨类型 ask:含任何非 document 的选中 → 走 universe.askStream(SSE 流式)
    if (selectedKuIds.length > 0 && hasNonDocSelected) {
      setAskQuery(text);
      setAskResult(null);
      setAskKuIds([...selectedKuIds]);
      setChatStarted(true);
      setPickerOpen(false);
      void startAskStream(text, selectedKuIds);
      return;
    }
    // 否则走原 chat(纯文档 KB 或无 KB)
    if (!activeConversationId) {
      const id = uuid();
      const now = Date.now();
      // 先打标记再 setActive:reset effect 看到这个 id 就知道是自建会话,不回退。
      selfCreatedConvRef.current = id;
      setActive(id);
      void upsertConversation({
        id,
        remote_id: null,
        title: text.slice(0, 40) || t('chat.newConversation'),
        model: modelId,
        mode: selectedKuIds.length ? 'kb' : 'plain',
        created_at: now,
        updated_at: now,
      })
        .then(() => qc.invalidateQueries({ queryKey: CONVERSATION_KEYS.list }))
        .catch((e) => console.error('[chayuan] kb 新建会话写库失败:', e));
    }
    setChatStarted(true);
    setPickerOpen(false);
    await chat.sendMessage(text);
    const cid = activeConversationId;
    if (cid) {
      const last = chat.messages[chat.messages.length - 1];
      const second = chat.messages[chat.messages.length - 2];
      try {
        if (second) await appendMessage(cid, second);
        if (last) await appendMessage(cid, last);
      } catch (e) {
        console.error('[chayuan] kb 落库失败:', e);
      }
    }
  };

  const showPicker = !chatStarted || pickerOpen;
  const showRail = chatStarted && !pickerOpen;

  return (
    <div className="flex h-full">
      <div className="flex h-full min-w-0 flex-1 flex-col">
        {showPicker && (
          <div className="flex flex-col gap-3 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-6 py-4">
            <Header
              total={list.data?.length ?? 0}
              view={view}
              onView={setView}
              onCreate={() => setCreateOpen({})}
              onCreateDoc={() => setCreateOpen({ kind: 'document' })}
              onCreateDb={() => setAddStructuredOpen(true)}
              onCreateImage={() => setCreateOpen({ kind: 'image' })}
              onCreateVector={() => setCreateOpen({ kind: 'vector' })}
              onAiNote={goToAiNote}
              onUploadDoc={() => setUploadTarget({ kind: 'document' })}
              onUploadImage={() => setUploadTarget({ kind: 'image' })}
              showCloseOverlay={chatStarted && pickerOpen}
              onCloseOverlay={() => setPickerOpen(false)}
            />
            <KindChips kindFilter={kindFilter} onKindFilter={setKindFilter} items={list.data ?? []} />
            <FilterBar
              filter={filter}
              onFilter={setFilter}
              sort={sort}
              onSort={setSort}
              keyword={keyword}
              onKeyword={setKeyword}
              onSubmitKeyword={onSubmitContentSearch}
              contentSearchBusy={askStreaming}
              onSearchByImage={onSearchByImageAcrossKbs}
              imageSearchBusy={kbImgSearch.state.busy}
              imageSearchActive={kbImgSearch.state.active}
            />
            {kbImgSearch.state.active && (
              <KbImageSearchStrip state={kbImgSearch.state} onClear={kbImgSearch.clear} onCancel={kbImgSearch.cancel} />
            )}
            <SelectionToolbar
              total={items.length}
              selected={selectedKuIds.length}
              allSelected={allSelected}
              onToggleAll={toggleAll}
              onClear={clearAll}
              onAsk={() => {
                // 引导用户聚焦 composer;实际触发由 ChatComposer onSend 拦截
                const ta = document.querySelector<HTMLTextAreaElement>('textarea');
                if (ta) ta.focus();
              }}
              onExitMultiSelect={() => clearAll()}
              groups={groups}
              onAssignGroup={(groupId) => assignSelectedToGroup(groupId)}
              onCreateGroup={() => {
                const id = createKbGroup();
                if (id) assignSelectedToGroup(id);
              }}
            />
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {showPicker ? (
            <KbPickerPane
              loading={list.isLoading}
              error={list.error as Error | null}
              items={items}
              view={view}
              selectedSet={selectedSet}
              filtered={!!keyword || filter !== 'all' || !!kindFilter}
              imageScores={kbImgSearch.state.active ? kbImgSearch.state.scores : null}
              onToggle={toggleOne}
              onOpen={onOpenKb}
              groups={groups}
              activeGroupId={activeGroupId}
              groupByKuId={groupByKuId}
              onOpenGroup={setActiveGroup}
              onDeleteGroup={deleteGroup}
              onKbContextMenu={openKbContextMenu}
              onBlankContextMenu={openBlankContextMenu}
              onRetry={() => list.refetch()}
            />
          ) : askQuery && (askResult || askStreaming) ? (
            // 跨 KB ask 模式:渲染按 kind 分块的结果面板;流式期间 result 已逐步填充
            <KbAskResultPanel
              query={askQuery}
              result={askResult}
              itemsByKuId={itemsByKuId}
              loading={askStreaming}
              onStop={stopAskStream}
              selectedKuIds={askKuIds}
              summary={askSummary}
            />
          ) : (
            // 纯文档 KB 走原 Thread(useChayuanChat)
            <Thread messages={chat.messages} onRegenerate={chat.regenerate} />
          )}
        </div>

        <div className="border-t border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-6 py-3">
          <div className="mx-auto w-full max-w-3xl">
            <ChatComposer
              isStreaming={chat.isStreaming || askStreaming}
              onSend={onSend}
              onStop={() => {
                if (askStreaming) stopAskStream();
                else chat.stop();
              }}
              placeholder={
                selectedKuIds.length > 0
                  ? t('knowledge.composerScope', { count: selectedKuIds.length })
                  : t('knowledge.composerNoScope')
              }
              glow={false}
              showRetrievalControls
            />
          </div>
        </div>
      </div>

      {showRail && (
        <KbScopeRail
          selected={selectedKuIds}
          itemsByKuId={itemsByKuId}
          onExpand={() => setPickerOpen(true)}
        />
      )}

      <AddStructuredSourceDialog
        open={addStructuredOpen}
        onOpenChange={setAddStructuredOpen}
      />

      <CreateKbDialog
        open={createOpen != null}
        initialKind={createOpen?.kind}
        onOpenChange={(o) => { if (!o) setCreateOpen(null); }}
        onPickStructured={() => setAddStructuredOpen(true)}
        onCreated={(kuId) => {
          // 刚建好就让用户能看到+操作:在 TabBar 上新开一个详情 Tab
          // refetch 先跑,onOpenKb 才能拿到正确的 display_name
          void list.refetch().finally(() => onOpenKb(kuId));
        }}
      />

      {/* 兼容老路径:CreateKbDialog 之外的地方暂未来得及切;详情 Dialog 仍可被外部 setDetailKuId 唤起 */}
      <KbDetailDialog kuId={detailKuId} onClose={() => setDetailKuId(null)} />

      {uploadTarget && (
        <UploadTargetDialog
          open={true}
          kind={uploadTarget.kind}
          onClose={() => setUploadTarget(null)}
          onPick={onUploadTargetPick}
          onRequestCreate={onUploadTargetCreate}
        />
      )}
      {editDetail && (
        <EditKbInfoDialog
          detail={editDetail}
          open={!!editKu}
          onOpenChange={(open) => {
            if (!open) {
              setEditKu(null);
              void list.refetch();
            }
          }}
        />
      )}
      <KbContextMenu
        menu={contextMenu}
        groups={groups}
        onClose={() => setContextMenu(null)}
        onOpenKb={(item) => onOpenKb(item.ku_id)}
        onEditKb={setEditKu}
        onDeleteKb={(item) => void deleteKu(item)}
        onCreateGroup={createKbGroup}
        onMoveToGroup={(item, groupId) => assignToGroup([item.ku_id], groupId)}
      />
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// Header
// ──────────────────────────────────────────────────────────────

const Header: React.FC<{
  total: number;
  view: ViewMode;
  onView(v: ViewMode): void;
  /** 打开 picker(无预选 kind),让用户选 4 种之一 */
  onCreate(): void;
  onCreateDoc(): void;
  onCreateDb(): void;
  onCreateImage(): void;
  onCreateVector(): void;
  /** 新增:AI 笔记 / 快捷上传入口 */
  onAiNote(): void;
  onUploadDoc(): void;
  onUploadImage(): void;
  showCloseOverlay: boolean;
  onCloseOverlay(): void;
}> = ({ total, view, onView, onCreate, onCreateDoc, onCreateDb, onCreateImage, onCreateVector, onAiNote, onUploadDoc, onUploadImage, showCloseOverlay, onCloseOverlay }) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-baseline gap-3">
        <h1 className="text-xl font-semibold text-[var(--cy-text-primary)]">
          {t('knowledge.title')}
        </h1>
        <span className="text-sm text-[var(--cy-text-tertiary)]">{total}</span>
      </div>
      <div className="flex items-center gap-2">
        <div className="inline-flex h-8 items-center rounded-full bg-[var(--cy-surface-2)] p-0.5">
          <ViewBtn active={view === 'card'} onClick={() => onView('card')} aria-label={t('knowledge.viewCard')}>
            <LayoutGrid className="h-3.5 w-3.5" />
          </ViewBtn>
          <ViewBtn active={view === 'list'} onClick={() => onView('list')} aria-label={t('knowledge.viewList')}>
            <ListIcon className="h-3.5 w-3.5" />
          </ViewBtn>
        </div>

        <button
          type="button"
          onClick={onUploadDoc}
          className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-3 text-xs text-[var(--cy-text-secondary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
          title="上传文件到任一文档知识库"
        >
          <FileUp className="h-3.5 w-3.5" />
          上传文件
        </button>
        <button
          type="button"
          onClick={onUploadImage}
          className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-3 text-xs text-[var(--cy-text-secondary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
          title="上传图像到任一图像知识库"
        >
          <ImageUp className="h-3.5 w-3.5" />
          上传图像
        </button>
        <button
          type="button"
          onClick={onAiNote}
          className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-3 text-xs text-[var(--cy-text-secondary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
        >
          <NotebookPen className="h-3.5 w-3.5" />
          {t('knowledge.aiNote')}
        </button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="inline-flex h-8 items-center gap-1.5 rounded-full bg-[var(--cy-ink-700)] px-3 text-xs font-medium text-white transition-colors hover:bg-[var(--cy-ink-800)]"
            >
              <Plus className="h-3.5 w-3.5" />
              {t('common.new')}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onCreate}>
              <Plus className="h-3.5 w-3.5" />
              选择类型…
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onCreateDoc}>
              <FileText className="h-3.5 w-3.5" />
              {t('knowledge.addDoc')}
            </DropdownMenuItem>
            {/* 图像库已下线(Level C)—— image-embedding sidecar 需要 PyTorch,
                full 版瘦身后没打。要恢复:取消下面注释 + 取消注释 KbBoard 的
                Header 入参里 onCreateImage 那一条 + CreateKbDialog.tsx 里
                kind === 'image' 表单。文档库现在自动 OCR,图像 PDF 也能搜。
            <DropdownMenuItem onClick={onCreateImage}>
              <Images className="h-3.5 w-3.5" />
              图像库
            </DropdownMenuItem>
            */}
            <DropdownMenuItem onClick={onCreateDb}>
              <Database className="h-3.5 w-3.5" />
              {t('knowledge.addDb')}
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onCreateVector}>
              <Boxes className="h-3.5 w-3.5" />
              外部向量库
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        {showCloseOverlay && (
          <button
            type="button"
            onClick={onCloseOverlay}
            className="inline-flex h-8 w-8 items-center justify-center rounded-full text-[var(--cy-text-tertiary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
            aria-label={t('knowledge.rail.expanded')}
          >
            <XIcon className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
};

const ViewBtn: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement> & { active: boolean }> = ({
  active,
  className,
  ...rest
}) => (
  <button
    type="button"
    className={cn(
      'flex h-7 w-8 items-center justify-center rounded-full transition-colors',
      active
        ? 'bg-[var(--cy-surface-base)] text-[var(--cy-text-primary)] shadow-[var(--cy-shadow-sm)]'
        : 'text-[var(--cy-text-tertiary)] hover:text-[var(--cy-text-primary)]',
      className,
    )}
    {...rest}
  />
);

// ──────────────────────────────────────────────────────────────
// Kind chips(类型筛选)+ Filter / Sort / Search + Toolbar
// ──────────────────────────────────────────────────────────────

const KindChips: React.FC<{
  kindFilter: KuKind | '';
  onKindFilter(k: KuKind | ''): void;
  items: KuItem[];
}> = ({ kindFilter, onKindFilter, items }) => {
  const counts = React.useMemo(() => {
    const c: Record<KuKind, number> = { document: 0, image: 0, structured: 0, vector: 0 };
    for (const it of items) c[it.kind] = (c[it.kind] ?? 0) + 1;
    return c;
  }, [items]);

  const pills: Array<{ value: KuKind | ''; label: string; count: number }> = [
    { value: '', label: '全部', count: items.length },
    { value: 'document', label: KIND_META.document.label, count: counts.document },
    { value: 'image', label: KIND_META.image.label, count: counts.image },
    { value: 'structured', label: KIND_META.structured.label, count: counts.structured },
    { value: 'vector', label: KIND_META.vector.label, count: counts.vector },
  ];
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {pills.map((p) => {
        const active = p.value === kindFilter;
        const Icon = p.value ? KIND_META[p.value].icon : Library;
        return (
          <button
            key={p.value || 'all'}
            type="button"
            onClick={() => onKindFilter(p.value)}
            className={cn(
              'inline-flex h-7 items-center gap-1.5 rounded-full px-3 text-xs transition-colors',
              active
                ? 'bg-[var(--cy-ink-700)] text-white'
                : 'border border-[var(--cy-border-subtle)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
            )}
          >
            <Icon className="h-3 w-3" />
            <span>{p.label}</span>
            <span className={cn('rounded-full px-1.5 text-[10px]', active ? 'bg-white/20' : 'bg-[var(--cy-surface-2)]')}>
              {p.count}
            </span>
          </button>
        );
      })}
    </div>
  );
};

const FILTERS: ReadonlyArray<{ value: KbFilter; key: string }> = [
  { value: 'all', key: 'knowledge.filter.all' },
  { value: 'mine', key: 'knowledge.filter.mine' },
  { value: 'public', key: 'knowledge.filter.public' },
  { value: 'recommended', key: 'knowledge.filter.recommended' },
];

const SORTS: ReadonlyArray<{ value: KbSort; key: string }> = [
  { value: 'latest', key: 'knowledge.sortBy.latest' },
  { value: 'name', key: 'knowledge.sortBy.name' },
  { value: 'docs', key: 'knowledge.sortBy.docs' },
];

const FilterBar: React.FC<{
  filter: KbFilter;
  onFilter(f: KbFilter): void;
  sort: KbSort;
  onSort(s: KbSort): void;
  keyword: string;
  onKeyword(s: string): void;
  /** 提交搜索(Enter / 点搜索按钮)→ 跨所有可见 KB 走内容检索 */
  onSubmitKeyword?(): void;
  /** 内容检索进行中(askStream 流式)— 控制搜索按钮的 spinner */
  contentSearchBusy?: boolean;
  /** 触发以图搜库 */
  onSearchByImage?(): void;
  /** 以图搜库进行中(显示 spinner 替代 icon) */
  imageSearchBusy?: boolean;
  /** 以图搜库激活态(高亮按钮) */
  imageSearchActive?: boolean;
}> = ({ filter, onFilter, sort, onSort, keyword, onKeyword, onSubmitKeyword, contentSearchBusy, onSearchByImage, imageSearchBusy, imageSearchActive }) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-1">
        {FILTERS.map((f) => {
          const active = f.value === filter;
          return (
            <button
              key={f.value}
              type="button"
              onClick={() => onFilter(f.value)}
              className={cn(
                'inline-flex h-7 items-center rounded-full px-3 text-xs transition-colors',
                active
                  ? 'bg-[var(--cy-text-primary)] text-[var(--cy-surface-base)]'
                  : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
              )}
            >
              {t(f.key)}
            </button>
          );
        })}
      </div>
      <div className="flex items-center gap-2">
        <div className="relative w-64">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
          <input
            value={keyword}
            placeholder="搜知识库名 / 回车搜库内容"
            onChange={(e) => onKeyword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing && onSubmitKeyword) {
                e.preventDefault();
                onSubmitKeyword();
              }
            }}
            title="输入关键词:边输边过滤知识库卡片;按 Enter 在所有可见知识库中检索内容"
            className="h-8 w-full rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] pl-8 pr-12 text-xs text-[var(--cy-text-primary)] placeholder:text-[var(--cy-text-tertiary)] focus-visible:border-[var(--cy-brand-400)] focus-visible:outline-none"
          />
          {onSubmitKeyword && (
            <button
              type="button"
              onClick={() => onSubmitKeyword()}
              disabled={!keyword.trim() || contentSearchBusy}
              title="在所有可见知识库中检索内容(等同 Enter)"
              className="absolute right-1 top-1/2 inline-flex h-6 -translate-y-1/2 items-center gap-1 rounded-full bg-[var(--cy-brand-500)] px-2 text-[10px] font-medium text-white shadow-sm transition-opacity hover:bg-[var(--cy-brand-600)] disabled:opacity-40"
            >
              {contentSearchBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Search className="h-3 w-3" />}
              <span>搜内容</span>
            </button>
          )}
        </div>
        {onSearchByImage && (
          <button
            type="button"
            onClick={onSearchByImage}
            disabled={imageSearchBusy}
            title="上传一张图,在所有图像知识库中查找相似图"
            className={cn(
              'inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs transition-colors disabled:opacity-60',
              imageSearchActive
                ? 'border-rose-300 bg-rose-50 text-rose-700 hover:bg-rose-100'
                : 'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
            )}
          >
            {imageSearchBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ImageIcon className="h-3.5 w-3.5" />}
            <span>以图搜库</span>
          </button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-3 text-xs text-[var(--cy-text-secondary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
            >
              {t(SORTS.find((s) => s.value === sort)!.key)}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {SORTS.map((s) => (
              <DropdownMenuItem key={s.value} onClick={() => onSort(s.value)}>
                {t(s.key)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
};

const SelectionToolbar: React.FC<{
  total: number;
  selected: number;
  allSelected: boolean;
  onToggleAll(): void;
  onClear(): void;
  onAsk(): void;
  onExitMultiSelect(): void;
  groups: KbLocalGroup[];
  onAssignGroup(groupId: string | null): void;
  onCreateGroup(): void;
}> = ({
  total, selected, allSelected, onToggleAll, onClear, onAsk, onExitMultiSelect,
  groups, onAssignGroup, onCreateGroup,
}) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--cy-text-tertiary)]">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onToggleAll}
          disabled={total === 0}
          className={cn(
            'inline-flex h-7 items-center gap-1.5 rounded-full border px-3 transition-colors disabled:opacity-50',
            allSelected
              ? 'border-[var(--cy-brand-300)] bg-[var(--cy-brand-50)] text-[var(--cy-brand-700)]'
              : 'border-[var(--cy-border-subtle)] text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]',
          )}
        >
          <CheckCheck className="h-3.5 w-3.5" />
          {t('knowledge.selectAll')}
        </button>
        {selected > 0 && (
          <>
            <span>{t('knowledge.selectedNCount', { count: selected })}</span>
            <button
              type="button"
              onClick={onClear}
              className="text-[var(--cy-text-secondary)] hover:text-[var(--cy-text-primary)]"
            >
              {t('common.clear')}
            </button>
          </>
        )}
      </div>

      {/* 多选时浮出操作组(对齐 知识库2.png:问一问 / 退出多选) */}
      {selected > 0 && (
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onAsk}
            className="inline-flex h-7 items-center gap-1.5 rounded-full bg-[var(--cy-brand-500)] px-3 text-xs font-medium text-white transition-colors hover:bg-[var(--cy-brand-600)]"
          >
            <MessageSquarePlus className="h-3.5 w-3.5" />
            问一问
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="inline-flex h-7 items-center gap-1.5 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-3 text-xs text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
              >
                加入分组
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {groups.map((g) => (
                <DropdownMenuItem key={g.id} onClick={() => onAssignGroup(g.id)}>
                  {g.name}
                </DropdownMenuItem>
              ))}
              {groups.length > 0 && <DropdownMenuItem onClick={() => onAssignGroup(null)}>移出分组</DropdownMenuItem>}
              <DropdownMenuItem onClick={onCreateGroup}>
                <Plus className="h-3.5 w-3.5" />
                新建分组并加入
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <button
            type="button"
            onClick={onExitMultiSelect}
            className="inline-flex h-7 items-center gap-1.5 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-3 text-xs text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
          >
            <LogOut className="h-3.5 w-3.5" />
            退出多选
          </button>
        </div>
      )}
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// 主面板
// ──────────────────────────────────────────────────────────────

const KbPickerPane: React.FC<{
  loading: boolean;
  error: Error | null;
  items: KuItem[];
  view: ViewMode;
  selectedSet: Set<string>;
  filtered: boolean;
  /** 以图搜库激活时的 ku_id → score 映射;为 null 表示未激活 */
  imageScores: Map<string, number> | null;
  onToggle(id: string): void;
  onOpen(id: string): void;
  groups: KbLocalGroup[];
  activeGroupId: string | null;
  groupByKuId: Record<string, string>;
  onOpenGroup(id: string | null): void;
  onDeleteGroup(id: string): void;
  onKbContextMenu(event: React.MouseEvent, item: KuItem): void;
  onBlankContextMenu(event: React.MouseEvent): void;
  onRetry(): void;
}> = ({
  loading, error, items, view, selectedSet, filtered, imageScores, onToggle, onOpen,
  groups, activeGroupId, groupByKuId, onOpenGroup, onDeleteGroup,
  onKbContextMenu, onBlankContextMenu, onRetry,
}) => {
  if (loading) return <div className="px-6 py-6"><SkeletonGrid view={view} /></div>;
  if (error)
    return (
      <div className="h-full px-6 py-6">
        <ErrorState error={error} onRetry={onRetry} />
      </div>
    );
  const showGroups = !activeGroupId && groups.length > 0 && !filtered;
  if (items.length === 0 && !showGroups)
    return (
      <div className="h-full px-6 py-6" onContextMenu={onBlankContextMenu}>
        {activeGroupId ? (
          <div className="mb-3">
            <button
              type="button"
              onClick={() => onOpenGroup(null)}
              className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[var(--cy-border-subtle)] px-3 text-xs text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)]"
            >
              返回全部知识库
            </button>
          </div>
        ) : null}
        <EmptyState filtered={filtered || !!activeGroupId} />
      </div>
    );
  return (
    <div className="h-full overflow-y-auto px-6 py-4" onContextMenu={onBlankContextMenu}>
      {activeGroupId ? (
        <div className="mb-3 flex items-center gap-2 text-xs">
          <button
            type="button"
            onClick={() => onOpenGroup(null)}
            className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[var(--cy-border-subtle)] px-3 text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
          >
            返回全部知识库
          </button>
          <span className="text-[var(--cy-text-tertiary)]">
            当前分组: {groups.find((g) => g.id === activeGroupId)?.name ?? '分组'}
          </span>
        </div>
      ) : null}
      {view === 'card' ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {showGroups && groups.map((g) => (
            <KbGroupCard
              key={g.id}
              group={g}
              count={itemsCountForGroup(groupByKuId, g.id)}
              onOpen={() => onOpenGroup(g.id)}
              onDelete={() => onDeleteGroup(g.id)}
            />
          ))}
          {items.map((k, i) => (
            <div key={k.ku_id} onContextMenu={(event) => onKbContextMenu(event, k)}>
              <KbCard
                kb={k}
                selected={selectedSet.has(k.ku_id)}
                imageScore={imageScores?.get(k.ku_id) ?? null}
                index={i}
                onToggle={() => onToggle(k.ku_id)}
                onOpen={() => onOpen(k.ku_id)}
              />
            </div>
          ))}
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]">
          {showGroups && groups.map((g) => (
            <KbGroupRow
              key={g.id}
              group={g}
              count={itemsCountForGroup(groupByKuId, g.id)}
              onOpen={() => onOpenGroup(g.id)}
              onDelete={() => onDeleteGroup(g.id)}
            />
          ))}
          {items.map((k, i) => (
            <div key={k.ku_id} onContextMenu={(event) => onKbContextMenu(event, k)}>
              <KbRow
                kb={k}
                selected={selectedSet.has(k.ku_id)}
                imageScore={imageScores?.get(k.ku_id) ?? null}
                index={i}
                onToggle={() => onToggle(k.ku_id)}
                onOpen={() => onOpen(k.ku_id)}
                isLast={i === items.length - 1}
              />
            </div>
          ))}
        </div>
      )}
      <style>{`@keyframes kbCardIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}`}</style>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// 卡片
// ──────────────────────────────────────────────────────────────

function itemsCountForGroup(groupByKuId: Record<string, string>, groupId: string): number {
  return Object.values(groupByKuId).filter((id) => id === groupId).length;
}

// 组删除前确认 — 包成异步函数,内部走 platform.dialog.confirm(ask)。
// 不要用 window.confirm:Tauri 2 把它走 plugin:dialog|confirm,部分版本下
// 即使 caps 已 allow-confirm 也会抛 "Command not found" ACL 错。
async function confirmDeleteGroup(name: string): Promise<boolean> {
  return getPlatform().dialog.confirm(
    `删除分组「${name}」? 知识库不会被删除。`,
    { title: '删除分组' },
  );
}

const KbGroupCard: React.FC<{
  group: KbLocalGroup;
  count: number;
  onOpen(): void;
  onDelete(): void;
}> = ({ group, count, onOpen, onDelete }) => (
  <button
    type="button"
    onClick={onOpen}
    className="group relative flex h-44 flex-col rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-4 py-3 text-left transition-all hover:-translate-y-0.5 hover:border-[var(--cy-brand-300)] hover:shadow-[var(--cy-shadow-md)]"
  >
    <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-amber-50 text-amber-600">
      <Folder className="h-5 w-5" />
    </span>
    <span className="mt-3 truncate text-sm font-medium text-[var(--cy-text-primary)]">{group.name}</span>
    <span className="mt-1 text-xs text-[var(--cy-text-tertiary)]">{count} 个知识库</span>
    <span className="mt-auto text-[11px] text-[var(--cy-text-secondary)]">打开分组</span>
    <span
      role="button"
      tabIndex={0}
      onClick={(e) => {
        e.stopPropagation();
        void confirmDeleteGroup(group.name).then((ok) => { if (ok) onDelete(); });
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          e.stopPropagation();
          void confirmDeleteGroup(group.name).then((ok) => { if (ok) onDelete(); });
        }
      }}
      className="absolute right-2 top-2 hidden h-6 w-6 items-center justify-center rounded-full text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-red-600 group-hover:flex"
      aria-label="删除分组"
    >
      <XIcon className="h-3.5 w-3.5" />
    </span>
  </button>
);

const KbGroupRow: React.FC<{
  group: KbLocalGroup;
  count: number;
  onOpen(): void;
  onDelete(): void;
}> = ({ group, count, onOpen, onDelete }) => (
  <div className="flex items-center gap-3 border-b border-[var(--cy-border-subtle)] px-3 py-2 hover:bg-[var(--cy-surface-2)]">
    <button type="button" onClick={onOpen} className="flex min-w-0 flex-1 items-center gap-3 text-left">
      <span className="flex h-7 w-7 items-center justify-center rounded-md bg-amber-50 text-amber-600">
        <Folder className="h-3.5 w-3.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-[var(--cy-text-primary)]">{group.name}</span>
        <span className="block text-[11px] text-[var(--cy-text-tertiary)]">{count} 个知识库</span>
      </span>
    </button>
    <button
      type="button"
      onClick={() => {
        void confirmDeleteGroup(group.name).then((ok) => { if (ok) onDelete(); });
      }}
      className="rounded p-1 text-[var(--cy-text-tertiary)] hover:bg-[var(--cy-surface-2)] hover:text-red-600"
      aria-label="删除分组"
    >
      <XIcon className="h-3.5 w-3.5" />
    </button>
  </div>
);

const KbCardCompact: React.FC<{
  kb: KuItem;
  selected: boolean;
  imageScore: number | null;
  index: number;
  onToggle(): void;
  onOpen(): void;
}> = ({ kb, selected, imageScore, index, onToggle, onOpen }) => {
  const meta = KIND_META[kb.kind];
  const Icon = meta.icon;
  const visible = kb.visibility ?? 'private';
  const hasImageHit = imageScore != null && imageScore > 0;
  // 入场 stagger:前 16 个错开 25ms,之后统一一帧免长延迟
  const delay = Math.min(index, 15) * 25;
  return (
    <KbDropZone kb={kb} className={cn(
      'group relative flex h-36 flex-col overflow-hidden rounded-xl border bg-[var(--cy-surface-base)] text-left transition-all duration-200 hover:-translate-y-0.5',
      selected
        ? 'border-[var(--cy-brand-500)] shadow-[var(--cy-shadow-md)] ring-1 ring-[var(--cy-brand-300)]'
        : hasImageHit
          ? 'border-rose-300 shadow-[0_0_0_2px_rgba(244,63,94,0.18)]'
          : 'border-[var(--cy-border-subtle)] hover:border-[var(--cy-brand-300)] hover:shadow-[var(--cy-shadow-md)]',
    )}>
    <div
      style={{ animation: `kbCardIn .35s ease-out ${delay}ms backwards` }}
      className="flex h-full w-full flex-col"
    >
      {/* 以图搜库命中分数 badge */}
      {hasImageHit && (
        <span className="pointer-events-none absolute right-2 bottom-2 z-10 inline-flex items-center gap-1 rounded-full bg-rose-500/95 px-1.5 py-0.5 text-[9px] font-semibold text-white shadow-sm">
          <span>{(imageScore * 100).toFixed(0)}</span>
          <span className="opacity-80">分</span>
        </span>
      )}
      {/* checkbox(独立点击区) */}
      <button
        type="button"
        onClick={onToggle}
        aria-label={selected ? '取消选择' : '选择'}
        className={cn(
          'absolute left-2 top-2 z-10 flex h-5 w-5 items-center justify-center rounded border bg-white/90 backdrop-blur transition-opacity',
          selected
            ? 'border-[var(--cy-brand-600)] bg-[var(--cy-brand-600)] text-white opacity-100'
            : 'border-[var(--cy-border-default)] opacity-0 group-hover:opacity-100',
        )}
      >
        {selected && <CheckCheck className="h-3 w-3" />}
      </button>

      {/* mine 标 */}
      {kb.mine && (
        <span className="absolute right-2 top-2 z-10 rounded-full bg-white/85 px-1.5 py-0.5 text-[10px] font-medium text-[var(--cy-brand-700)] backdrop-blur">
          我的
        </span>
      )}

      {/* 卡片主体可点开 detail */}
      <button
        type="button"
        onClick={onOpen}
        className="flex h-full w-full flex-col text-left"
      >
        <div className={cn('relative h-12 px-3 pt-2 bg-gradient-to-br', meta.gradient)}>
          <Icon className="absolute right-2 top-1/2 h-5 w-5 -translate-y-1/2 text-white/85" />
        </div>
        <div className="flex flex-1 flex-col px-3 pt-2">
          <p className="truncate text-sm font-medium text-[var(--cy-text-primary)]">
            {kb.display_name}
          </p>
          <p className="line-clamp-2 mt-0.5 flex-1 text-[11px] text-[var(--cy-text-tertiary)]">
            {kb.description || '—'}
          </p>
          <div className="mt-1.5 flex items-center justify-between text-[10px] text-[var(--cy-text-tertiary)]">
            <span className="inline-flex items-center gap-1">
              {visible === 'private' ? <Lock className="h-2.5 w-2.5" /> : null}
              <span>{meta.label}</span>
              {kb.count != null && <span className="opacity-70">· {kb.count} {meta.unit}</span>}
            </span>
            <span className="opacity-70">{formatRelative(kb.create_time ?? null) || ''}</span>
          </div>
        </div>
      </button>
      {/* 上传进度条(只有有 jobs 时才渲染) */}
      <KbProgressBar kuId={kb.ku_id} />
    </div>
    </KbDropZone>
  );
};

const KbRow: React.FC<{
  kb: KuItem;
  selected: boolean;
  imageScore: number | null;
  index: number;
  onToggle(): void;
  onOpen(): void;
  isLast: boolean;
}> = ({ kb, selected, imageScore, index, onToggle, onOpen, isLast }) => {
  const meta = KIND_META[kb.kind];
  const Icon = meta.icon;
  const hasImageHit = imageScore != null && imageScore > 0;
  const delay = Math.min(index, 25) * 15;
  return (
    <div
      style={{ animation: `kbCardIn .3s ease-out ${delay}ms backwards` }}
      className={cn(
        'flex w-full items-center gap-3 px-3 py-2 transition-colors',
        selected
          ? 'bg-[var(--cy-brand-50)]'
          : hasImageHit
            ? 'bg-rose-50/60 hover:bg-rose-50'
            : 'hover:bg-[var(--cy-surface-2)]',
        !isLast && 'border-b border-[var(--cy-border-subtle)]',
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-label={selected ? '取消选择' : '选择'}
        className={cn(
          'flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border',
          selected
            ? 'border-[var(--cy-brand-600)] bg-[var(--cy-brand-600)] text-white'
            : 'border-[var(--cy-border-default)] bg-[var(--cy-surface-base)]',
        )}
      >
        {selected && <CheckCheck className="h-3 w-3" />}
      </button>
      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 flex-1 items-center gap-3 text-left"
      >
        <span className={cn('flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-white bg-gradient-to-br', meta.listTint)}>
          <Icon className="h-3.5 w-3.5" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-[var(--cy-text-primary)]">
            {kb.display_name}
          </span>
          {kb.description && (
            <span className="block truncate text-[11px] text-[var(--cy-text-tertiary)]">{kb.description}</span>
          )}
        </span>
        <span className="hidden items-center gap-2 text-[11px] text-[var(--cy-text-tertiary)] sm:flex">
          {hasImageHit && (
            <span className="rounded-full bg-rose-500 px-1.5 py-0.5 font-mono text-white">
              {(imageScore * 100).toFixed(0)} 分
            </span>
          )}
          <span className="rounded-full bg-[var(--cy-surface-2)] px-1.5 py-0.5">{meta.label}</span>
          {kb.count != null && <span>{kb.count} {meta.unit}</span>}
        </span>
      </button>
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// 以图搜库进度条
// ──────────────────────────────────────────────────────────────

const KbImageSearchStrip: React.FC<{
  state: ReturnType<typeof useKbImageSearch>['state'];
  onCancel(): void;
  onClear(): void;
}> = ({ state, onCancel, onClear }) => {
  const pct = state.total > 0 ? Math.round((state.done / state.total) * 100) : 0;
  return (
    <div className="flex items-center gap-3 rounded-xl border border-rose-200 bg-gradient-to-r from-rose-50 to-pink-50 px-3 py-2">
      {state.queryThumb && (
        <img
          src={state.queryThumb}
          alt="查询图"
          className="h-10 w-10 rounded-md border border-rose-200 object-cover"
        />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] font-medium text-rose-800">
            {state.busy ? '正在跨库以图搜图…' : '以图搜库结果'}
          </p>
          <span className="font-mono text-[10px] text-rose-700/80">
            {state.done}/{state.total}
          </span>
        </div>
        <div className="relative mt-1 h-1 overflow-hidden rounded-full bg-white/60">
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-rose-400 to-pink-500 transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="mt-1 text-[10px] text-rose-700/70">
          {state.busy
            ? '查询并发进行中,完成的库会立即高亮显示'
            : state.scores.size > 0
              ? `命中 ${state.scores.size} 个图像知识库,已按相似度排序前置`
              : state.error || '没有找到相似图片'}
        </p>
      </div>
      {state.busy ? (
        <button
          type="button"
          onClick={onCancel}
          className="inline-flex h-7 items-center gap-1 rounded-full border border-rose-200 bg-white px-2.5 text-[11px] text-rose-700 hover:bg-rose-100"
        >
          停止
        </button>
      ) : (
        <button
          type="button"
          onClick={onClear}
          className="inline-flex h-7 items-center gap-1 rounded-full border border-rose-200 bg-white px-2.5 text-[11px] text-rose-700 hover:bg-rose-100"
        >
          <XIcon className="h-3 w-3" /> 退出搜索
        </button>
      )}
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// 右侧 chip rail
// ──────────────────────────────────────────────────────────────

const KbScopeRail: React.FC<{
  selected: string[];
  itemsByKuId: Map<string, KuItem>;
  onExpand(): void;
}> = ({ selected, itemsByKuId, onExpand }) => {
  const { t } = useTranslation();
  const label = t('knowledge.rail.collapsed', { count: selected.length });
  return (
    <aside className="flex h-full w-10 flex-col items-center gap-2 border-l border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] py-3">
      <button
        type="button"
        onClick={onExpand}
        className="flex h-8 w-8 items-center justify-center rounded-full text-[var(--cy-text-secondary)] transition-colors hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)]"
        aria-label={label}
        title={label}
      >
        <PanelRightOpen className="h-4 w-4" />
      </button>
      {selected.length > 0 && (
        <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--cy-brand-600)] px-1 text-[10px] font-semibold text-white">
          {selected.length}
        </span>
      )}
      <div className="mt-2 flex flex-1 flex-col gap-1.5 overflow-y-auto px-1">
        {selected.map((id) => {
          const it = itemsByKuId.get(id);
          const kind = it?.kind ?? 'document';
          const Icon = KIND_META[kind].icon;
          return (
            <button
              key={id}
              type="button"
              onClick={onExpand}
              className={cn(
                'flex h-6 w-6 items-center justify-center rounded-md text-white ring-1 ring-white transition-transform hover:scale-110 bg-gradient-to-br',
                KIND_META[kind].listTint,
              )}
              title={it?.display_name ?? id}
              aria-label={it?.display_name ?? id}
            >
              <Icon className="h-3 w-3" />
            </button>
          );
        })}
      </div>
    </aside>
  );
};

// ──────────────────────────────────────────────────────────────
// Empty / Skeleton / Error
// ──────────────────────────────────────────────────────────────

const ErrorState: React.FC<{ error: Error; onRetry(): void }> = ({ error, onRetry }) => {
  const { t } = useTranslation();
  const detail = (error as { detail?: string; message?: string }).message || String(error);
  return (
    <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 px-4 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--cy-danger-50,#fef2f2)] text-[var(--cy-danger-600,#dc2626)]">
        <AlertCircle className="h-6 w-6" />
      </div>
      <p className="text-sm font-medium text-[var(--cy-text-primary)]">知识库加载失败</p>
      <p className="max-w-md text-xs text-[var(--cy-text-secondary)]">{detail}</p>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex h-8 items-center gap-1.5 rounded-full border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-3 text-xs text-[var(--cy-text-primary)] transition-colors hover:bg-[var(--cy-surface-2)]"
      >
        <RefreshCw className="h-3.5 w-3.5" />
        {t('common.refresh')}
      </button>
    </div>
  );
};

const EmptyState: React.FC<{ filtered: boolean }> = ({ filtered }) => {
  const { t } = useTranslation();
  return (
    <div className="flex h-full min-h-[200px] flex-col items-center justify-center gap-3 text-center">
      {/* 浮动的彩色 kind 图标群作装饰 */}
      <div className="relative h-20 w-32" aria-hidden>
        <span
          className="absolute left-1/2 top-1 flex h-12 w-12 -translate-x-1/2 items-center justify-center rounded-2xl shadow-md"
          style={{ background: 'var(--cy-iris-gradient)', animation: 'kbFloatX 3.2s ease-in-out infinite' }}
        >
          <Library className="h-6 w-6 text-white" />
        </span>
        <span
          className="absolute left-2 top-7 flex h-7 w-7 items-center justify-center rounded-xl text-white shadow-sm bg-gradient-to-br from-sky-400 to-indigo-500"
          style={{ animation: 'kbFloat 3.6s ease-in-out infinite .25s' }}
        >
          <FileText className="h-3.5 w-3.5" />
        </span>
        <span
          className="absolute right-2 top-7 flex h-7 w-7 items-center justify-center rounded-xl text-white shadow-sm bg-gradient-to-br from-pink-400 to-rose-500"
          style={{ animation: 'kbFloat 3.4s ease-in-out infinite .5s' }}
        >
          <Images className="h-3.5 w-3.5" />
        </span>
        <span
          className="absolute left-9 top-12 flex h-7 w-7 items-center justify-center rounded-xl text-white shadow-sm bg-gradient-to-br from-emerald-400 to-teal-500"
          style={{ animation: 'kbFloat 3.8s ease-in-out infinite .75s' }}
        >
          <Database className="h-3.5 w-3.5" />
        </span>
        <span
          className="absolute right-9 top-12 flex h-7 w-7 items-center justify-center rounded-xl text-white shadow-sm bg-gradient-to-br from-amber-400 to-orange-500"
          style={{ animation: 'kbFloat 3.5s ease-in-out infinite 1s' }}
        >
          <Boxes className="h-3.5 w-3.5" />
        </span>
      </div>
      <p className="text-sm font-medium text-[var(--cy-text-primary)]">
        {filtered ? t('common.noData') : t('knowledge.empty')}
      </p>
      {!filtered && (
        <p className="max-w-sm text-xs text-[var(--cy-text-secondary)]">
          {t('knowledge.emptyHint')}
        </p>
      )}
      <style>{`@keyframes kbFloat{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}@keyframes kbFloatX{0%,100%{transform:translate(-50%,0)}50%{transform:translate(-50%,-3px)}}`}</style>
    </div>
  );
};

const SkeletonGrid: React.FC<{ view: ViewMode }> = ({ view }) => {
  if (view === 'list') {
    return (
      <div className="overflow-hidden rounded-xl border border-[var(--cy-border-subtle)]">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-12 animate-pulse border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]" />
        ))}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="h-36 animate-pulse rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)]" />
      ))}
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// 右键菜单
// ──────────────────────────────────────────────────────────────

type KbContextMenuState =
  | { x: number; y: number; target: { type: 'kb'; item: KuItem } }
  | { x: number; y: number; target: { type: 'blank' } }
  | null;

const KbContextMenu: React.FC<{
  menu: KbContextMenuState;
  groups: KbLocalGroup[];
  onClose(): void;
  onOpenKb(item: KuItem): void;
  onEditKb(item: KuItem): void;
  onDeleteKb(item: KuItem): void;
  onCreateGroup(): string | null;
  onMoveToGroup(item: KuItem, groupId: string | null): void;
}> = ({ menu, groups, onClose, onOpenKb, onEditKb, onDeleteKb, onCreateGroup, onMoveToGroup }) => {
  React.useEffect(() => {
    if (!menu) return;
    const close = () => onClose();
    window.addEventListener('click', close);
    window.addEventListener('keydown', close);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('keydown', close);
    };
  }, [menu, onClose]);

  if (!menu) return null;
  const kbItem = menu.target.type === 'kb' ? menu.target.item : null;
  const run = (fn: () => void) => {
    fn();
    onClose();
  };
  const rowClass = 'flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-xs text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-2)] hover:text-[var(--cy-text-primary)] disabled:cursor-not-allowed disabled:opacity-50';
  return (
    <div
      role="menu"
      className="fixed z-[80] min-w-[190px] rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-1 shadow-[var(--cy-shadow-lg)]"
      style={{ left: menu.x, top: menu.y }}
      onContextMenu={(event) => event.preventDefault()}
    >
      {!kbItem ? (
        <button type="button" role="menuitem" className={rowClass} onClick={() => run(() => { onCreateGroup(); })}>
          <Plus className="h-3.5 w-3.5" />
          新建分组
        </button>
      ) : (
        <>
          <button type="button" role="menuitem" className={rowClass} onClick={() => run(() => onOpenKb(kbItem))}>
            <Library className="h-3.5 w-3.5" />
            打开
          </button>
          <button
            type="button"
            role="menuitem"
            className={rowClass}
            disabled={!kbItem.mine}
            onClick={() => run(() => onEditKb(kbItem))}
          >
            <Edit3 className="h-3.5 w-3.5" />
            编辑
          </button>
          <div className="my-1 h-px bg-[var(--cy-border-subtle)]" />
          <div className="px-3 py-1 text-[10px] text-[var(--cy-text-tertiary)]">移到分组</div>
          {groups.map((g) => (
            <button
              key={g.id}
              type="button"
              role="menuitem"
              className={rowClass}
              onClick={() => run(() => onMoveToGroup(kbItem, g.id))}
            >
              {g.name}
            </button>
          ))}
          <button
            type="button"
            role="menuitem"
            className={rowClass}
            onClick={() => run(() => {
              const id = onCreateGroup();
              if (id) onMoveToGroup(kbItem, id);
            })}
          >
            <Plus className="h-3.5 w-3.5" />
            新建分组并移入
          </button>
          {groups.length > 0 && (
            <button
              type="button"
              role="menuitem"
              className={rowClass}
              onClick={() => run(() => onMoveToGroup(kbItem, null))}
            >
              移出分组
            </button>
          )}
          <div className="my-1 h-px bg-[var(--cy-border-subtle)]" />
          <button
            type="button"
            role="menuitem"
            className={cn(rowClass, 'text-red-600 hover:text-red-700')}
            disabled={!kbItem.mine}
            onClick={() => run(() => onDeleteKb(kbItem))}
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除
          </button>
        </>
      )}
    </div>
  );
};

// ──────────────────────────────────────────────────────────────
// Helper
// ──────────────────────────────────────────────────────────────

function formatRelative(time: string | null): string | null {
  if (!time) return null;
  const t = Date.parse(time);
  if (Number.isNaN(t)) return null;
  const delta = Date.now() - t;
  const day = 24 * 60 * 60 * 1000;
  if (delta < day) return '今天';
  if (delta < 2 * day) return '昨天';
  if (delta < 30 * day) return `${Math.floor(delta / day)} 天前`;
  if (delta < 365 * day) return `${Math.floor(delta / (30 * day))} 月前`;
  return `${Math.floor(delta / (365 * day))} 年前`;
}
