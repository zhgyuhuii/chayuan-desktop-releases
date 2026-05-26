import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Download, Loader2, RefreshCw, Sparkles, Tag, Upload } from 'lucide-react';
import {
  annotationApi,
  type AnnotationStatus,
  type AnnotationTask,
} from '@chayuan/api';
import { Button, Input, SegmentedTabs, cn } from '@chayuan/ui';
import { reportError } from '../../store/errorDialog';
import { DataMountsPanel } from './data-mounts/DataMountsPanel';

const STATUSES: Array<{ value: AnnotationStatus | ''; label: string }> = [
  { value: '', label: '全部' },
  { value: 'pending', label: '待处理' },
  { value: 'in_progress', label: '处理中' },
  { value: 'submitted', label: '待复审' },
  { value: 'approved', label: '已通过' },
  { value: 'rejected', label: '已驳回' },
  { value: 'conflict', label: '冲突' },
];

const DEFAULT_LABELS = {
  is_correct: true,
  relevance_score: 4,
  safety_score: 5,
  preferred_answer: '',
  error_tags: [],
};

const ANNOTATION_TEMPLATE_SAMPLES: Array<Record<string, unknown>> = [
  {
    id: 'annotation-template-sample-rag-001',
    task_type: 'rag_relevance',
    source: 'annotation_template',
    inputs: {
      query: '样例：根据知识库内容，合同解除需要满足哪些条件？',
      context: '样例原文片段：当事人协商一致，可以解除合同；出现法定解除情形时，也可以依法解除。',
      citations: [{ file_name: '样例-合同制度说明.pdf', chunk_index: 3 }],
    },
    model_output: {
      answer: '样例回答：合同解除通常包括协商解除和法定解除两类，需要结合合同约定与法律规定判断。',
    },
    labels: {
      relevance_score: 4,
      faithfulness_score: 4,
      answer_quality: 'good',
      citation_supported: true,
      error_tags: [],
    },
    meta: {
      is_template_sample: true,
      template_sample_id: 'rag-001',
      description: '这是模板样例，导入时会被自动跳过，请复制结构后替换为真实数据。',
    },
    note: '模板样例，请勿导入',
  },
];

export const AnnotationPage: React.FC = () => {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = React.useState<'tasks' | 'mounts'>('tasks');
  const [status, setStatus] = React.useState<AnnotationStatus | ''>('pending');
  const [taskType, setTaskType] = React.useState('');
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [labelsText, setLabelsText] = React.useState(JSON.stringify(DEFAULT_LABELS, null, 2));
  const [reviewText, setReviewText] = React.useState(JSON.stringify({ decision: 'approved', reason: '' }, null, 2));
  const [note, setNote] = React.useState('');
  const [routeContextId, setRouteContextId] = React.useState('');
  const importInputRef = React.useRef<HTMLInputElement | null>(null);

  const tasksQuery = useQuery({
    queryKey: ['annotation.tasks', status, taskType],
    queryFn: () => annotationApi.listTasks({
      status: status || undefined,
      taskType: taskType.trim() || undefined,
      limit: 80,
    }),
    staleTime: 10_000,
  });

  const usageQuery = useQuery({
    queryKey: ['annotation.usage.summary'],
    queryFn: () => annotationApi.usageSummary(1000),
    staleTime: 30_000,
  });

  const tasks = tasksQuery.data?.items ?? [];
  const selected = React.useMemo(
    () => tasks.find((task) => task.id === selectedId) ?? tasks[0] ?? null,
    [selectedId, tasks],
  );

  React.useEffect(() => {
    if (!selected) return;
    setSelectedId(selected.id);
    setLabelsText(JSON.stringify(selected.labels && Object.keys(selected.labels).length ? selected.labels : DEFAULT_LABELS, null, 2));
    setReviewText(JSON.stringify(selected.review && Object.keys(selected.review).length ? selected.review : { decision: 'approved', reason: '' }, null, 2));
    setNote(selected.note || '');
  }, [selected]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['annotation.tasks'] });
    void qc.invalidateQueries({ queryKey: ['annotation.usage.summary'] });
  };

  const claimMutation = useMutation({
    mutationFn: (taskId: string) => annotationApi.claimTask(taskId),
    onSuccess: (task) => {
      setSelectedId(task.id);
      invalidate();
    },
    onError: (e) => reportError(e, '领取训练样本任务失败'),
  });

  const submitMutation = useMutation({
    mutationFn: async (task: AnnotationTask) => {
      const labels = parseJson(labelsText, '标签 JSON');
      const tags = Array.isArray(labels.error_tags) ? labels.error_tags.map(String) : task.error_tags;
      return annotationApi.patchTask(task.id, {
        status: 'submitted',
        labels,
        error_tags: tags,
        note,
      });
    },
    onSuccess: (task) => {
      setSelectedId(task.id);
      invalidate();
    },
    onError: (e) => reportError(e, '提交训练样本失败'),
  });

  const reviewMutation = useMutation({
    mutationFn: async (task: AnnotationTask) => {
      const labels = parseJson(labelsText, '标签 JSON');
      const review = parseJson(reviewText, '复审 JSON');
      const decision = String(review.decision || 'approved');
      if (decision === 'rejected') {
        return annotationApi.reviewTask(task.id, {
          status: 'rejected',
          labels,
          review,
          note,
        });
      }
      if (decision === 'needs_fix') {
        const correctedOutput = String(review.corrected_output || review.corrected_answer || '').trim();
        if (!correctedOutput) {
          throw new Error('选择“需要修正”后，请先填写修正后的可用内容。');
        }
        return annotationApi.reviewTask(task.id, {
          status: 'approved',
          labels: {
            ...labels,
            is_correct: true,
            answer_correct: true,
            answer_quality: labels.answer_quality || 'good',
            quality: labels.quality || 'good',
            preferred_answer: correctedOutput,
            corrected_output: correctedOutput,
            correction_applied: true,
          },
          review: {
            ...review,
            decision: 'corrected_approved',
            original_decision: 'needs_fix',
            corrected_output: correctedOutput,
            correction_applied: true,
            corrected_at: new Date().toISOString(),
          },
          note,
        });
      }
      return annotationApi.reviewTask(task.id, {
        status: 'approved',
        labels,
        review,
        note,
      });
    },
    onSuccess: (task) => {
      setSelectedId(task.id);
      invalidate();
    },
    onError: (e) => reportError(e, '复审训练样本失败'),
  });

  const sampleRouteContextMutation = useMutation({
    mutationFn: () => annotationApi.sampleFromRouteContext({
      route_context_id: routeContextId.trim(),
      task_type: taskType.trim() || 'rag_relevance',
      priority: 10,
    }),
    onSuccess: (task) => {
      setRouteContextId('');
      setStatus('');
      setTaskType(task.task_type);
      setSelectedId(task.id);
      invalidate();
    },
    onError: (e) => reportError(e, '从上下文创建训练样本失败'),
  });

  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      const text = await file.text();
      const parsed = parseAnnotationImportText(text);
      if (!parsed.items.length) {
        throw new Error(`没有可导入的真实数据；已跳过 ${parsed.skippedSamples} 条模板样例或近似样例`);
      }
      const result = await annotationApi.importDataset({
        items: parsed.items,
        source: 'annotation_page_import',
        default_status: 'pending',
        note: `从文件 ${file.name} 导入${parsed.skippedSamples ? `；前端跳过模板样例 ${parsed.skippedSamples} 条` : ''}`,
      });
      return { ...result, skipped: (result.skipped ?? 0) + parsed.skippedSamples };
    },
    onSuccess: (result) => {
      setStatus('');
      invalidate();
      window.alert(`导入完成：成功 ${result.created} 条，跳过样例 ${result.skipped ?? 0} 条，失败 ${result.failed} 条`);
    },
    onError: (e) => reportError(e, '导入训练数据失败'),
  });

  const mountFilteredMutation = useMutation({
    mutationFn: () => annotationApi.mountDataset({
      name: taskType.trim() ? `${taskType.trim()} 问答挂载` : '训练数据中心问答挂载',
      description: '从训练数据中心筛选条件发布，问答时自动注入偏好、样例和排序信号。',
      task_type: taskType.trim() || undefined,
      scope_type: 'user',
      mount_modes: ['preference', 'fewshot', 'retrieval_boost', 'safety_rule'],
      max_items: 20,
      max_tokens: 1600,
      publish: true,
    }),
    onSuccess: (mount) => {
      void qc.invalidateQueries({ queryKey: ['annotation.usage.summary'] });
      window.alert(`已发布问答挂载：${mount.name}`);
    },
    onError: (e) => reportError(e, '发布问答挂载失败'),
  });

  const mountTaskMutation = useMutation({
    mutationFn: (task: AnnotationTask) => annotationApi.mountDataset({
      name: `${task.task_type} 单样本问答挂载`,
      description: `从训练样本 ${task.id} 发布，问答时作为已审核偏好/样例使用。`,
      sample_ids: [task.id],
      scope_type: 'user',
      mount_modes: ['preference', 'fewshot', 'retrieval_boost', 'safety_rule'],
      max_items: 5,
      max_tokens: 1000,
      publish: true,
    }),
    onSuccess: (mount) => {
      window.alert(`已发布问答挂载：${mount.name}`);
    },
    onError: (e) => reportError(e, '挂载当前训练样本失败'),
  });

  const onExport = () => {
    if (typeof window === 'undefined') return;
    window.open(annotationApi.datasetJsonlUrl({
      taskType: taskType.trim() || undefined,
      status: 'approved',
      limit: 5000,
    }), '_blank');
  };

  const onExportTemplate = () => {
    const template = {
      template_name: 'chayuan_annotation_import_template',
      version: 1,
      instructions: [
        '支持 JSON 或 JSONL 导入。推荐使用本模板的 JSON 格式，并把真实样本放到 items 数组。',
        '每条样本建议包含 task_type、inputs、model_output、labels、meta、note。',
        'inputs 是训练样本输入上下文；model_output 是模型输出；labels 是人工修订或预处理标签。',
        '模板中 meta.is_template_sample=true 的样例只用于说明，导入时系统会自动跳过。',
        '请删除或替换所有“样例”文本后再导入真实数据。',
      ],
      field_schema: {
        task_type: '训练样本类型，如 rag_relevance / qa_quality',
        source: '数据来源，如 kb_chat / manual_dataset',
        target_type: '可选，目标类型，如 kb_doc / conversation',
        target_id: '可选，业务对象 ID',
        inputs: '必填，训练样本输入 JSON 对象',
        model_output: '必填，模型输出 JSON 对象',
        labels: '可选，人工标签或期望标签 JSON 对象',
        error_tags: '可选，错误标签数组',
        meta: '可选，额外元数据 JSON 对象',
        note: '可选，备注',
      },
      items: ANNOTATION_TEMPLATE_SAMPLES,
    };
    downloadTextFile(
      'chayuan-training-data-import-template.json',
      JSON.stringify(template, null, 2),
      'application/json',
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-[var(--cy-surface-base)]">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--cy-border-subtle)] px-6 py-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-[var(--cy-text-primary)]">
            <Tag className="h-5 w-5" />
            训练数据中心
          </h1>
          <p className="mt-1 text-sm text-[var(--cy-text-tertiary)]">
            已通过样本可进入问答挂载、评测集和训练数据导出。
          </p>
          <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-[var(--cy-text-tertiary)]">
            <span className="rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-2 py-0.5">
              可在线使用 {usageQuery.data?.usable_total ?? 0} 条
            </span>
            <span className="rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-2 py-0.5">
              RAG 排序闭环
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="hidden items-center gap-2 lg:flex">
            <Input
              value={routeContextId}
              onChange={(e) => setRouteContextId(e.target.value)}
              placeholder="route_context id"
              className="h-8 w-56 text-xs"
            />
            <Button
              size="sm"
              variant="outline"
              onClick={() => sampleRouteContextMutation.mutate()}
              disabled={!routeContextId.trim() || sampleRouteContextMutation.isPending}
            >
              {sampleRouteContextMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Tag className="h-3.5 w-3.5" />}
              从上下文生成
            </Button>
          </div>
          <Button size="sm" variant="outline" onClick={() => void tasksQuery.refetch()}>
            {tasksQuery.isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            刷新
          </Button>
          <input
            ref={importInputRef}
            type="file"
            accept=".json,.jsonl,.ndjson,application/json,application/x-ndjson"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.currentTarget.value = '';
              if (file) importMutation.mutate(file);
            }}
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => importInputRef.current?.click()}
            disabled={importMutation.isPending}
          >
            {importMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            导入
          </Button>
          <Button size="sm" variant="outline" onClick={onExportTemplate}>
            <Download className="h-3.5 w-3.5" />
            导出模板
          </Button>
          <Button size="sm" variant="outline" onClick={onExport}>
            <Download className="h-3.5 w-3.5" />
            导出 JSONL
          </Button>
          <Button
            size="sm"
            onClick={() => mountFilteredMutation.mutate()}
            disabled={mountFilteredMutation.isPending}
            title="仅已通过样本会进入线上问答挂载"
          >
            {mountFilteredMutation.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
            挂载筛选
          </Button>
        </div>
      </header>

      <div className="shrink-0 border-b border-[var(--cy-border-subtle)] px-6 py-2">
        <SegmentedTabs
          items={[
            { value: 'tasks', label: '标注任务' },
            { value: 'mounts', label: '数据挂载' },
          ]}
          value={activeTab}
          onChange={(v) => setActiveTab(v as 'tasks' | 'mounts')}
          size="sm"
        />
      </div>

      {activeTab === 'mounts' && <DataMountsPanel />}

      {activeTab === 'tasks' && <div className="flex min-h-0 flex-1">
        <aside className="flex w-[360px] shrink-0 flex-col border-r border-[var(--cy-border-subtle)]">
          <div className="space-y-2 border-b border-[var(--cy-border-subtle)] p-3">
            <div className="flex gap-2">
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="h-8 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-xs"
              >
                {STATUSES.map((item) => (
                  <option key={item.value || 'all'} value={item.value}>{item.label}</option>
                ))}
              </select>
              <Input
                value={taskType}
                onChange={(e) => setTaskType(e.target.value)}
                placeholder="task_type，如 rag_relevance"
                className="h-8 text-xs"
              />
            </div>
            <p className="text-[11px] text-[var(--cy-text-tertiary)]">
              共 {tasksQuery.data?.total ?? 0} 条，当前显示 {tasks.length} 条
            </p>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {tasksQuery.isLoading && (
              <p className="px-2 py-3 text-xs text-[var(--cy-text-tertiary)]">加载中...</p>
            )}
            {!tasksQuery.isLoading && tasks.length === 0 && (
              <p className="px-2 py-3 text-xs text-[var(--cy-text-tertiary)]">
                暂无任务。可以通过 API 从业务运行结果创建训练样本任务。
              </p>
            )}
            {tasks.map((task) => (
              <button
                key={task.id}
                type="button"
                onClick={() => setSelectedId(task.id)}
                className={cn(
                  'mb-2 w-full rounded-xl border p-3 text-left text-xs transition-colors',
                  selected?.id === task.id
                    ? 'border-[var(--cy-brand-300)] bg-[var(--cy-brand-50)]'
                    : 'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] hover:bg-[var(--cy-surface-2)]',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-[var(--cy-text-primary)]">{task.task_type}</span>
                  <StatusPill status={task.status} />
                </div>
                <p className="mt-1 line-clamp-2 text-[var(--cy-text-secondary)]">
                  {summaryOf(task)}
                </p>
                <p className="mt-2 truncate text-[10px] text-[var(--cy-text-tertiary)]">
                  {task.source} · {task.target_type || 'sample'}:{task.target_id || task.id.slice(0, 8)}
                </p>
              </button>
            ))}
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto p-5">
          {selected ? (
            <TaskDetail
              task={selected}
              labelsText={labelsText}
              reviewText={reviewText}
              note={note}
              busy={claimMutation.isPending || submitMutation.isPending || reviewMutation.isPending}
              onLabelsChange={setLabelsText}
              onReviewChange={setReviewText}
              onNoteChange={setNote}
              onClaim={() => claimMutation.mutate(selected.id)}
              onSubmit={() => submitMutation.mutate(selected)}
              onReview={() => reviewMutation.mutate(selected)}
              onMount={() => mountTaskMutation.mutate(selected)}
              mountBusy={mountTaskMutation.isPending}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-[var(--cy-text-tertiary)]">
              选择一条训练样本任务
            </div>
          )}
        </main>
      </div>}
    </div>
  );
};

function TaskDetail(props: {
  task: AnnotationTask;
  labelsText: string;
  reviewText: string;
  note: string;
  busy: boolean;
  onLabelsChange(v: string): void;
  onReviewChange(v: string): void;
  onNoteChange(v: string): void;
  onClaim(): void;
  onSubmit(): void;
  onReview(): void;
  onMount(): void;
  mountBusy: boolean;
}) {
  const { task } = props;
  const review = parseLooseJson(props.reviewText, { decision: 'approved', reason: '' });
  const reviewDecision = String(review.decision || 'approved');
  const reviewButtonText = reviewDecision === 'needs_fix'
    ? '提交修正并通过'
    : reviewDecision === 'rejected'
      ? '复审驳回'
      : '复审通过';
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <section className="rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-[var(--cy-text-primary)]">{task.task_type}</h2>
            <p className="mt-1 text-xs text-[var(--cy-text-tertiary)]">
              {task.id} · {task.source} · priority {task.priority}
            </p>
          </div>
          <StatusPill status={task.status} />
        </div>
        <AnnotationSampleOverview task={task} />
      </section>

      <section className="grid gap-4 md:grid-cols-2">
        <AnnotationLabelForm
          task={task}
          valueText={props.labelsText}
          onChangeText={props.onLabelsChange}
        />
        <AnnotationReviewForm
          valueText={props.reviewText}
          onChangeText={props.onReviewChange}
        />
      </section>

      <section className="rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-4">
        <label className="text-xs font-medium text-[var(--cy-text-secondary)]">备注</label>
        <textarea
          value={props.note}
          onChange={(e) => props.onNoteChange(e.target.value)}
          className="mt-2 h-20 w-full resize-none rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-3 text-sm text-[var(--cy-text-primary)]"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={props.onClaim} disabled={props.busy}>
            领取
          </Button>
          <Button size="sm" onClick={props.onSubmit} disabled={props.busy}>
            {props.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
            提交样本
          </Button>
          <Button size="sm" variant="outline" onClick={props.onReview} disabled={props.busy}>
            {reviewButtonText}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={props.onMount}
            disabled={props.mountBusy || task.status !== 'approved'}
            title={task.status === 'approved' ? '把当前已通过样本发布为问答挂载' : '只有已通过样本可以挂载到问答'}
          >
            {props.mountBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
            挂载本样本
          </Button>
        </div>
      </section>
    </div>
  );
}

const AnnotationLabelForm: React.FC<{
  task: AnnotationTask;
  valueText: string;
  onChangeText(v: string): void;
}> = ({ task, valueText, onChangeText }) => {
  const [advanced, setAdvanced] = React.useState(false);
  const labels = parseLooseJson(valueText, DEFAULT_LABELS);
  const patch = (next: Record<string, unknown>) => {
    onChangeText(JSON.stringify({ ...labels, ...next }, null, 2));
  };
  const errorTags = Array.isArray(labels.error_tags) ? labels.error_tags.map(String).join(', ') : '';
  const isRag = /rag|qa|search|relevance/i.test(task.task_type);
  return (
    <EditorCard title="人工修订表单">
      <p className="mb-3 text-xs leading-relaxed text-[var(--cy-text-tertiary)]">
        按表单修订即可，系统会自动生成后端需要的 labels JSON。通过复审后，这些样本可进入问答挂载、评测集和训练数据。
      </p>
      <div className="space-y-3">
        <FormRow label="整体是否正确">
          <select
            value={String(labels.is_correct ?? labels.answer_correct ?? true)}
            onChange={(e) => patch({ is_correct: e.target.value === 'true', answer_correct: e.target.value === 'true' })}
            className="h-9 rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-sm"
          >
            <option value="true">正确 / 可接受</option>
            <option value="false">错误 / 不可接受</option>
          </select>
        </FormRow>

        {isRag && (
          <>
            <FormRow label="检索相关性">
              <ScoreInput value={labels.relevance_score} onChange={(v) => patch({ relevance_score: v, retrieval_relevant: v >= 3 })} />
            </FormRow>
            <FormRow label="引用是否支撑答案">
              <select
                value={String(labels.citation_supported ?? true)}
                onChange={(e) => patch({ citation_supported: e.target.value === 'true' })}
                className="h-9 rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-sm"
              >
                <option value="true">支撑</option>
                <option value="false">不支撑 / 缺证据</option>
              </select>
            </FormRow>
            <FormRow label="事实一致性">
              <ScoreInput value={labels.faithfulness_score ?? labels.safety_score} onChange={(v) => patch({ faithfulness_score: v })} />
            </FormRow>
          </>
        )}

        <FormRow label="答案质量">
          <select
            value={String(labels.answer_quality ?? labels.quality ?? 'good')}
            onChange={(e) => patch({ answer_quality: e.target.value, quality: e.target.value })}
            className="h-9 rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-sm"
          >
            <option value="excellent">优秀</option>
            <option value="good">良好</option>
            <option value="partial">部分可用</option>
            <option value="bad">不可用</option>
          </select>
        </FormRow>

        <FormRow label="错误标签">
          <Input
            value={errorTags}
            onChange={(e) => patch({ error_tags: e.target.value.split(/[,，]/).map((x) => x.trim()).filter(Boolean) })}
            placeholder="如：引用错误, 遗漏事实, 格式问题"
            className="h-9 text-sm"
          />
        </FormRow>

        <label className="block">
          <span className="text-xs font-medium text-[var(--cy-text-secondary)]">期望答案 / 修改建议</span>
          <textarea
            value={String(labels.preferred_answer ?? labels.comment ?? '')}
            onChange={(e) => patch({ preferred_answer: e.target.value, comment: e.target.value })}
            className="mt-1 h-20 w-full resize-none rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-3 text-sm text-[var(--cy-text-primary)]"
            placeholder="写下你希望模型给出的答案或修改方向"
          />
        </label>

        <button
          type="button"
          onClick={() => setAdvanced((v) => !v)}
          className="text-xs text-[var(--cy-brand-700)] hover:underline"
        >
          {advanced ? '收起 JSON' : '高级：查看/编辑 JSON'}
        </button>
        {advanced && (
          <textarea
            value={valueText}
            onChange={(e) => onChangeText(e.target.value)}
            className="h-48 w-full resize-none rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-3 font-mono text-xs text-[var(--cy-text-primary)]"
          />
        )}
      </div>
    </EditorCard>
  );
};

const AnnotationReviewForm: React.FC<{
  valueText: string;
  onChangeText(v: string): void;
}> = ({ valueText, onChangeText }) => {
  const [advanced, setAdvanced] = React.useState(false);
  const review = parseLooseJson(valueText, { decision: 'approved', reason: '' });
  const decision = String(review.decision ?? 'approved');
  const patch = (next: Record<string, unknown>) => {
    onChangeText(JSON.stringify({ ...review, ...next }, null, 2));
  };
  return (
    <EditorCard title="复审表单">
      <p className="mb-3 text-xs leading-relaxed text-[var(--cy-text-tertiary)]">
        复审通过后，样本才会进入系统闭环；未通过的样本只保留为问题记录，不影响线上结果。
      </p>
      <div className="space-y-3">
        <FormRow label="复审结论">
          <select
            value={decision}
            onChange={(e) => patch({ decision: e.target.value })}
            className="h-9 rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-sm"
          >
            <option value="approved">通过，可用于闭环</option>
            <option value="rejected">驳回，不进入闭环</option>
            <option value="needs_fix">需要修正</option>
          </select>
        </FormRow>
        {decision === 'needs_fix' && (
          <div className="rounded-xl border border-amber-200 bg-amber-50/70 p-3">
            <p className="text-xs leading-relaxed text-amber-800">
              选择“需要修正”后，请在这里填写修正后的可用内容。提交时系统会把该内容写入 labels.corrected_output / preferred_answer，并以“已通过”状态进入闭环。
            </p>
            <label className="mt-3 block">
              <span className="text-xs font-medium text-amber-900">修正后的可用内容</span>
              <textarea
                value={String(review.corrected_output ?? review.corrected_answer ?? '')}
                onChange={(e) => patch({
                  corrected_output: e.target.value,
                  corrected_answer: e.target.value,
                })}
                className="mt-1 h-32 w-full resize-none rounded-xl border border-amber-200 bg-white p-3 text-sm text-[var(--cy-text-primary)]"
                placeholder="填写修正后的答案或可直接用于训练/评测的标准输出"
              />
            </label>
          </div>
        )}
        <FormRow label="使用范围">
          <select
            value={String(review.usage ?? 'online_and_eval')}
            onChange={(e) => patch({ usage: e.target.value })}
            className="h-9 rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 text-sm"
          >
            <option value="online_and_eval">线上闭环 + 评测集</option>
            <option value="eval_only">仅评测集</option>
            <option value="archive_only">仅归档</option>
          </select>
        </FormRow>
        <label className="block">
          <span className="text-xs font-medium text-[var(--cy-text-secondary)]">复审理由</span>
          <textarea
            value={String(review.reason ?? '')}
            onChange={(e) => patch({ reason: e.target.value })}
            className="mt-1 h-28 w-full resize-none rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-3 text-sm text-[var(--cy-text-primary)]"
            placeholder="说明为什么通过/驳回，便于团队理解和后续追踪"
          />
        </label>
        <button
          type="button"
          onClick={() => setAdvanced((v) => !v)}
          className="text-xs text-[var(--cy-brand-700)] hover:underline"
        >
          {advanced ? '收起 JSON' : '高级：查看/编辑 JSON'}
        </button>
        {advanced && (
          <textarea
            value={valueText}
            onChange={(e) => onChangeText(e.target.value)}
            className="h-48 w-full resize-none rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] p-3 font-mono text-xs text-[var(--cy-text-primary)]"
          />
        )}
      </div>
    </EditorCard>
  );
};

const FormRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <label className="grid gap-1.5 text-xs font-medium text-[var(--cy-text-secondary)]">
    <span>{label}</span>
    {children}
  </label>
);

const ScoreInput: React.FC<{ value: unknown; onChange(v: number): void }> = ({ value, onChange }) => (
  <div className="flex items-center gap-2">
    <input
      type="range"
      min={1}
      max={5}
      step={1}
      value={Number(value ?? 4)}
      onChange={(e) => onChange(Number(e.target.value))}
      className="min-w-0 flex-1 accent-[var(--cy-brand-500)]"
    />
    <span className="w-8 rounded-md bg-[var(--cy-surface-base)] px-2 py-1 text-center text-xs text-[var(--cy-text-primary)]">
      {Number(value ?? 4)}
    </span>
  </div>
);

const EditorCard: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <section className="rounded-2xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] p-4">
    <h3 className="mb-2 text-sm font-medium text-[var(--cy-text-primary)]">{title}</h3>
    {children}
  </section>
);

function parseAnnotationImportText(text: string): { items: Array<Record<string, unknown>>; skippedSamples: number } {
  const raw = text.trim();
  if (!raw) throw new Error('导入文件为空');
  let items: Array<Record<string, unknown>>;
  if (raw.startsWith('[') || raw.startsWith('{')) {
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed)) items = parsed.map(assertObjectItem);
    else if (parsed && typeof parsed === 'object') {
      const obj = parsed as Record<string, unknown>;
      if (Array.isArray(obj.items)) items = obj.items.map(assertObjectItem);
      else items = [obj];
    } else {
      items = [];
    }
  } else {
    items = raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => assertObjectItem(JSON.parse(line)));
  }
  const realItems = items.filter((item) => !isTemplateSampleLike(item));
  return { items: realItems, skippedSamples: items.length - realItems.length };
}

function isTemplateSampleLike(item: Record<string, unknown>): boolean {
  const meta = item.meta && typeof item.meta === 'object' ? item.meta as Record<string, unknown> : {};
  const id = String(item.id ?? '').toLowerCase();
  const source = String(item.source ?? '').toLowerCase();
  if (meta.is_template_sample === true || id.startsWith('annotation-template-sample') || source === 'annotation_template') {
    return true;
  }
  const text = normalizeSampleText(item);
  return ANNOTATION_TEMPLATE_SAMPLES.some((sample) => textSimilarity(text, normalizeSampleText(sample)) >= 0.82);
}

function normalizeSampleText(value: unknown): string {
  const raw = JSON.stringify(value ?? {}, (_key, v) => {
    if (_key === 'id' || _key === 'create_time' || _key === 'update_time') return undefined;
    return v;
  });
  return raw
    .toLowerCase()
    .replace(/annotation-template-sample-[\w-]+/g, '')
    .replace(/[^\p{L}\p{N}\u4e00-\u9fff]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function textSimilarity(a: string, b: string): number {
  const aa = tokenSet(a);
  const bb = tokenSet(b);
  if (!aa.size || !bb.size) return 0;
  let hit = 0;
  for (const t of aa) {
    if (bb.has(t)) hit += 1;
  }
  return hit / Math.max(aa.size, bb.size);
}

function tokenSet(text: string): Set<string> {
  const out = new Set<string>();
  for (const token of text.split(/\s+/)) {
    if (token.length >= 2) out.add(token);
    for (let i = 0; i + 2 <= token.length; i += 1) {
      const gram = token.slice(i, i + 2);
      if (/[\u4e00-\u9fff]/.test(gram)) out.add(gram);
    }
  }
  return out;
}

function downloadTextFile(filename: string, text: string, mime: string): void {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

function assertObjectItem(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('导入数据必须是对象、对象数组或 JSONL 对象行');
  }
  return value as Record<string, unknown>;
}

const AnnotationSampleOverview: React.FC<{ task: AnnotationTask }> = ({ task }) => {
  const input = task.inputs ?? {};
  const output = task.model_output ?? {};
  const pre = task.llm_prel_labels ?? {};
  const meta = task.meta ?? {};

  const question = firstText(input, ['query', 'question', 'prompt', 'input', 'instruction']);
  const context = firstText(input, ['context', 'text', 'original_text', 'source_text', 'document_text', 'summary']);
  const answer = firstText(output, ['answer', 'result', 'content', 'draft', 'output', 'value', 'text']);
  const retrievedItems = arrayField(output, 'retrieved_items');
  const route = firstText(input, ['route']);
  const kuIds = arrayField(input, 'ku_ids');
  const labels = pre.label_schema && typeof pre.label_schema === 'object'
    ? Object.entries(pre.label_schema as Record<string, unknown>)
    : [];

  return (
    <div className="mt-4 space-y-3">
      <div className="grid gap-3 lg:grid-cols-2">
        <ReadableCard title="输入了什么" tone="blue">
          <ReadableField label={questionLabel(task.task_type)} value={question || '未提供明确问题/指令'} primary />
          {context ? <ReadableField label="上下文 / 原文" value={context} multiline /> : null}
          {route ? <ReadableField label="业务路径" value={route} /> : null}
          {kuIds.length ? <ReadableChips label="知识库范围" values={kuIds.map(String)} /> : null}
          <ReadableKeyValues
            title="其它输入字段"
            value={input}
            omit={['query', 'question', 'prompt', 'input', 'instruction', 'context', 'text', 'original_text', 'source_text', 'document_text', 'summary', 'route', 'ku_ids']}
          />
        </ReadableCard>

        <ReadableCard title="模型输出了什么" tone="green">
          <ReadableField label="模型回答 / 生成内容" value={answer || '模型输出为空'} primary multiline />
          {retrievedItems.length ? (
            <div>
              <p className="mb-1 text-[11px] font-medium text-[var(--cy-text-secondary)]">检索命中</p>
              <div className="space-y-1">
                {retrievedItems.slice(0, 5).map((item, idx) => (
                  <RetrievedItemSummary key={idx} item={item} index={idx + 1} />
                ))}
              </div>
            </div>
          ) : null}
          <ReadableKeyValues
            title="其它输出字段"
            value={output}
            omit={['answer', 'result', 'content', 'draft', 'output', 'value', 'text', 'retrieved_items']}
          />
        </ReadableCard>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <ReadableCard title="LLM 预处理建议" tone="amber">
          <ReadableField label="建议任务类型" value={textValue(pre.suggested_task) || task.task_type} />
          <ReadableField label="是否需要人工复审" value={pre.needs_human_review === false ? '否' : '是'} />
          {labels.length ? (
            <div>
              <p className="mb-1 text-[11px] font-medium text-[var(--cy-text-secondary)]">建议标注项</p>
              <div className="grid gap-1 sm:grid-cols-2">
                {labels.map(([key, value]) => (
                  <div key={key} className="rounded-lg bg-[var(--cy-surface-base)] px-2 py-1 text-xs">
                    <span className="font-medium text-[var(--cy-text-secondary)]">{friendlyKey(key)}：</span>
                    <span className="text-[var(--cy-text-primary)]">{formatReadableValue(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <ReadableKeyValues
            title="其它建议"
            value={pre}
            omit={['suggested_task', 'needs_human_review', 'label_schema']}
          />
        </ReadableCard>

        <ReadableCard title="样本来源和元数据" tone="slate">
          <div className="grid gap-2 sm:grid-cols-2">
            <InfoPill label="来源" value={task.source} />
            <InfoPill label="目标" value={`${task.target_type || 'sample'}:${task.target_id || task.id.slice(0, 8)}`} />
            <InfoPill label="创建人" value={task.created_by == null ? '未知' : String(task.created_by)} />
            <InfoPill label="更新时间" value={task.update_time || '未知'} />
          </div>
          <ReadableKeyValues title="业务元数据" value={meta} />
        </ReadableCard>
      </div>
    </div>
  );
};

const ReadableCard: React.FC<{
  title: string;
  tone: 'blue' | 'green' | 'amber' | 'slate';
  children: React.ReactNode;
}> = ({ title, tone, children }) => (
  <section className={cn(
    'min-w-0 rounded-xl border p-3',
    tone === 'blue' && 'border-blue-200 bg-blue-50/60',
    tone === 'green' && 'border-emerald-200 bg-emerald-50/60',
    tone === 'amber' && 'border-amber-200 bg-amber-50/60',
    tone === 'slate' && 'border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)]',
  )}>
    <h3 className="mb-3 text-sm font-semibold text-[var(--cy-text-primary)]">{title}</h3>
    <div className="space-y-3">{children}</div>
  </section>
);

const ReadableField: React.FC<{
  label: string;
  value: unknown;
  primary?: boolean;
  multiline?: boolean;
}> = ({ label, value, primary, multiline }) => (
  <div>
    <p className="mb-1 text-[11px] font-medium text-[var(--cy-text-secondary)]">{label}</p>
    <div className={cn(
      'rounded-lg bg-[var(--cy-surface-base)] px-3 py-2 text-sm text-[var(--cy-text-primary)]',
      primary && 'font-medium',
      multiline && 'whitespace-pre-wrap leading-relaxed',
    )}>
      {formatReadableValue(value)}
    </div>
  </div>
);

const ReadableChips: React.FC<{ label: string; values: string[] }> = ({ label, values }) => (
  <div>
    <p className="mb-1 text-[11px] font-medium text-[var(--cy-text-secondary)]">{label}</p>
    <div className="flex flex-wrap gap-1">
      {values.map((value) => (
        <span key={value} className="rounded-full border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-2 py-0.5 text-xs text-[var(--cy-text-secondary)]">
          {value}
        </span>
      ))}
    </div>
  </div>
);

const ReadableKeyValues: React.FC<{
  title: string;
  value: Record<string, unknown>;
  omit?: string[];
}> = ({ title, value, omit = [] }) => {
  const entries = Object.entries(value || {})
    .filter(([key, val]) => !omit.includes(key) && val != null && val !== '' && !(Array.isArray(val) && val.length === 0));
  if (!entries.length) return null;
  return (
    <details className="rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-base)] px-3 py-2">
      <summary className="cursor-pointer text-xs font-medium text-[var(--cy-text-secondary)]">{title}</summary>
      <div className="mt-2 grid gap-2">
        {entries.map(([key, value]) => (
          <div key={key} className="grid gap-1 rounded-md bg-[var(--cy-surface-1)] px-2 py-1.5 text-xs">
            <span className="font-medium text-[var(--cy-text-secondary)]">{friendlyKey(key)}</span>
            <span className="whitespace-pre-wrap break-words text-[var(--cy-text-primary)]">{formatReadableValue(value)}</span>
          </div>
        ))}
      </div>
    </details>
  );
};

const InfoPill: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="rounded-lg border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2 text-xs">
    <p className="text-[var(--cy-text-tertiary)]">{label}</p>
    <p className="mt-0.5 truncate font-medium text-[var(--cy-text-primary)]">{value}</p>
  </div>
);

const RetrievedItemSummary: React.FC<{ item: unknown; index: number }> = ({ item, index }) => {
  const obj = item && typeof item === 'object' && !Array.isArray(item) ? item as Record<string, unknown> : {};
  const file = textValue(obj.file_name) || textValue(obj.source) || textValue((obj.metadata as Record<string, unknown> | undefined)?.source) || `命中 ${index}`;
  const snippet = firstText(obj, ['content', 'page_content', 'text', 'snippet'], 180);
  const score = obj.score == null ? '' : ` · score ${Number(obj.score).toFixed(3)}`;
  return (
    <div className="rounded-lg bg-[var(--cy-surface-base)] px-3 py-2 text-xs">
      <p className="font-medium text-[var(--cy-text-primary)]">{file}{score}</p>
      {snippet ? <p className="mt-1 line-clamp-2 text-[var(--cy-text-secondary)]">{snippet}</p> : null}
    </div>
  );
};

const StatusPill: React.FC<{ status: string }> = ({ status }) => (
  <span className="shrink-0 rounded-full bg-[var(--cy-surface-base)] px-2 py-0.5 text-[10px] text-[var(--cy-text-tertiary)]">
    {status}
  </span>
);

function summaryOf(task: AnnotationTask): string {
  const q = task.inputs.query || task.inputs.instruction || task.inputs.text || task.inputs.prompt;
  if (typeof q === 'string' && q.trim()) return q.trim();
  const out = task.model_output.answer || task.model_output.content;
  if (typeof out === 'string' && out.trim()) return out.trim();
  return task.note || '无摘要';
}

function firstText(obj: Record<string, unknown>, keys: string[], limit = 1200): string {
  for (const key of keys) {
    const text = textValue(obj[key], limit);
    if (text) return text;
  }
  return '';
}

function textValue(value: unknown, limit = 1200): string {
  if (value == null) return '';
  if (typeof value === 'string') return value.trim().slice(0, limit);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    return value.map((item) => formatReadableValue(item)).filter(Boolean).join('、').slice(0, limit);
  }
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    const preferred = firstText(obj, ['title', 'name', 'label', 'summary', 'content', 'text', 'value'], limit);
    if (preferred) return preferred;
    return Object.entries(obj)
      .slice(0, 6)
      .map(([key, val]) => `${friendlyKey(key)}：${formatReadableValue(val)}`)
      .join('；')
      .slice(0, limit);
  }
  return String(value).slice(0, limit);
}

function arrayField(obj: Record<string, unknown>, key: string): unknown[] {
  const value = obj[key];
  return Array.isArray(value) ? value : [];
}

function formatReadableValue(value: unknown): string {
  if (value == null || value === '') return '无';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '无';
  if (typeof value === 'string') return value.trim() || '无';
  if (Array.isArray(value)) {
    if (!value.length) return '无';
    return value.map((item) => formatReadableValue(item)).join('、');
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, val]) => val != null && val !== '')
      .map(([key, val]) => `${friendlyKey(key)}：${formatReadableValue(val)}`)
      .join('；') || '无';
  }
  return String(value);
}

function friendlyKey(key: string): string {
  const map: Record<string, string> = {
    query: '问题',
    question: '问题',
    prompt: '提示词',
    instruction: '指令',
    context: '上下文',
    text: '文本',
    original_text: '原文',
    answer: '回答',
    result: '结果',
    content: '内容',
    route: '路径',
    target_type: '目标类型',
    target_id: '目标 ID',
    ku_ids: '知识库',
    top_k: '召回数量',
    suggested_task: '建议任务',
    needs_human_review: '需要人工复审',
    retrieved_count: '检索数量',
    block_count: '结果块数量',
    created_from: '创建来源',
    action_type: '动作类型',
    type: '类型',
    scope: '范围',
    source: '来源',
    file_name: '文件名',
    score: '分数',
  };
  return map[key] || key.replace(/_/g, ' ');
}

function questionLabel(taskType: string): string {
  if (/action/i.test(taskType)) return '用户指令';
  if (/rag|qa|search|relevance/i.test(taskType)) return '用户问题';
  return '输入内容';
}

function parseJson(text: string, name: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(text || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(`${name} 必须是 JSON object`);
    }
    return parsed as Record<string, unknown>;
  } catch (e) {
    throw new Error(`${name} 解析失败: ${e instanceof Error ? e.message : String(e)}`);
  }
}

function parseLooseJson(text: string, fallback: Record<string, unknown>): Record<string, unknown> {
  try {
    const parsed = JSON.parse(text || '{}');
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // 高级 JSON 正在编辑到半截时,表单用 fallback 保持可渲染。
  }
  return fallback;
}
