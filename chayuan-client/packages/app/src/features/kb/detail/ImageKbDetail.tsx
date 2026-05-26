/**
 * 图像型 KB 详情。
 *
 * 设计:
 *   - 顶部 toolbar:双模式搜索 tab(按文字 / 按图)+ 上传 + 删除
 *   - 网格 4-6 列(响应式),hover 显示 caption + 命中时显示 score badge
 *   - 点击图 → lightbox 大图(简易实现:全屏黑底 + 单图 + ESC/点击关闭)
 *   - owner 可上传 / 删除选中
 *   - 按文字搜图:调 searchByText → RRF fused hits,独立结果区展示
 *   - 以图搜图:上传一张参考图 → 调 /search_by_image → 命中按 score 降序前置 + 高亮边框
 *   - 上传后立即 onMutate 触发父组件重新 fetch,每 2.5s 轮询 pending item 状态
 */

import * as React from 'react';
import { AlertCircle, Download, Eye, FileText, ImageIcon, ImagePlus, Loader2, RefreshCw, Search, Settings2, Trash2, Type, X } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';
import { Button, cn } from '@chayuan/ui';
import {
  getAccessToken, getClientConfig, imageSource,
  type ImageItem, type ImageItemStatus, type ImageRrfHit, type ImageRrfSearchResponse,
  type ImageSearchHit, type KuImageDetail,
} from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import { notifySuccess, reportError } from '../../../store/errorDialog';
import { OcrTextDialog } from './OcrTextDialog';
import type { DetailPanelProps } from './types';

/** 把 ku_id='src:42' 解出 sourceId=42 */
function srcIdFromKuId(kuId: string): number | null {
  if (!kuId.startsWith('src:')) return null;
  const n = Number(kuId.slice(4));
  return Number.isFinite(n) ? n : null;
}

/** 给 image url 拼上 baseURL + token query(后端 image 端点接受 query token) */
async function absImageUrl(rel: string | undefined): Promise<string> {
  if (!rel) return '';
  if (/^https?:/i.test(rel)) return rel;
  const cfg = getClientConfig();
  const base = (cfg.baseURL || '').replace(/\/+$/, '');
  const tok = await getAccessToken();
  const sep = rel.includes('?') ? '&' : '?';
  return `${base}${rel}${tok ? `${sep}token=${encodeURIComponent(tok)}` : ''}`;
}

/** 从 hit.meta 抽 image_id;后端 image connector 会把 image_id 放在 citation.meta.image_id 或 meta.image_id */
function extractImageId(hit: ImageSearchHit): string | null {
  const meta = (hit.citation?.meta || {}) as Record<string, unknown>;
  const id = meta.image_id ?? meta.id ?? meta.filename;
  if (typeof id === 'string' && id) return id;
  return null;
}

export const ImageKbDetail: React.FC<DetailPanelProps<KuImageDetail>> = ({
  detail,
  mine,
  onMutate,
}) => {
  const sourceId = React.useMemo(() => srcIdFromKuId(detail.ku_id), [detail.ku_id]);
  const navigate = useNavigate();
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [preview, setPreview] = React.useState<ImageItem | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [keyword, setKeyword] = React.useState('');
  // score map by image_id;非空时表示进入"以图搜图"结果模式
  const [scoreMap, setScoreMap] = React.useState<Map<string, number> | null>(null);
  const [searching, setSearching] = React.useState(false);
  const [queryThumbUrl, setQueryThumbUrl] = React.useState<string | null>(null);
  const searchInputRef = React.useRef<HTMLInputElement>(null);

  // 搜索模式 tab
  const [searchMode, setSearchMode] = React.useState<'text' | 'image'>('text');
  const [textQuery, setTextQuery] = React.useState('');
  const [textResult, setTextResult] = React.useState<ImageRrfSearchResponse | null>(null);
  const [textSearching, setTextSearching] = React.useState(false);

  // 本地状态覆盖层:从 /status 轮询拿到的实时状态,合并到 detail.items 之上
  const [statusOverrides, setStatusOverrides] = React.useState<Map<string, Partial<ImageItem>>>(
    () => new Map(),
  );

  // 合并 statusOverrides 到 detail.items
  const items = React.useMemo(() => {
    const raw = detail.items ?? [];
    if (statusOverrides.size === 0) return raw;
    return raw.map((it) => {
      const o = statusOverrides.get(it.id);
      return o ? { ...it, ...o } : it;
    });
  }, [detail.items, statusOverrides]);

  // 「未能向量化」根因检测:有图片处于 failed / partial 且错误信息提到 PyTorch /
  // image-embedding 运行时不可用时,给一条可执行指引(去「设置 → 本地模型服务」装)。
  // 后端 pipeline._friendly_clip_error 已把诊断写进 item.error,这里只做归集。
  const torchFailureHint = React.useMemo<string | null>(() => {
    for (const it of items) {
      if (it.state !== 'failed' && it.state !== 'partial') continue;
      const err = it.error ?? '';
      if (
        err.includes('PyTorch') ||
        err.includes('image-embedding') ||
        err.includes('未安装 PyTorch') ||
        err.includes('本地模型服务')
      ) {
        return err;
      }
    }
    return null;
  }, [items]);

  // pipeline 终态:任意一个 → 不再轮询,且 UI 隐藏进度卡片
  // ready  = 至少一路向量化成功
  // failed = 早期硬失败(老版本可能写过)
  // partial= CLIP/OCR 全失败,图保留但无索引(pipeline 走完了,UI 别再转圈)
  const isTerminalState = (s?: string | null) =>
    s === 'ready' || s === 'failed' || s === 'partial';

  // 收集 pending 的 image id (state queued / ocr_and_embedding 等非终态)
  const pendingIds = React.useMemo(() => {
    return items
      .filter((it) => it.state && !isTerminalState(it.state))
      .map((it) => it.id);
  }, [items]);

  React.useEffect(() => {
    if (sourceId == null) return;
    if (pendingIds.length === 0) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const results = await Promise.all(
          pendingIds.map((id) =>
            imageSource.itemStatus(sourceId, id).catch(() => null as ImageItemStatus | null),
          ),
        );
        if (cancelled) return;
        setStatusOverrides((prev) => {
          const next = new Map(prev);
          for (let i = 0; i < pendingIds.length; i++) {
            const r = results[i];
            const pid = pendingIds[i];
            if (!r || pid == null) continue;
            // 终态直接清掉 override —— 避免 100% 进度残留(此前 onMutate 刷父组件
            // 还没落地时,本地 override 用老的 ocr_and_embedding/进度=100,触发
            // "永远停在 100%"的视觉 bug)
            if (isTerminalState(r.state)) {
              next.delete(pid);
            } else {
              next.set(pid, {
                state: r.state,
                progress: r.progress,
                error: r.error ?? null,
                has_text_vector: r.has_text_vector,
                ocr_text: r.ocr_text ?? null,
              });
            }
          }
          return next;
        });
        // 还有 pending 就继续轮询
        const stillPending = results.some(
          (r) => r && !isTerminalState(r.state),
        );
        if (!cancelled && stillPending) {
          timer = setTimeout(poll, 2500);
        } else if (!cancelled) {
          // 全部就绪,触发父组件重新拉数据,让 universe detail 也同步
          onMutate();
        }
      } catch {
        // 静默,下一次 effect 触发会重试
      }
    };
    timer = setTimeout(poll, 2500);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [sourceId, pendingIds.join(','), onMutate]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // 文字 + 以图搜图 双过滤;以图搜图模式下按 score 降序
  const displayItems = React.useMemo(() => {
    let arr: ImageItem[] = items;
    if (keyword.trim()) {
      const q = keyword.toLowerCase();
      arr = arr.filter(
        (it) =>
          it.id.toLowerCase().includes(q) ||
          (it.caption ?? '').toLowerCase().includes(q),
      );
    }
    if (scoreMap && scoreMap.size > 0) {
      // 以图搜图:只展示命中的图,按 score 降序;未命中的不显示
      arr = arr
        .filter((it) => scoreMap.has(it.id))
        .sort((a, b) => (scoreMap.get(b.id) ?? 0) - (scoreMap.get(a.id) ?? 0));
    }
    return arr;
  }, [items, keyword, scoreMap]);

  const onSearchByText = async () => {
    if (sourceId == null) return;
    const q = textQuery.trim();
    if (!q) return;
    setTextSearching(true);
    try {
      const resp = await imageSource.searchByText(sourceId, q, 20);
      setTextResult(resp);
    } catch (e) {
      reportError(e, '按文字搜图失败');
    } finally {
      setTextSearching(false);
    }
  };

  const onClearTextSearch = () => {
    setTextResult(null);
    setTextQuery('');
  };

  const onSearchByImage = async () => {
    if (sourceId == null) return;
    try {
      // 走 platform.fs.pickFiles 拿 File(只取 1 张)
      const picked = await getPlatform().fs.pickFiles({
        multiple: false,
        accept: ['image/*'],
      });
      if (!picked.length) return;
      const file = picked[0] as unknown as File;
      // 给查询图生成本地 thumb 显示
      try {
        const url = URL.createObjectURL(file);
        setQueryThumbUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
      } catch { /* ignore */ }

      setSearching(true);
      const hits = await imageSource.searchByImage(sourceId, file, 12);
      const map = new Map<string, number>();
      for (const h of hits) {
        const id = extractImageId(h);
        if (id) map.set(id, h.score ?? 0);
      }
      setScoreMap(map);
      if (map.size === 0) {
        notifySuccess('以图搜图:未找到相似图片');
      } else {
        notifySuccess(`以图搜图:命中 ${map.size} 张`);
      }
    } catch (e) {
      reportError(e, '以图搜图失败');
    } finally {
      setSearching(false);
    }
  };

  const onClearImageSearch = () => {
    setScoreMap(null);
    if (queryThumbUrl) {
      URL.revokeObjectURL(queryThumbUrl);
      setQueryThumbUrl(null);
    }
  };

  // 卸载时回收 objectURL
  React.useEffect(() => {
    return () => {
      if (queryThumbUrl) URL.revokeObjectURL(queryThumbUrl);
    };
  }, [queryThumbUrl]);

  const onUpload = async () => {
    if (!mine || sourceId == null) return;
    try {
      const picked = await getPlatform().fs.pickFiles({
        multiple: true,
        accept: ['image/*'],
      });
      if (!picked.length) return;
      setBusy(true);
      const r = await imageSource.upload(sourceId, picked);
      if (r.errors.length === 0) {
        notifySuccess(`已开始处理 ${r.added.length} 张图片(OCR + 向量化进行中)`);
      } else {
        notifySuccess(`已开始处理 ${r.added.length} 张,失败 ${r.errors.length} 张`);
      }
      onMutate();
    } catch (e) {
      reportError(e, '上传图像失败');
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async () => {
    if (!mine || selected.size === 0 || sourceId == null) return;
    const ok0 = await getPlatform().dialog.confirm(
      `删除选中的 ${selected.size} 张图片?该操作不可恢复`,
      { title: '删除图片' },
    );
    if (!ok0) return;
    setBusy(true);
    try {
      // 串行删,避免并发冲击 store._meta;失败的继续
      let okCount = 0;
      for (const id of selected) {
        const ok = await imageSource.remove(sourceId, id).catch(() => false);
        if (ok) okCount += 1;
      }
      notifySuccess(`已删除 ${okCount} 张${okCount < selected.size ? `(${selected.size - okCount} 失败)` : ''}`);
      setSelected(new Set());
      onMutate();
    } catch (e) {
      reportError(e, '删除失败');
    } finally {
      setBusy(false);
    }
  };

  const onDeleteOne = React.useCallback(async (img: ImageItem) => {
    if (!mine || sourceId == null) return;
    const ok0 = await getPlatform().dialog.confirm(
      `删除「${img.caption || img.id}」?该操作不可恢复`,
      { title: '删除图片' },
    );
    if (!ok0) return;
    setBusy(true);
    try {
      const ok = await imageSource.remove(sourceId, img.id).catch(() => false);
      if (ok) {
        notifySuccess('已删除');
        // 把选中里面残留这张图的 id 清掉
        setSelected((s) => {
          if (!s.has(img.id)) return s;
          const next = new Set(s);
          next.delete(img.id);
          return next;
        });
        onMutate();
      } else {
        reportError(new Error('imageSource.remove 返回 false'), '删除失败');
      }
    } catch (e) {
      reportError(e, '删除失败');
    } finally {
      setBusy(false);
    }
  }, [mine, sourceId, onMutate]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-4 py-2">
        <span className="text-xs text-[var(--cy-text-tertiary)]">
          {scoreMap ? `${displayItems.length} / ${items.length}` : `${items.length}`} 张图片
          {selected.size > 0 && <span className="ml-2 text-[var(--cy-text-secondary)]">已选 {selected.size}</span>}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          {/* 模式 tab */}
          <div className="inline-flex h-8 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-0.5 text-xs">
            <button
              type="button"
              onClick={() => setSearchMode('text')}
              className={cn(
                'inline-flex items-center gap-1 rounded-full px-3 transition',
                searchMode === 'text'
                  ? 'bg-[var(--cy-brand-500)] text-white shadow'
                  : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-1)]',
              )}
            >
              <Type className="h-3 w-3" /> 按文字
            </button>
            <button
              type="button"
              onClick={() => setSearchMode('image')}
              className={cn(
                'inline-flex items-center gap-1 rounded-full px-3 transition',
                searchMode === 'image'
                  ? 'bg-[var(--cy-brand-500)] text-white shadow'
                  : 'text-[var(--cy-text-secondary)] hover:bg-[var(--cy-surface-1)]',
              )}
            >
              <ImageIcon className="h-3 w-3" /> 按图
            </button>
          </div>

          {searchMode === 'text' ? (
            <div className="flex items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
                <input
                  ref={searchInputRef}
                  value={textQuery}
                  onChange={(e) => setTextQuery(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void onSearchByText(); }}
                  placeholder="例如:发票 / 红色汽车 / 山水画"
                  className="h-8 w-56 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] pl-7 pr-3 text-xs focus-visible:outline-none focus-visible:border-[var(--cy-brand-400)]"
                />
              </div>
              <Button
                size="sm"
                onClick={() => void onSearchByText()}
                disabled={textSearching || !textQuery.trim() || sourceId == null}
                className="h-8 text-xs"
              >
                {textSearching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                搜索
              </Button>
              {textResult && (
                <Button size="sm" variant="ghost" onClick={onClearTextSearch} className="h-8 text-xs">
                  <X className="h-3.5 w-3.5" /> 清除
                </Button>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--cy-text-tertiary)]" />
                <input
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                  placeholder="按 caption / id 过滤"
                  className="h-8 w-44 rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] pl-7 pr-3 text-xs focus-visible:outline-none focus-visible:border-[var(--cy-brand-400)]"
                />
              </div>
              <Button
                size="sm"
                variant={scoreMap ? 'default' : 'outline'}
                onClick={() => void onSearchByImage()}
                disabled={searching || sourceId == null}
                className="h-8 text-xs"
                title="上传一张参考图,按相似度反查本库"
              >
                {searching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ImageIcon className="h-3.5 w-3.5" />}
                {searching ? '搜索中…' : '以图搜图'}
              </Button>
            </div>
          )}

          <Button size="icon" variant="ghost" onClick={onMutate} aria-label="刷新" className="h-8 w-8">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          {mine && selected.size > 0 && (
            <Button size="sm" variant="ghost" className="h-8 text-xs text-destructive" onClick={() => void onDelete()} disabled={busy}>
              <Trash2 className="h-3.5 w-3.5" /> 删除
            </Button>
          )}
          {mine && (
            <Button size="sm" onClick={() => void onUpload()} className="h-8 text-xs" disabled={busy}>
              <ImagePlus className="h-3.5 w-3.5" /> 上传
            </Button>
          )}
        </div>
      </header>

      {/* 以图搜图结果条:展示查询缩略图 + 命中数 + 清除按钮 */}
      {scoreMap && (
        <div className="flex items-center gap-3 border-b border-[var(--cy-border-subtle)] bg-gradient-to-r from-pink-50 to-rose-50 px-4 py-2">
          {queryThumbUrl && (
            <img
              src={queryThumbUrl}
              alt="查询图"
              className="h-10 w-10 rounded-md border border-pink-200 object-cover"
            />
          )}
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium text-rose-800">以图搜图结果</p>
            <p className="text-[10px] text-rose-700/80">
              命中 {scoreMap.size} 张相似图片,按相似度排序
            </p>
          </div>
          <button
            type="button"
            onClick={onClearImageSearch}
            className="inline-flex h-7 items-center gap-1 rounded-full border border-rose-200 bg-white px-2.5 text-[11px] text-rose-700 hover:bg-rose-100"
          >
            <X className="h-3 w-3" /> 退出搜索
          </button>
        </div>
      )}

      {/* 按文字搜图结果区 */}
      {textResult && (
        <div className="border-b border-[var(--cy-border-subtle)] bg-gradient-to-r from-violet-50 to-fuchsia-50 px-4 py-3">
          <div className="mb-2 flex items-center gap-3">
            <Type className="h-4 w-4 text-violet-700" />
            <div className="min-w-0 flex-1">
              <p className="text-[11px] font-medium text-violet-800">
                按文字搜图:&ldquo;{textQuery}&rdquo; — {textResult.hits.length} 张相关
              </p>
              {/* 命中诊断:后端已经把 keyword_path / text_path / image_path 都
                  改成中文人话(例:"按关键词命中 3 张" / "按 OCR 文字未命中" /
                  "视觉跨模态搜索不可用:..."),直接平铺展示即可。 */}
              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-violet-700/80">
                <span>· {textResult.diagnostics.keyword_path}</span>
                <span>· {textResult.diagnostics.text_path}</span>
                <span>· {textResult.diagnostics.image_path}</span>
              </p>
              {textResult.diagnostics.summary && (
                <p className="mt-0.5 inline-flex items-center gap-1 text-[10px] text-amber-700">
                  <AlertCircle className="h-3 w-3" />
                  {textResult.diagnostics.summary}
                </p>
              )}
            </div>
          </div>
          {textResult.hits.length === 0 ? (
            <p className="text-xs text-violet-700/70">未找到相关图片</p>
          ) : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">
              {textResult.hits.map((h) => (
                <RrfHitCard key={h.id} hit={h} sourceId={sourceId ?? 0} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* 「未能向量化」根因指引 —— 失败根因是 PyTorch / image-embedding 运行时
          不可用时,明确告诉用户该去哪装,并提供直达「本地模型服务」的入口。 */}
      {torchFailureHint && (
        <div className="flex items-start gap-3 border-b border-amber-200 bg-amber-50 px-4 py-2.5 dark:border-amber-900/50 dark:bg-amber-950/40">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-xs font-medium text-amber-800 dark:text-amber-200">
              部分图片未能向量化
            </p>
            <p className="text-[11px] leading-relaxed text-amber-700 dark:text-amber-300">
              {torchFailureHint}
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              void navigate({ to: '/settings', hash: 'local-runtime-pytorch' });
            }}
            className="inline-flex h-7 shrink-0 items-center gap-1 rounded-full border border-amber-300 bg-white px-2.5 text-[11px] font-medium text-amber-700 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-900/40 dark:text-amber-200"
            title="跳转到「设置 → 本地模型服务 → PyTorch」"
          >
            <Settings2 className="h-3.5 w-3.5" />
            去安装 PyTorch
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4">
        {/* 以文搜图(textResult)激活时,命中结果已在上方独立结果区展示,
            主网格隐藏 —— 搜索时只显示命中的图,不再列出全部图片。 */}
        {textResult ? null : items.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
            还没有图片
          </div>
        ) : displayItems.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
            没有匹配的图片
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {displayItems.map((img) => (
              <ImageCard
                key={img.id}
                img={img}
                score={scoreMap?.get(img.id)}
                checked={selected.has(img.id)}
                showCheckbox={mine}
                canDelete={mine}
                onToggle={() => toggle(img.id)}
                onPreview={() => setPreview(img)}
                onDelete={() => void onDeleteOne(img)}
              />
            ))}
          </div>
        )}
      </div>

      {preview && <Lightbox img={preview} onClose={() => setPreview(null)} />}
    </div>
  );
};

const ImageCard: React.FC<{
  img: ImageItem;
  score?: number;
  checked: boolean;
  showCheckbox: boolean;
  canDelete: boolean;
  onToggle(): void;
  onPreview(): void;
  onDelete(): void;
}> = ({ img, score, checked, showCheckbox, canDelete, onToggle, onPreview, onDelete }) => {
  const [src, setSrc] = React.useState<string>('');
  const [ocrOpen, setOcrOpen] = React.useState(false);
  React.useEffect(() => {
    let live = true;
    void absImageUrl(img.thumb_url || img.url).then((u) => {
      if (live) setSrc(u);
    });
    return () => { live = false; };
  }, [img.thumb_url, img.url]);
  const isHit = score != null;
  return (
    <div
      className={cn(
        'group relative aspect-square overflow-hidden rounded-lg border bg-[var(--cy-surface-1)] transition-all',
        isHit
          ? 'border-rose-300 shadow-[0_0_0_2px_rgba(244,63,94,0.25)]'
          : 'border-[var(--cy-border-subtle)]',
      )}
    >
      {src ? (
        <img
          src={src}
          alt={img.caption || img.id}
          className="h-full w-full cursor-pointer object-cover transition-transform group-hover:scale-105"
          onClick={onPreview}
          loading="lazy"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-xs text-[var(--cy-text-tertiary)]">
          加载中…
        </div>
      )}
      {showCheckbox && (
        <label className="absolute left-1.5 top-1.5 flex h-5 w-5 cursor-pointer items-center justify-center rounded bg-white/85 backdrop-blur opacity-0 transition-opacity group-hover:opacity-100 has-[:checked]:opacity-100">
          <input
            type="checkbox"
            checked={checked}
            onChange={onToggle}
            className="h-3.5 w-3.5 accent-[var(--cy-brand-500)]"
          />
        </label>
      )}
      {/* 处理状态 badge —— 只在真正"还在跑"时显示。pipeline 终态(ready/failed/partial)
          都不再转圈,避免 100% 视觉残留 */}
      {img.state && img.state !== 'ready' && img.state !== 'failed' && img.state !== 'partial' && (img.progress ?? 0) < 100 && (
        <div className="absolute inset-x-1.5 bottom-1.5 rounded-md bg-black/65 px-2 py-1 backdrop-blur">
          <div className="flex items-center gap-1 text-[10px] text-white">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span className="flex-1 truncate">
              {(img.progress ?? 0) < 70
                ? '视觉向量化中'
                : (img.progress ?? 0) < 95
                ? 'OCR 提取中'
                : '文本向量化中'}
            </span>
            <span>{img.progress ?? 0}%</span>
          </div>
          <div className="mt-0.5 h-1 w-full overflow-hidden rounded-full bg-white/20">
            <div
              className="h-full bg-emerald-400 transition-[width]"
              style={{ width: `${Math.min(100, img.progress ?? 0)}%` }}
            />
          </div>
        </div>
      )}
      {img.state === 'failed' && (
        <span
          className={cn(
            'absolute top-1.5 inline-flex items-center gap-1 rounded-full bg-red-600/90 px-1.5 py-0.5 text-[9px] font-semibold text-white shadow-sm',
            // 有选择框时往右让位,别盖住左上角的复选框
            showCheckbox ? 'left-8' : 'left-1.5',
          )}
          title={img.error ?? '失败'}
        >
          <AlertCircle className="h-3 w-3" /> 失败
        </span>
      )}
      {img.state === 'partial' && (
        <span
          className={cn(
            'absolute top-1.5 inline-flex items-center gap-1 rounded-full bg-amber-600/90 px-1.5 py-0.5 text-[9px] font-semibold text-white shadow-sm',
            // 有选择框时往右让位,别盖住左上角的复选框
            showCheckbox ? 'left-8' : 'left-1.5',
          )}
          title={img.error ?? '图像向量化未完成,以图搜图不可用'}
        >
          <AlertCircle className="h-3 w-3" /> 未索引
        </span>
      )}
      {/* 「含文字」徽标:只要识别出了 OCR 文字就显示 —— ready(已索引)和
          partial(图像向量化失败但 OCR 出了文字)都要能看 / 复制这段文字。 */}
      {(img.state === 'ready' || img.state === 'partial') && img.ocr_text && (
        <>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setOcrOpen(true);
            }}
            title="点击查看 / 复制图片文字"
            className="absolute right-1.5 bottom-1.5 inline-flex items-center gap-1 rounded-full bg-blue-500/90 px-1.5 py-0.5 text-[9px] font-semibold text-white shadow-sm transition-colors hover:bg-blue-500"
          >
            <FileText className="h-3 w-3" /> 含文字
          </button>
          <OcrTextDialog open={ocrOpen} onOpenChange={setOcrOpen} text={img.ocr_text} />
        </>
      )}
      {img.state === 'ready' && img.has_text_vector === false && img.ocr_text && (
        <span
          className="absolute right-1.5 top-1.5 inline-flex items-center gap-1 rounded-full bg-amber-500/90 px-1.5 py-0.5 text-[9px] font-semibold text-white shadow-sm"
          title="默认文本向量模型未就绪,本图仅按视觉检索"
        >
          仅图搜
        </span>
      )}
      {/* 命中分数 badge */}
      {isHit && (
        <span className="absolute left-1.5 bottom-1.5 inline-flex items-center gap-1 rounded-full bg-rose-500/90 px-1.5 py-0.5 text-[9px] font-semibold text-white shadow-sm backdrop-blur">
          <span>{(score * 100).toFixed(0)}</span>
          <span className="opacity-80">分</span>
        </span>
      )}
      {img.caption && (
        <div className="pointer-events-none absolute bottom-0 left-0 right-0 truncate bg-gradient-to-t from-black/70 to-transparent px-2 py-1 text-[10px] text-white opacity-0 group-hover:opacity-100">
          {img.caption}
        </div>
      )}
      <div className="absolute right-1.5 top-1.5 flex gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          type="button"
          onClick={onPreview}
          aria-label="查看大图"
          className="flex h-6 w-6 items-center justify-center rounded-full bg-black/40 text-white backdrop-blur hover:bg-black/60"
        >
          <Eye className="h-3 w-3" />
        </button>
        {canDelete && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            aria-label="删除"
            title="删除这张图片"
            className="flex h-6 w-6 items-center justify-center rounded-full bg-red-500/85 text-white backdrop-blur hover:bg-red-600"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        )}
      </div>
    </div>
  );
};

const RrfHitCard: React.FC<{ hit: ImageRrfHit; sourceId: number }> = ({ hit }) => {
  const [src, setSrc] = React.useState<string>('');
  React.useEffect(() => {
    let live = true;
    void absImageUrl(hit.thumbnail_url).then((u) => { if (live) setSrc(u); });
    return () => { live = false; };
  }, [hit.thumbnail_url]);
  const pathLabel: Record<ImageRrfHit['source_path'], string> = {
    text_vec: '文本',
    clip_text: '视觉',
    clip_image: '图',
    fused: '融合',
  };
  return (
    <div className="group relative aspect-square overflow-hidden rounded-md border border-violet-200 bg-white">
      {src ? (
        <img
          src={src}
          alt={hit.filename}
          className="h-full w-full object-cover"
          loading="lazy"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-[10px] text-[var(--cy-text-tertiary)]">…</div>
      )}
      <span className="absolute left-1 top-1 inline-flex items-center rounded-full bg-violet-500/95 px-1.5 py-0.5 text-[9px] font-semibold text-white shadow-sm">
        {pathLabel[hit.source_path] ?? hit.source_path}
      </span>
      {hit.ocr_snippet && (
        <div className="pointer-events-none absolute bottom-0 left-0 right-0 truncate bg-gradient-to-t from-black/70 to-transparent px-1.5 py-0.5 text-[9px] text-white opacity-0 group-hover:opacity-100" title={hit.ocr_snippet}>
          {hit.ocr_snippet}
        </div>
      )}
    </div>
  );
};

const Lightbox: React.FC<{ img: ImageItem; onClose(): void }> = ({ img, onClose }) => {
  const [src, setSrc] = React.useState<string>('');
  React.useEffect(() => {
    let live = true;
    void absImageUrl(img.url || img.thumb_url).then((u) => {
      if (live) setSrc(u);
    });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      live = false;
      window.removeEventListener('keydown', onKey);
    };
  }, [onClose, img.url, img.thumb_url]);
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/85 p-8"
      onClick={onClose}
    >
      {src ? (
        <img
          src={src}
          alt={img.caption || img.id}
          className="max-h-full max-w-full rounded-lg object-contain shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <div className="text-sm text-white/60">加载中…</div>
      )}
      <button
        type="button"
        onClick={onClose}
        aria-label="关闭"
        className="absolute right-4 top-4 flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20"
      >
        <X className="h-5 w-5" />
      </button>
      {src && (
        <a
          href={src}
          download={img.id}
          className="absolute bottom-6 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-white/10 px-4 py-2 text-xs text-white backdrop-blur hover:bg-white/20"
          onClick={(e) => e.stopPropagation()}
        >
          <Download className="h-3.5 w-3.5" /> 下载原图
        </a>
      )}
    </div>
  );
};
