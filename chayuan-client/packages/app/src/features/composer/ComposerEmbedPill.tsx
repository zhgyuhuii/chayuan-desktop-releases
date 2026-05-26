/**
 * ComposerEmbedPill —— KB 嵌入模型只读徽章(原:可选下拉)。
 *
 * Block B §1.1 修复:
 *   旧版本允许用户在对话时切换嵌入模型,会导致 query 编码与 KB 向量空间不一致 →
 *   召回为零(用户感知"搜不到")。修复后该控件改为**只读徽章**,显示当前 KB 用的
 *   嵌入模型名,点击展开 tooltip 解释"为何不可改"。
 *
 * 行为:
 *   - 没选 KB 时不渲染(与 SearchModePill 一致)。
 *   - 选了多个 KB 时:若所有 KB 嵌入模型相同 → 显示该模型;若不一致 → 警告徽章
 *     "嵌入模型不一致,会影响召回质量"。
 *   - 点击 → tooltip:嵌入模型由 KB 创建时定型,如需更换请重建索引。
 */

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Lock, Sparkles } from 'lucide-react';
import {
  cn,
  Pill,
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@chayuan/ui';
import { kb as kbApi } from '@chayuan/api';
import { useComposerStore, useComposerS } from '../../store/composer';

interface KbRow {
  kb_name?: string | null;
  embed_model?: string | null;
}

export const ComposerEmbedPill: React.FC = () => {
  const selectedKuIds = useComposerS((s) => s.selectedKuIds);
  const selectedKbs = useComposerS((s) => s.selectedKbs);

  // 没选 KB → 嵌入模型本就不会进入查询路径,无需展示
  const hasKb = (selectedKuIds?.length ?? 0) + (selectedKbs?.length ?? 0) > 0;

  // 拉一次 KB 列表;命中已选 KB 后看 embed_model
  const { data: kbList } = useQuery<KbRow[]>({
    queryKey: ['kb', 'list'],
    queryFn: async (): Promise<KbRow[]> => {
      const r = (await kbApi.list()) as unknown;
      const arr = Array.isArray(r) ? r : (r as { data?: unknown })?.data;
      return Array.isArray(arr) ? (arr as KbRow[]) : [];
    },
    staleTime: 60_000,
    enabled: hasKb,
  });

  if (!hasKb) return null;

  const selectedNames = new Set([
    ...(selectedKuIds ?? []).map((id) => (id.startsWith('doc:') ? id.slice(4) : id)),
    ...(selectedKbs ?? []),
  ]);
  const matched: KbRow[] = (kbList ?? []).filter(
    (k: KbRow) => !!k.kb_name && selectedNames.has(k.kb_name as string),
  );
  const models = Array.from(
    new Set(matched.map((k: KbRow) => k.embed_model).filter((x): x is string => !!x)),
  );

  const single = models.length === 1 ? models[0] : null;
  const conflict = models.length > 1;

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Pill
            tone="ghost"
            size="sm"
            className={cn(
              conflict && 'border border-amber-300 bg-amber-50 text-amber-700',
            )}
            aria-label="KB 嵌入模型(只读)"
          >
            {conflict ? (
              <AlertTriangle className="h-3.5 w-3.5" />
            ) : (
              <Lock className="h-3 w-3 opacity-60" />
            )}
            {single ? (
              <span className="max-w-[200px] truncate">嵌入: {single}</span>
            ) : conflict ? (
              <span>嵌入: 不一致</span>
            ) : (
              <>
                <Sparkles className="h-3 w-3 opacity-60" />
                <span>嵌入: 跟随 KB</span>
              </>
            )}
          </Pill>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[320px] text-xs leading-relaxed">
          {conflict ? (
            <>
              <strong className="block">所选 KB 的嵌入模型不一致</strong>
              不同维度的向量空间无法联合检索,推荐只选嵌入模型相同的 KB,
              或为这批 KB 统一重建索引。
            </>
          ) : (
            <>
              <strong className="block">嵌入模型由 KB 创建时定型</strong>
              查询时**强制**走 KB 配置的嵌入模型,以保持向量空间一致;
              如需更换嵌入模型,请到 KB 详情页 · 重建索引。
            </>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};
