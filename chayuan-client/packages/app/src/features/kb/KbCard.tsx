/**
 * KbCard —— 知识库卡片(对齐 界面参考/知识库.jpg 设计稿)。
 *
 * 设计要点:
 *   - 白底 / soft border / hover 微浮起;不再用渐变条压顶
 *   - 第一行:粗标题(font-medium)
 *   - 第二行:描述 line-clamp-3,~12px,text-tertiary
 *   - 第三行:kind chip(PDF/IMG/DB/VEC,带颜色)+ 创建日期
 *   - 角标:右上 mine pill / hover 才出 checkbox(多选)
 *   - 选中态:整卡边框 + 右上勾选;以图搜库命中:玫粉环 + 分数 badge
 *
 * 可达性:整卡是 button,checkbox 单独按钮(stopPropagation 不冒泡到卡片 onOpen)。
 */

import * as React from 'react';
import { Boxes, CheckCheck, Database, FileText, Images, Lock } from 'lucide-react';
import { cn } from '@chayuan/ui';
import { getAccessToken, getClientConfig, type KuItem, type KuKind } from '@chayuan/api';
import { KbDropZone } from './upload/KbDropZone';
import { KbProgressBar } from './components/KbProgressBar';
import { DbLogo, resolveDbBrand } from './DbLogo';

interface KindMeta {
  icon: React.ComponentType<{ className?: string }>;
  /** 类型 chip 文字 */
  label: string;
  /** "20 文件" 这种单位 */
  unit: string;
  /** chip 颜色 token(class 名) */
  chipBg: string;
  chipText: string;
  /** 角图标颜色 */
  iconBg: string;
  iconText: string;
}

interface KindGradient {
  /** cover 区 Tailwind 渐变 class — 用 kind 主色到浅色 */
  coverBg: string;
  /** cover 区 icon 颜色 */
  coverIconColor: string;
}

const KIND: Record<KuKind, KindMeta & KindGradient> = {
  document: {
    icon: FileText,
    label: 'PDF',
    unit: '文件',
    chipBg: 'bg-sky-50',
    chipText: 'text-sky-700',
    iconBg: 'bg-sky-50',
    iconText: 'text-sky-600',
    coverBg: 'bg-gradient-to-br from-sky-100 to-sky-50 dark:from-sky-900/40 dark:to-sky-950/20',
    coverIconColor: 'text-sky-600/70 dark:text-sky-300/80',
  },
  image: {
    icon: Images,
    label: 'IMG',
    unit: '图片',
    chipBg: 'bg-rose-50',
    chipText: 'text-rose-700',
    iconBg: 'bg-rose-50',
    iconText: 'text-rose-600',
    coverBg: 'bg-gradient-to-br from-rose-100 to-rose-50 dark:from-rose-900/40 dark:to-rose-950/20',
    coverIconColor: 'text-rose-600/70 dark:text-rose-300/80',
  },
  structured: {
    icon: Database,
    label: 'DB',
    unit: '表',
    chipBg: 'bg-emerald-50',
    chipText: 'text-emerald-700',
    iconBg: 'bg-emerald-50',
    iconText: 'text-emerald-600',
    coverBg: '',  // structured 用品牌色,见 cover 渲染处
    coverIconColor: '',
  },
  vector: {
    icon: Boxes,
    label: 'VEC',
    unit: '集合',
    chipBg: 'bg-amber-50',
    chipText: 'text-amber-700',
    iconBg: 'bg-amber-50',
    iconText: 'text-amber-600',
    coverBg: 'bg-gradient-to-br from-amber-100 to-amber-50 dark:from-amber-900/40 dark:to-amber-950/20',
    coverIconColor: 'text-amber-600/70 dark:text-amber-300/80',
  },
};

export interface KbCardProps {
  kb: KuItem;
  selected: boolean;
  /** 以图搜库命中分数(0-1);非空 → 玫粉边框 + badge */
  imageScore?: number | null;
  /** 入场动画的次序 */
  index?: number;
  onToggle(): void;
  onOpen(): void;
}

export const KbCard: React.FC<KbCardProps> = ({
  kb,
  selected,
  imageScore = null,
  index = 0,
  onToggle,
  onOpen,
}) => {
  const meta = KIND[kb.kind];
  const Icon = meta.icon;
  const isImageHit = imageScore != null && imageScore > 0;
  const visibility = kb.visibility ?? 'private';
  const delay = Math.min(index, 15) * 25;

  return (
    <KbDropZone
      kb={kb}
      hint="corner"
      canUpload={!!kb.mine}
      className={cn(
        'group relative flex h-44 flex-col overflow-hidden rounded-xl border bg-[var(--cy-surface-base)] text-left transition-all duration-200',
        'hover:-translate-y-0.5 hover:shadow-[var(--cy-shadow-md)]',
        selected
          ? 'border-[var(--cy-brand-500)] shadow-[var(--cy-shadow-md)] ring-1 ring-[var(--cy-brand-300)]'
          : isImageHit
            ? 'border-rose-300 shadow-[0_0_0_2px_rgba(244,63,94,0.18)]'
            : 'border-[var(--cy-border-subtle)] hover:border-[var(--cy-brand-300)]',
      )}
    >
      <div
        style={{ animation: `kbCardIn .35s ease-out ${delay}ms backwards` }}
        className="relative flex h-full w-full flex-col"
      >
        {/* 多选 checkbox(默认隐藏,hover 或选中可见) */}
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          aria-label={selected ? '取消选择' : '选择'}
          className={cn(
            'absolute right-2 top-2 z-10 flex h-5 w-5 items-center justify-center rounded border bg-white/95 backdrop-blur transition-opacity',
            selected
              ? 'border-[var(--cy-brand-600)] bg-[var(--cy-brand-600)] text-white opacity-100'
              : 'border-[var(--cy-border-default)] opacity-0 group-hover:opacity-100',
          )}
        >
          {selected && <CheckCheck className="h-3 w-3" />}
        </button>

        {/* 主体可点击 — 加 cover 区后高度调整:cover 64px + 内容 80px */}
        <button type="button" onClick={onOpen} className="flex h-full w-full flex-col text-left">
          {/* Cover 区 — 按 kind 渲染不同封面 */}
          <KbCardCover kb={kb} meta={meta} />

          {/* 内容区 */}
          <div className="flex flex-1 flex-col px-3.5 pt-2 pb-2.5">
            {/* 第 1 行:粗标题 */}
            <p className="min-w-0 truncate pr-6 text-sm font-medium text-[var(--cy-text-primary)]">
              {kb.display_name}
            </p>

            {/* 第 2 行:描述(cover 占空间,描述压缩为 1 行) */}
            <p className="mt-0.5 line-clamp-1 flex-1 text-[11px] leading-relaxed text-[var(--cy-text-secondary)]">
              {kb.description || '—'}
            </p>

            {/* 第 3 行:chip + 元信息 */}
            <div className="mt-1.5 flex items-center justify-between gap-2 text-[10px] text-[var(--cy-text-tertiary)]">
              <span className={cn('inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold', meta.chipBg, meta.chipText)}>
                <Icon className="h-2.5 w-2.5" />
                {meta.label}
              </span>
              <span className="inline-flex items-center gap-2 opacity-80">
                {kb.count != null && (
                  <span>
                    {kb.count} {meta.unit}
                  </span>
                )}
                {visibility === 'private' && <Lock className="h-2.5 w-2.5" />}
                {kb.create_time && <span>{formatDate(kb.create_time)}</span>}
              </span>
            </div>
          </div>
        </button>

        {/* mine pill 角标 */}
        {kb.mine && (
          <span className="pointer-events-none absolute left-2 top-2 rounded-full bg-[var(--cy-brand-50)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--cy-brand-700)]">
            我的
          </span>
        )}

        {/* 以图搜库命中分数 badge */}
        {isImageHit && (
          <span className="pointer-events-none absolute right-2 bottom-2 z-10 inline-flex items-center gap-1 rounded-full bg-rose-500/95 px-1.5 py-0.5 text-[9px] font-semibold text-white shadow-sm">
            <span>{(imageScore * 100).toFixed(0)}</span>
            <span className="opacity-80">分</span>
          </span>
        )}

        {/* 上传进度条(下沿,只在有 jobs 时渲染) */}
        <KbProgressBar kuId={kb.ku_id} />
      </div>
    </KbDropZone>
  );
};

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${m}/${day}`;
}

/**
 * KB 卡片顶部封面区(64px 高)。按 kind 渲染:
 *   - structured → 数据库官方 logo + 中文品牌名,背景用品牌色淡化
 *   - document   → 大号文件 icon + 文件数 chip,渐变蓝;V2 替换成前 4 个文件缩略图九宫格
 *   - image      → 大号图集 icon + 图片数 chip,渐变粉;V2 替换成前 4 张图缩略图
 *   - vector     → 大号 Boxes icon + 维度/集合数,渐变琥珀
 *
 * V1 实现:全部用 icon + 数字 + 渐变背景;V2 改造时后端 list 端点返
 * cover_thumbnails: string[](<=4 个 URL)替代 document/image 的 icon。
 */
const KbCardCover: React.FC<{
  kb: KuItem;
  meta: KindMeta & KindGradient;
}> = ({ kb, meta }) => {
  if (kb.kind === 'structured') {
    // 优先用后端返的 dialect(2026-05-17 起 _source_to_universe 会查
    // connection.dialect 返出);老 server 没返 → fallback sub_kind +
    // description 关键字启发式
    const dialectHint = kb.dialect || inferStructuredDialect(kb);
    const brand = resolveDbBrand(dialectHint);
    return (
      <div
        className="relative flex h-16 w-full items-center justify-center overflow-hidden"
        style={{
          background: `linear-gradient(135deg, ${brand.color}22, ${brand.color}0a)`,
        }}
      >
        <DbLogo dialect={dialectHint} size={36} />
        <span
          className="absolute right-2 top-1.5 text-[10px] font-medium uppercase tracking-wide opacity-70"
          style={{ color: brand.color }}
        >
          {brand.name}
        </span>
      </div>
    );
  }

  // image KB:后端给了 cover_thumbnails 就用真图;否则渐变 + 大 icon 兜底
  if (kb.kind === 'image' && kb.cover_thumbnails && kb.cover_thumbnails.length > 0) {
    return <ImageCoverGrid urls={kb.cover_thumbnails} meta={meta} count={kb.count} />;
  }

  // document KB:后端给了 cover_files 就显示文件名 chip 九宫格;否则兜底
  if (kb.kind === 'document' && kb.cover_files && kb.cover_files.length > 0) {
    return <DocumentCoverGrid files={kb.cover_files} meta={meta} count={kb.count} />;
  }

  // 兜底:渐变 + 大 icon + 数字(没数据 / vector 类)
  const Icon = meta.icon;
  return (
    <div
      className={cn(
        'relative flex h-16 w-full items-center justify-center overflow-hidden',
        meta.coverBg,
      )}
    >
      <Icon className={cn('h-8 w-8', meta.coverIconColor)} />
      {kb.count != null && (
        <span
          className={cn(
            'absolute right-2 top-1.5 rounded-full bg-white/70 px-2 py-0.5 text-[10px] font-medium dark:bg-black/40',
            meta.chipText,
          )}
        >
          {kb.count} {meta.unit}
        </span>
      )}
    </div>
  );
};

/** image KB 封面九宫格:前 4 张图缩略图 + 右上角图片数 chip。 */
const ImageCoverGrid: React.FC<{
  urls: string[];
  meta: KindMeta & KindGradient;
  count: number | null;
}> = ({ urls, meta, count }) => {
  const slots = urls.slice(0, 4);
  // 不足 4 个补占位(后续保持 grid 形态)
  while (slots.length < 4) slots.push('');
  return (
    <div className={cn('relative grid h-16 w-full grid-cols-2 gap-px overflow-hidden', meta.coverBg)}>
      {slots.map((url, i) => (
        <CoverThumb key={i} url={url} />
      ))}
      {count != null && (
        <span className={cn(
          'absolute right-2 top-1.5 rounded-full bg-white/85 px-2 py-0.5 text-[10px] font-medium shadow-sm dark:bg-black/60',
          meta.chipText,
        )}>
          {count} {meta.unit}
        </span>
      )}
    </div>
  );
};

/** image 单格缩略图;空 URL 显示灰底占位;token 异步拼接好后 swap src。 */
const CoverThumb: React.FC<{ url: string }> = ({ url }) => {
  const [src, setSrc] = React.useState<string>('');
  React.useEffect(() => {
    let live = true;
    if (!url) { setSrc(''); return; }
    void (async () => {
      // 跟 ImageKbDetail.absImageUrl 同逻辑:走 baseURL + ?token= query
      // 图像 endpoint 接受 query token 因 <img> 不能自定义 header
      if (/^https?:/i.test(url)) {
        if (live) setSrc(url);
        return;
      }
      const cfg = getClientConfig();
      const base = (cfg.baseURL || '').replace(/\/+$/, '');
      const tok = await getAccessToken();
      const sep = url.includes('?') ? '&' : '?';
      if (live) setSrc(`${base}${url}${tok ? `${sep}token=${encodeURIComponent(tok)}` : ''}`);
    })();
    return () => { live = false; };
  }, [url]);
  if (!src) return <div className="bg-black/5 dark:bg-white/5" />;
  // 图片加载失败显示空灰底,不暴露错误 / 不打 console — 卡片封面是装饰,
  // 失败 silently degrade 体感更稳
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
      className="h-full w-full object-cover"
    />
  );
};

/** document KB 封面九宫格:前 4 个文件名 + 扩展名 chip。 */
const DocumentCoverGrid: React.FC<{
  files: NonNullable<KuItem['cover_files']>;
  meta: KindMeta & KindGradient;
  count: number | null;
}> = ({ files, meta, count }) => {
  const slots = files.slice(0, 4);
  return (
    <div className={cn('relative grid h-16 w-full grid-cols-2 gap-px overflow-hidden p-1', meta.coverBg)}>
      {slots.map((f, i) => (
        <div key={i} className="flex items-center gap-1 overflow-hidden rounded bg-white/70 px-1.5 py-0.5 dark:bg-black/30">
          <FileTypeIcon ext={f.ext} className="h-3 w-3 flex-shrink-0" />
          <span className="truncate text-[9px] font-medium text-[var(--cy-text-primary)]">
            {f.name}
          </span>
        </div>
      ))}
      {/* 不足 4 个补占位 */}
      {Array.from({ length: Math.max(0, 4 - slots.length) }).map((_, i) => (
        <div key={`pad-${i}`} className="rounded bg-white/30 dark:bg-black/15" />
      ))}
      {count != null && (
        <span className={cn(
          'absolute right-2 top-1.5 rounded-full bg-white/85 px-2 py-0.5 text-[10px] font-medium shadow-sm dark:bg-black/60',
          meta.chipText,
        )}>
          {count} {meta.unit}
        </span>
      )}
    </div>
  );
};

/** 文件类型颜色映射 — 跟 Office / 工程师社区习惯对齐。 */
const _EXT_COLOR: Record<string, string> = {
  pdf:  'text-red-600',
  doc:  'text-blue-600', docx: 'text-blue-600',
  xls:  'text-green-600', xlsx: 'text-green-600', csv: 'text-green-600',
  ppt:  'text-orange-600', pptx: 'text-orange-600',
  md:   'text-gray-700', txt: 'text-gray-700',
  html: 'text-orange-500', htm: 'text-orange-500',
  json: 'text-amber-600', yaml: 'text-amber-600', yml: 'text-amber-600',
  png:  'text-pink-600', jpg: 'text-pink-600', jpeg: 'text-pink-600', gif: 'text-pink-600',
};
const FileTypeIcon: React.FC<{ ext: string; className?: string }> = ({ ext, className }) => {
  const color = _EXT_COLOR[ext] || 'text-gray-500';
  return <FileText className={cn(className, color)} />;
};

/**
 * structured KB:试图从 KuItem 提取 dialect(mysql / postgresql / mongodb / ...)
 *
 * 启发式:
 *   1) sub_kind 已经是 mongo / es 时直接用;
 *   2) description / display_name 里关键字(mysql / postgres / sqlite 等)
 *      模糊命中;
 *   3) 都没命中返 sub_kind(让 resolveDbBrand 走通用 fallback)。
 *
 * V2 后端 list 端点直接返 dialect 字段时,前端可读 (kb as any).dialect 优先;
 * 这里只做 starts-with 不做精确匹配,容错 yaml 写歪。
 */
function inferStructuredDialect(kb: KuItem): string {
  const dialect = (kb as KuItem & { dialect?: string }).dialect;
  if (dialect) return dialect;
  const sub = kb.sub_kind || '';
  if (sub === 'mongo' || sub === 'es') return sub;
  const haystack = `${kb.display_name} ${kb.description}`.toLowerCase();
  for (const key of [
    'postgresql', 'postgres', 'mysql', 'mariadb', 'sqlite',
    'mongodb', 'mongo', 'elasticsearch', 'redis', 'clickhouse',
    'sqlserver', 'mssql', 'oracle', 'kingbase', 'dameng',
  ]) {
    if (haystack.includes(key)) return key;
  }
  return sub || '';
}
