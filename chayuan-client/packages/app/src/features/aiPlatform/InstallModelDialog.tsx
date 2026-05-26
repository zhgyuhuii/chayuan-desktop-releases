/**
 * "本地模型服务"行内"下载模型"入口的 Dialog。
 *
 * 用户故事
 * ========
 *
 * 用户在设置页看到「聊天」cap 的本地模型服务 state=stopped(因为没装模型),
 * 点该行右侧的「下载模型」按钮 → 这个 Dialog 弹出。
 *
 * Dialog 提供两种途径:
 *
 *   1. **去 ModelScope 搜模型 + 粘贴 model_id 下载**(主路径)
 *      - 标题:在 ModelScope 搜 <cap-推荐关键字>
 *      - 「打开 modelscope.cn」按钮(用 shell.openExternal,WebView 不允许 window.open)
 *      - 输入框:粘贴 model_id(``owner/name``)
 *      - 可选输入:specific_file(GGUF 多 quant 仓库挑一个,如 ``Q4_K_M.gguf``)
 *      - 「开始下载」→ POST /admin/models/install_model 返回 job_id → 每 2s 轮询
 *      - 进度条 + log_tail 显示
 *      - 成功 → 提示"已下载并自动启动" + onSuccess() 让父组件刷新模型下拉
 *
 *   2. **手动放好模型,扫描挂载**(备用路径)
 *      - 展示 ``<CHAYUAN_ROOT>/models/bundled/<cap>/`` 真实绝对路径
 *      - 复制按钮 + 提示"把模型文件复制到这里,然后点下面"
 *      - 「我已放好,扫描挂载」→ POST /admin/models/scan_and_mount
 *      - 后端 scan_once + 自动 start 新发现的 cap
 *      - 完成 → 列出 ``scan_delta`` + ``auto_started`` + onSuccess()
 *
 * 跟 BootstrapBanner 的区别
 * ========================
 *
 * BootstrapBanner 已删除 — 它强推 release 整包,用户没自主权;这个 Dialog
 * 是 cap-粒度的自主入口,用户挑模型、挑源、决定何时下,UI 更直接也更克制。
 */
import * as React from 'react';
import { Copy, ExternalLink, FolderOpen, Loader2, RotateCw } from 'lucide-react';
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
} from '@chayuan/ui';
import {
  type InstallModelCapability,
  type InstallModelJob,
  type InstallModelSource,
  type LocalRuntimeCapability,
  type ScanAndMountResult,
  serverModelsInstallModel,
  serverModelsScanAndMount,
} from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';
import { notifyInfo, notifySuccess, reportError } from '../../store/errorDialog';

export interface InstallModelDialogProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  /** 当前 cap(LocalRuntimeServicesSection 用的短名:chat/embedding/...);
   *  会被映射成 catalog 长名传给后端。 */
  capability: LocalRuntimeCapability;
  /** 下载成功 / 扫描挂载成功后回调,让父组件重拉模型下拉 + 状态徽章。 */
  onSuccess?(): void;
}

// LocalRuntimeServicesSection 短名 → backend catalog 长名
const CAP_TO_CATALOG: Record<LocalRuntimeCapability, InstallModelCapability> = {
  chat: 'chat',
  embedding: 'text-embedding',
  rerank: 'rerank',
  asr: 'speech-to-text',
  'image-embedding': 'image-embedding',
};

const CAP_LABEL: Record<LocalRuntimeCapability, string> = {
  chat: '聊天模型',
  embedding: '文本向量模型',
  rerank: '重排模型',
  asr: '语音识别模型',
  'image-embedding': '图像向量模型',
};

// 跟后端 chayuan-server/.../model_registry/install_job.py 的 _RELEASE_MANIFEST['lite']
// 对齐 — 这些 repo + file 组合都已在 hf-mirror.com 实测 HEAD 200(line 80 注释)。
// Dialog 一打开就把对应 cap 的 modelId + source + specificFile 预填好,用户点
// "开始下载"即装,不需要看 examples 再手填。
//
// rerank:rerank sidecar 跑 llama-server,**只吃 GGUF**。HF safetensors 整仓
//   (BAAI/bge-reranker-v2-m3 那种)扫得到但 sidecar 起不来,报 "format=
//   'hf_transformers' 不是 gguf"。所以预填的必须是 *-GGUF 量化仓库,并指定
//   单个 quant 文件(单文件下载,免得把 9 个 quant 全拉下来)。
//   gpustack/* 系列在 ModelScope 国内源,避开 HF Xet LFS 大文件卡 0% 的老坑。
// embedding:跟 rerank 同理 —— embedding sidecar 也跑 llama-server,**只吃 GGUF**。
//   HF safetensors 整仓(BAAI/bge-m3、iic/nlp_gte_* 那种)扫得到但 sidecar 起不来,
//   报 "format='hf_transformers' 不是 gguf"。所以预填 *-GGUF 仓库 + 单 quant 文件。
// image-embedding 仍故意不列:HF transformers 整仓,走 infinity Python sidecar;
//   project 还没把 image-embedding 进 verified 清单,先不预填。
type VerifiedDefault = { id: string; source: InstallModelSource; file?: string };
// 弹窗打开即预填的「系统默认」模型。2026-05 逐条真下载实测过
// (ModelScope / hf-mirror range-GET 校验文件头)。能用 ModelScope 的全走
// ModelScope(国内直连);whisper.cpp ModelScope 无仓,走 HF→hf-mirror。
const VERIFIED_DEFAULTS: Partial<Record<LocalRuntimeCapability, VerifiedDefault>> = {
  chat: {
    id: 'unsloth/Qwen3-4B-Instruct-2507-GGUF',
    source: 'modelscope',
    file: 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
  },
  embedding: {
    id: 'gpustack/bge-m3-GGUF',
    source: 'modelscope',
    file: 'bge-m3-Q8_0.gguf',
  },
  rerank: {
    id: 'gpustack/bge-reranker-v2-m3-GGUF',
    source: 'modelscope',
    file: 'bge-reranker-v2-m3-Q8_0.gguf',
  },
  asr: {
    id: 'ggerganov/whisper.cpp',
    source: 'huggingface',
    file: 'ggml-tiny.bin',
  },
  'image-embedding': {
    id: 'openai/clip-vit-base-patch32',
    source: 'huggingface',
    // CLIP 多文件 transformers 模型 → 整仓下载,不指定单文件。
    // HF 走 hf-mirror 镜像;ModelScope 上对应的是 openai-mirror/ ——
    // 主源 404 时 install_model 会自动换源回退,两边都能下成。
  },
};

// 按 cap 给出 ModelScope 上比较合适的搜索关键字 + 1-2 个示例 model_id。
// 例:chat → "Qwen GGUF" 比单 "chat" 命中更准(都是 LLM 标"对话")。
// 这里的 example 是为了让用户对"该填什么"有第一印象,不是强推。
//
// example 上必须带 source — 用户点示例直接 set source,避免出现"推荐 ggerganov/
// whisper.cpp(HF only)但默认 source=modelscope → repo_exists 404"那种坑。
// 取舍:Qwen/* 双源都有,标 modelscope(国内快);BAAI/* 双源都有,标 huggingface
// (走 hf-mirror)。iic/* 是阿里 ModelScope 专属 owner,必须 modelscope。
// label 有值时按钮显示 label(友好名),没有则显示 id;note 进 title tooltip。
type CapExample = {
  id: string;
  source: InstallModelSource;
  label?: string;
  note?: string;
  /** GGUF 多 quant 仓库:指定单个 quant 文件,点示例时一并填进"指定文件"。 */
  file?: string;
};
const CAP_HINTS: Record<
  LocalRuntimeCapability,
  { search: string; examples: CapExample[]; note: string }
> = {
  // 各能力只预填**系统默认**那一个模型;每条 model_id / 源 / 文件均已
  // 2026-05 实测可下载 —— ModelScope 真下载校验 GGUF/ZIP 文件头、hf-mirror
  // 校验 whisper ggml 文件头。想要别的模型,用「去 ModelScope 搜 + 粘贴
  // model_id」自行下载。
  chat: {
    search: 'Qwen3 GGUF',
    // 实测:Qwen/Qwen3-4B-Instruct-2507-GGUF 在 ModelScope 404(官方 owner
    // 没发);unsloth/Qwen3-4B-Instruct-2507-GGUF @ ModelScope 在,Q4_K_M
    // 真下载文件头 = GGUF。必须带 file —— 不指定会把整仓 27 个 quant 全拉下来。
    examples: [
      {
        id: 'unsloth/Qwen3-4B-Instruct-2507-GGUF',
        source: 'modelscope',
        file: 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
        label: 'Qwen3-4B-Instruct-2507 · Q4_K_M (系统默认)',
        note: '约 2.5GB · 系统默认对话模型,llama.cpp 直接加载',
      },
    ],
    note: 'chat 用 GGUF 单文件;示例即系统默认,点一下即装。其它模型可搜索后粘贴 model_id 下载。',
  },
  embedding: {
    search: 'bge-m3 GGUF',
    // 实测:gpustack/bge-m3-GGUF @ ModelScope,bge-m3-Q8_0.gguf 真下载文件头 = GGUF。
    examples: [
      {
        id: 'gpustack/bge-m3-GGUF',
        source: 'modelscope',
        file: 'bge-m3-Q8_0.gguf',
        label: 'bge-m3 · Q8_0 (系统默认)',
        note: '约 605MB · 100+ 语种 · 多语种+中文检索强,办公场景首选',
      },
    ],
    note: 'embedding 只支持 GGUF 单文件;示例即系统默认 bge-m3 Q8_0,点一下即装。其它模型可搜索后粘贴 model_id。',
  },
  rerank: {
    search: 'reranker GGUF',
    // 实测:gpustack/bge-reranker-v2-m3-GGUF @ ModelScope,
    // bge-reranker-v2-m3-Q8_0.gguf 真下载文件头 = GGUF。
    examples: [
      {
        id: 'gpustack/bge-reranker-v2-m3-GGUF',
        source: 'modelscope',
        file: 'bge-reranker-v2-m3-Q8_0.gguf',
        label: 'bge-reranker-v2-m3 · Q8_0 (系统默认)',
        note: '约 606MB · 100+ 语种 · 跟 bge-m3 embedding 同家族对齐',
      },
    ],
    note: 'rerank 只支持 GGUF 单文件;示例即系统默认 bge-reranker-v2-m3 Q8_0,点一下即装。其它模型可搜索后粘贴 model_id。',
  },
  asr: {
    search: 'whisper ggml',
    // 实测:whisper.cpp ggml 在 ModelScope 没有现成仓;ggerganov/whisper.cpp
    // @ HuggingFace 经 hf-mirror,ggml-tiny.bin 真下载文件头 = whisper ggml
    // 魔数。必须带 file —— 整仓有几十个不同大小的 ggml。
    examples: [
      {
        id: 'ggerganov/whisper.cpp',
        source: 'huggingface',
        file: 'ggml-tiny.bin',
        label: 'whisper tiny · ggml (系统默认)',
        note: '约 74MB · whisper.cpp ggml 单文件,经 hf-mirror 镜像下载',
      },
    ],
    note: 'asr 用 whisper.cpp ggml 单文件;示例即系统默认 tiny(走 hf-mirror)。其它模型可搜索后粘贴 model_id。',
  },
  'image-embedding': {
    search: 'CLIP',
    // openai/clip-vit-base-patch32 @ HuggingFace(经 hf-mirror);ModelScope
    // 上对应 openai-mirror/clip-vit-base-patch32 —— 主源 404 后端自动换源。
    examples: [
      {
        id: 'openai/clip-vit-base-patch32',
        source: 'huggingface',
        label: 'CLIP ViT-B/32 (系统默认)',
        note: '标准 CLIP,transformers 直接加载、不挑依赖(整仓下载,经 hf-mirror)',
      },
    ],
    note: '系统默认图像模型 CLIP ViT-B/32。其它图像模型(jina-clip、chinese-clip 等)可搜索后自行粘贴 model_id 下载。',
  },
};

function modelscopeSearchURL(keyword: string): string {
  return `https://modelscope.cn/models?name=${encodeURIComponent(keyword)}`;
}

export const InstallModelDialog: React.FC<InstallModelDialogProps> = ({
  open,
  onOpenChange,
  capability,
  onSuccess,
}) => {
  const hint = CAP_HINTS[capability];
  const [source, setSource] = React.useState<InstallModelSource>('modelscope');
  const [modelId, setModelId] = React.useState('');
  const [specificFile, setSpecificFile] = React.useState('');
  const [job, setJob] = React.useState<InstallModelJob | null>(null);
  const [scanResult, setScanResult] = React.useState<ScanAndMountResult | null>(null);
  const [pending, setPending] = React.useState<'install' | 'scan' | null>(null);
  const [bundledRoot, setBundledRoot] = React.useState<string>('');

  // 打开 Dialog 时重置临时状态(保留 source/modelId 偏好以防用户失误关闭)
  React.useEffect(() => {
    if (!open) {
      setJob(null);
      setScanResult(null);
      setPending(null);
    }
  }, [open]);

  // Dialog 打开 / 切 cap 时,对 verified cap 自动预填 model_id + source +
  // specific_file(从项目 install_job._RELEASE_MANIFEST 同步过来,实测可下)。
  // 非 verified cap 不动 — 用户从 examples buttons 主动挑,免得装下来跑不通。
  React.useEffect(() => {
    if (!open) return;
    const verified = VERIFIED_DEFAULTS[capability];
    if (!verified) return;
    setModelId(verified.id);
    setSource(verified.source);
    setSpecificFile(verified.file ?? '');
  }, [open, capability]);

  // 轮询 job 状态:running 时每 2s 拉一次
  React.useEffect(() => {
    if (!job || !pending) return;
    if (job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled') {
      setPending(null);
      if (job.status === 'succeeded') {
        notifySuccess('模型下载完成', job.target_dir || '已落入本地模型目录');
        onSuccess?.();
      }
      return;
    }
    const tid = window.setTimeout(async () => {
      try {
        const fresh = await serverModelsInstallModel.get(job.id);
        setJob(fresh);
      } catch (e) {
        reportError('查询下载进度失败', e instanceof Error ? e.message : String(e));
        setPending(null);
      }
    }, 2000);
    return () => window.clearTimeout(tid);
  }, [job, pending, onSuccess]);

  const onStartInstall = async () => {
    // 防重入兜底:button disabled 已挡常态点击,但 setPending → reconciliation →
    // button disabled 生效之间有一帧 async 窗口,用户连点(或失败后多次重试)会
    // 触发多次后端 job — 现象上就是"服务端下载了好几次"。这里 guard 兜底。
    if (pending) return;
    const id = modelId.trim();
    if (!id) {
      reportError('请输入 model_id', '形如 owner/name,例如 ' + (hint.examples[0]?.id ?? ''));
      return;
    }
    setPending('install');
    setScanResult(null);
    try {
      const j = await serverModelsInstallModel.start({
        source,
        model_id: id,
        capability: CAP_TO_CATALOG[capability],
        specific_file: specificFile.trim() || null,
      });
      setJob(j);
    } catch (e) {
      reportError('启动下载失败', e instanceof Error ? e.message : String(e));
      setPending(null);
    }
  };

  const onCancelInstall = () => {
    if (!job) return;
    // 后端 cancel 只 set _cancel_requested flag,但 modelscope / huggingface 的
    // snapshot_download 是同步阻塞、不可中断 — 线程要等 SDK 当前下载分片自然结束
    // 才命中下一个 _check_cancel 退出。所以同步 await cancel API 拿回来的 job
    // status 一般还是 running,UI 看上去"按了没反应"。
    //
    // 修法:前端立即收尾 UI(停轮询 + mock cancelled 状态)给用户即时反馈,后端
    // cancel 改 fire-and-forget。线程实际何时退出对用户不可见,也无害(target_dir
    // 用户随时可手动清理)。
    const jobSnapshot = job;
    setPending(null);
    setJob({
      ...jobSnapshot,
      status: 'cancelled',
      log_tail: [
        ...jobSnapshot.log_tail,
        '[install_model] 已请求取消;后台 SDK 阻塞调用会在当前下载片段完成后退出',
      ],
    });
    void serverModelsInstallModel.cancel(jobSnapshot.id).catch((e) => {
      reportError('取消失败', e instanceof Error ? e.message : String(e));
    });
  };

  const onScanAndMount = async () => {
    setPending('scan');
    setJob(null);
    try {
      // 对话框是按能力打开的(rerank 对话框 / embedding 对话框 …),
      // 「扫描挂载」只扫当前这个能力对应的 bundled 子目录,不碰别的 cap。
      const r = await serverModelsScanAndMount.invoke(CAP_TO_CATALOG[capability]);
      setScanResult(r);
      setBundledRoot(r.bundled_root);
      onSuccess?.();

      // 提示要讲清楚:一共有几个模型、本次有没有变化、有没有 cap 加载失败。
      // "发现 0 个新模型" 信息量太小 —— 增量为 0 不等于没模型。
      const sm = r.summary;
      const total = sm?.total ?? -1;
      if (total === 0) {
        notifyInfo(
          '未发现任何本地模型',
          `把模型文件夹放进 ${r.bundled_root || 'bundled'}\\<能力>\\(如 rerank、embedding)后再点「扫描挂载」,` +
            `或用上方「在线下载」一键安装。`,
        );
        return;
      }
      const lines: string[] = [];
      if (total > 0) lines.push(`本地共 ${total} 个模型`);
      lines.push(
        `本次新增 ${r.scan_delta.added}、更新 ${r.scan_delta.updated}` +
          (r.scan_delta.removed > 0 ? `、移除 ${r.scan_delta.removed}` : ''),
      );
      if (r.auto_started.length > 0) {
        lines.push(`已自动启动:${r.auto_started.join('、')}`);
      }
      const problems = sm?.problems ?? [];
      if (problems.length > 0) {
        lines.push(
          `⚠ ${problems.map((p) => p.capability).join('、')} 加载失败 — 详见下方说明`,
        );
        notifyInfo('扫描完成,但部分模型未就绪', lines.join('\n'));
      } else {
        notifySuccess('扫描完成', lines.join('\n'));
      }
    } catch (e) {
      reportError('扫描失败', e instanceof Error ? e.message : String(e));
    } finally {
      setPending(null);
    }
  };

  // 进 Dialog 时先尝试拿一次 bundled_root(免触发 scan,直接读后端拼好的路径)
  // 走 scan_and_mount 同接口最简单,但它会真的扫盘,稍慢。这里 lazy:用户点
  // 「打开本地模型目录」时再触发,或者第一次 scan 时一并填上。
  // 为了让"复制路径"按钮也能用,这里在 open 后做一次 lightweight scan(0 改动场景很快)。
  React.useEffect(() => {
    if (!open || bundledRoot) return;
    // 同样带上当前 cap:这次只为拿 bundled_root,scan 范围越小越快,
    // 也避免顺手把别的能力的 sidecar 启动起来。
    void serverModelsScanAndMount
      .invoke(CAP_TO_CATALOG[capability])
      .then((r) => setBundledRoot(r.bundled_root))
      .catch(() => {});
  }, [open, bundledRoot, capability]);

  const isBusy = pending !== null;
  const installRunning =
    job && (job.status === 'queued' || job.status === 'running');
  const installDone = job?.status === 'succeeded';
  const installFailed = job?.status === 'failed' || job?.status === 'cancelled';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* max-h + flex 列布局:Header 固定 / 中间内容滚动 / Footer 固定;防小屏(笔记本 768)
          + 长 example model_id 把上下 Header/Footer 顶出可视区。 */}
      <DialogContent className="flex max-h-[calc(100vh-4rem)] max-w-2xl flex-col">
        <DialogHeader>
          <DialogTitle>下载{CAP_LABEL[capability]}</DialogTitle>
          <DialogDescription>
            从 ModelScope(国内)或 Hugging Face 拉一个模型到本地,装完即用。
            或者你已经手动下好了文件,用下面的「扫描挂载」一键识别。
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 space-y-5 overflow-y-auto py-2 pr-1">
          {/* ─────── 路径 A:在线下载 ─────── */}
          <section className="space-y-2 rounded-md border border-[var(--cy-border-subtle)] p-3">
            <div className="text-sm font-medium">方式 1:在线下载</div>
            <div className="text-xs text-[var(--cy-text-tertiary)]">
              在 ModelScope 找到一个模型(推荐关键字:
              <code className="rounded bg-[var(--cy-surface-elev1)] px-1">{hint.search}</code>
              )→ 复制它的 model_id(形如 ``owner/name``)粘到下方。
              {hint.note}
            </div>

            <div>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  void getPlatform().shell.openExternal(modelscopeSearchURL(hint.search))
                }
              >
                <ExternalLink className="mr-1 h-3.5 w-3.5" />
                打开 ModelScope 搜索
              </Button>
            </div>
            {/* 示例 button 单独成行;长 model_id(如 Qwen/Qwen3-4B-Instruct-2507-GGUF)
                需要 break-all 才能在窄宽 Dialog 里换行而不顶破。 */}
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-[var(--cy-text-tertiary)]">示例:</span>
              {hint.examples.map((eg) => (
                <button
                  key={eg.id}
                  type="button"
                  className="max-w-full break-all rounded border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-1.5 py-0.5 text-left text-[11px] hover:bg-[var(--cy-surface-elev1)]"
                  onClick={() => {
                    setModelId(eg.id);
                    setSource(eg.source);
                    // GGUF 多 quant 仓库:示例带 file 时一并填,空则清掉上次的残留
                    setSpecificFile(eg.file ?? '');
                  }}
                  title={
                    (eg.note ? eg.note + '\n' : '') +
                    `${eg.id}${eg.file ? ' :: ' + eg.file : ''}\n点击填到下方 (来源:${eg.source})`
                  }
                >
                  {eg.label ?? eg.id}
                </button>
              ))}
            </div>

            {/* minmax(0,1fr) 等价于把 1fr 列 min-width 强制成 0,长 placeholder 不会撑宽 Input */}
            <div className="grid grid-cols-[5rem_minmax(0,1fr)] items-center gap-2">
              <label className="text-xs text-[var(--cy-text-tertiary)]">来源</label>
              <select
                value={source}
                onChange={(e) => setSource(e.target.value as InstallModelSource)}
                disabled={isBusy}
                className="w-32 rounded-md border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-2 py-1 text-xs"
              >
                <option value="modelscope">ModelScope(国内)</option>
                <option value="huggingface">Hugging Face(走 hf-mirror)</option>
              </select>

              <label className="text-xs text-[var(--cy-text-tertiary)]">model_id</label>
              <Input
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                placeholder={hint.examples[0]?.id ?? 'owner/name'}
                disabled={isBusy}
              />

              <label
                className="text-xs text-[var(--cy-text-tertiary)]"
                title="GGUF 多 quant 仓库挑一个 .gguf 文件;HF transformers 仓库留空"
              >
                单文件(可选)
              </label>
              <Input
                value={specificFile}
                onChange={(e) => setSpecificFile(e.target.value)}
                placeholder="留空 = 整仓 snapshot(transformers / sentence-transformers)"
                disabled={isBusy}
              />
            </div>

            <div className="flex gap-2">
              <Button size="sm" onClick={onStartInstall} disabled={isBusy || !modelId.trim()}>
                {pending === 'install' ? (
                  <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                ) : null}
                开始下载
              </Button>
              {installRunning && (
                <Button size="sm" variant="outline" onClick={onCancelInstall}>
                  取消下载
                </Button>
              )}
            </div>

            {job && (
              <div className="rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-elev1)] p-2 text-xs">
                <div className="font-mono">
                  status={job.status} · step={job.step} · {job.progress_pct.toFixed(1)}%
                </div>
                {job.progress_message && (
                  <div className="mt-1 text-[var(--cy-text-secondary)]">{job.progress_message}</div>
                )}
                {/* 进度条 */}
                <div className="mt-2 h-1.5 w-full rounded bg-[var(--cy-surface-base)]">
                  <div
                    className="h-1.5 rounded bg-emerald-500 transition-all"
                    style={{ width: `${Math.max(0, Math.min(100, job.progress_pct))}%` }}
                  />
                </div>
                {installDone && (
                  <div className="mt-2 text-emerald-700 dark:text-emerald-300">
                    ✓ 完成,落到:<code>{job.target_dir}</code>
                  </div>
                )}
                {installFailed && (
                  <div className="mt-2 text-rose-700 dark:text-rose-300">
                    ✗ {job.status};查看日志:
                    <pre className="mt-1 max-h-32 overflow-y-auto rounded bg-rose-50 p-1.5 text-[10px] dark:bg-rose-950/30">
                      {job.log_tail.slice(-10).join('\n')}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </section>

          {/* ─────── 路径 B:手动放好,扫描挂载 ─────── */}
          <section className="space-y-2 rounded-md border border-[var(--cy-border-subtle)] p-3">
            <div className="text-sm font-medium">方式 2:我已经下好了文件,扫描挂载</div>
            <div className="text-xs text-[var(--cy-text-tertiary)]">
              已经从其它来源拿到了模型(GGUF / safetensors / 整个 HF 仓库)?
              把它们放到下面这棵目录树的指定位置,然后点「扫描挂载」,后端按目录关键字
              识别 capability + 自动启动对应服务。
            </div>

            <div className="flex items-center gap-2">
              <code className="flex-1 truncate rounded border border-[var(--cy-border-default)] bg-[var(--cy-surface-elev1)] px-2 py-1 text-xs">
                {bundledRoot || '(载入中…)'}
              </code>
              <Button
                size="sm"
                variant="outline"
                disabled={!bundledRoot}
                onClick={() => {
                  if (bundledRoot) {
                    void navigator.clipboard?.writeText(bundledRoot);
                    notifySuccess('已复制路径', bundledRoot);
                  }
                }}
                title="复制目录路径"
              >
                <Copy className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!bundledRoot}
                onClick={() => {
                  // 打开本地文件夹要走 shell.openPath(Rust 端按平台调
                  // explorer/open/xdg-open)。不能用 openExternal(file://...)
                  // —— plugin-shell 把它当 URL 正则校验,文件夹路径过不了。
                  if (bundledRoot) {
                    void getPlatform()
                      .shell.openPath(bundledRoot)
                      .catch((e) => reportError(e, '打开文件夹失败'));
                  }
                }}
                title="用资源管理器打开"
              >
                <FolderOpen className="h-3.5 w-3.5" />
              </Button>
            </div>

            {/* cap → 物理子目录的映射跟后端 _CAP_TO_BUNDLED_DIR 对齐;
                注意 image-embedding 走 image/(不是 image-embedding/),OCR 才走 ocr/。 */}
            {(() => {
              const subdir =
                capability === 'image-embedding' ? 'image' : capability;
              const root = bundledRoot
                ? bundledRoot.replace(/[/\\]+$/, '')
                : '<bundled>';
              // 路径分隔符跟 bundledRoot 保持一致:Windows 上后端 pathlib 输出 '\',
              // 不能跟 JSX 拼的 '/' 混用 (会显示 C:\...\bundled/embedding/foo 丑且
              // 复制粘贴可能解析失败)。bundledRoot 含 '\' = Windows,否则 POSIX。
              const sep = bundledRoot && bundledRoot.includes('\\') ? '\\' : '/';
              const exampleOwner =
                capability === 'chat'
                  ? 'Qwen--Qwen3-4B-Instruct-2507-GGUF'
                  : capability === 'embedding'
                  ? 'gpustack--bge-m3-GGUF'
                  : capability === 'rerank'
                  ? 'gpustack--bge-reranker-v2-m3-GGUF'
                  : capability === 'asr'
                  ? 'ggerganov--whisper.cpp'
                  : 'jinaai--jina-clip-v2';
              const needsGguf =
                capability === 'chat' ||
                capability === 'embedding' ||
                capability === 'rerank';
              return (
                <div className="space-y-1.5 rounded border border-amber-400/40 bg-amber-50/40 p-2 text-[11px] text-[var(--cy-text-secondary)] dark:bg-amber-950/20">
                  <div className="font-medium text-[var(--cy-text-primary)]">
                    {CAP_LABEL[capability]} 的正确目录结构:
                  </div>
                  <pre className="overflow-x-auto rounded bg-[var(--cy-surface-base)] p-1.5 font-mono text-[10px] leading-relaxed">{[
                    `${root}${sep}${subdir}${sep}${exampleOwner}${sep}`,
                    `├── config.json`,
                    `├── ${needsGguf ? '*.gguf            ← GGUF 量化权重' : '*.safetensors / *.onnx / *.bin'}`,
                    `└── tokenizer.json  (有就放,没有跳过)`,
                  ].join('\n')}</pre>
                  <ul className="ml-4 list-disc space-y-0.5">
                    <li>
                      <b>必须套一层 <code className="bg-[var(--cy-surface-elev1)] px-1">&lt;owner--model&gt;/</code> 子目录</b> —
                      跟「下载模型」自动落地格式一致,多模型并存才不打架。
                    </li>
                    {capability === 'image-embedding' && (
                      <li>
                        <b>放到 <code className="bg-[var(--cy-surface-elev1)] px-1">image/</code> 不是 <code className="bg-[var(--cy-surface-elev1)] px-1">image-embedding/</code></b> —
                        这是图像嵌入(CLIP/ViT);OCR 模型放 <code className="bg-[var(--cy-surface-elev1)] px-1">ocr/</code> 子目录。
                      </li>
                    )}
                    {needsGguf && (
                      <li>
                        <b>当前 runtime 是 llama-server,只吃 GGUF 量化版</b> —
                        HF safetensors 仓库放进来会被扫到但 sidecar 起不来。
                        推荐 <code className="bg-[var(--cy-surface-elev1)] px-1">{exampleOwner}</code> 这种 *-GGUF 仓库。
                      </li>
                    )}
                  </ul>
                </div>
              );
            })()}

            <Button size="sm" onClick={() => void onScanAndMount()} disabled={isBusy}>
              {pending === 'scan' ? (
                <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RotateCw className="mr-1 h-3.5 w-3.5" />
              )}
              我已放好,扫描挂载
            </Button>

            {scanResult && (
              <div className="space-y-1.5 rounded-md border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-elev1)] p-2 text-xs">
                {/* 现状:一共几个模型 + 各能力几个 —— 增量为 0 不代表没模型 */}
                {scanResult.summary ? (
                  scanResult.summary.total === 0 ? (
                    <div className="text-amber-700 dark:text-amber-300">
                      未发现任何本地模型。把模型文件夹放进{' '}
                      <code className="bg-[var(--cy-surface-base)] px-1">
                        {bundledRoot || 'bundled'}\{capability}\
                      </code>{' '}
                      后再点「扫描挂载」,或用上方「在线下载」。
                    </div>
                  ) : (
                    <div>
                      本地共 <b>{scanResult.summary.total}</b> 个模型
                      {Object.keys(scanResult.summary.by_capability).length > 0 && (
                        <span className="text-[var(--cy-text-tertiary)]">
                          {' '}
                          ·{' '}
                          {Object.entries(scanResult.summary.by_capability)
                            .map(([cap, n]) => `${cap} ${n}`)
                            .join(' / ')}
                        </span>
                      )}
                    </div>
                  )
                ) : null}

                <div className="text-[var(--cy-text-tertiary)]">
                  本次:新增 {scanResult.scan_delta.added} · 更新{' '}
                  {scanResult.scan_delta.updated} · 移除{' '}
                  {scanResult.scan_delta.removed}
                </div>

                {scanResult.auto_started.length > 0 && (
                  <div className="text-emerald-700 dark:text-emerald-300">
                    ✓ 已自动启动:{scanResult.auto_started.join('、')}
                  </div>
                )}

                {/* 加载失败的 cap:逐条给原因,GGUF 格式错单独给可执行建议 */}
                {(scanResult.summary?.problems ?? []).map((p) => {
                  const isGgufFmt = /gguf/i.test(p.reason);
                  return (
                    <div
                      key={p.capability}
                      className="rounded border border-amber-300/60 bg-amber-50 p-1.5 text-amber-800 dark:border-amber-700/50 dark:bg-amber-950/40 dark:text-amber-200"
                    >
                      ⚠ <b>{p.capability}</b> 加载失败:{p.reason}
                      {isGgufFmt && (
                        <div className="mt-0.5">
                          该能力的本地引擎只支持 <b>GGUF</b> 模型。当前放的是 HF
                          safetensors 整仓 → 删掉它,改用上方示例下载{' '}
                          <code className="bg-[var(--cy-surface-base)] px-1">
                            *-GGUF
                          </code>{' '}
                          量化版本。
                        </div>
                      )}
                    </div>
                  );
                })}

                {scanResult.summary &&
                  scanResult.summary.total > 0 &&
                  scanResult.scan_delta.added === 0 &&
                  scanResult.scan_delta.updated === 0 &&
                  (scanResult.summary.problems ?? []).length === 0 && (
                    <div className="text-[var(--cy-text-tertiary)]">
                      模型清单无变化 —— 若刚放了新文件,确认它在{' '}
                      {capability}/ 子目录的独立文件夹里。
                    </div>
                  )}
              </div>
            )}
          </section>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
