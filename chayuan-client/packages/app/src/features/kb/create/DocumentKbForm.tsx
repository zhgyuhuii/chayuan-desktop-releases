/**
 * 文档型 KB 创建表单。
 *
 * 字段:
 *   - knowledge_base_name(必填,用户可见名称;后端会生成独立向量集合名)
 *   - kb_info(简介)
 *   - visibility(私有 / 公开)
 *
 * **不再让用户在这里选向量库类型 / 嵌入模型** —— 设置面板已经统一配置过(并经过
 * 联通性自检),创建时直接沿用 Settings.kb_settings.DEFAULT_VS_TYPE +
 * DEFAULT_EMBEDDING_MODEL。文件落盘也由 Settings.basic_settings.FILE_STORAGE_BACKEND
 * (local / minio)接管,前端无需关心。
 *
 * 创建后回调 onSubmitted(name)。
 */

import * as React from 'react';
import { useNavigate } from '@tanstack/react-router';
import { AlertTriangle, Cpu, Loader2, ShieldCheck } from 'lucide-react';
import { Button, Input, Switch, Textarea } from '@chayuan/ui';
import { serverCapabilityDefaults } from '@chayuan/api';
import { useCreateDocumentKb } from './useCreateKb';
import { reportError, notifySuccess, notifyInfo } from '../../../store/errorDialog';
import { BizError } from '@chayuan/api';

export const DocumentKbForm: React.FC<{ onSubmitted(kbName: string): void }> = ({ onSubmitted }) => {
  const navigate = useNavigate();
  const [name, setName] = React.useState('');
  const [info, setInfo] = React.useState('');
  const [isPublic, setIsPublic] = React.useState(false);
  const [checking, setChecking] = React.useState(false);
  const [missingModel, setMissingModel] = React.useState(false);

  const create = useCreateDocumentKb();

  const validName = isValidKbName(name);

  const onSubmit = async () => {
    if (!validName) return;

    // 守卫:无向量化模型时不让走后端 create(后端会因 embed_model 为空抛 404
    // 之类的难懂错误)。先 list 一次默认 / 候选,缺失就在表单内显示横幅
    // (不用 window.confirm — Tauri 下会触发 dialog plugin ACL 错误)。
    setChecking(true);
    setMissingModel(false);
    let needConfig = false;
    try {
      const d = await serverCapabilityDefaults.list();
      const hasDefault = !!d.defaults?.embedding;
      const hasCandidate = (d.candidates?.embedding ?? []).length > 0;
      needConfig = !hasDefault && !hasCandidate;
    } catch {
      // 接口不可达 / 后端未起来 — 让 create 走原路径,错误由 reportError 兜底。
      needConfig = false;
    } finally {
      setChecking(false);
    }
    if (needConfig) {
      setMissingModel(true);
      return;
    }

    try {
      // 不传 vector_store_type / embed_model:让服务器读 Settings 默认。
      // 改设置走「设置 → 知识库 / 文件存储」,这里不重复造轮子。
      await create.mutateAsync({
        knowledge_base_name: name.trim(),
        kb_info: info.trim() || undefined,
        visibility: isPublic ? 'public' : 'private',
      });
      notifySuccess(`已创建文档库:${name}`);
      onSubmitted(name.trim());
    } catch (e) {
      // 后端 422 = "embedding 未配置,用户操作可解决"。不当作错误显示,改 notifyInfo
      // 给清晰引导(去哪里配置 embedding 模型)。其他错误仍走 reportError 红框。
      if (e instanceof BizError && e.code === 422) {
        notifyInfo('请先配置向量模型', e.message, 10000);
        return;
      }
      reportError(e, '创建文档库失败');
    }
  };

  return (
    <div className="space-y-4">
      <Field label="名称" required hint="支持中文和常见符号,1-100 字">
        <Input
          value={name}
          autoFocus
          placeholder="例如:产品手册 / 法务 FAQ"
          onChange={(e) => setName(e.target.value)}
        />
        {name && !validName && (
          <p className="mt-1 text-[11px] text-red-600">名称不能为空,且不能包含 / \ 或控制字符</p>
        )}
      </Field>
      <Field label="简介(用于 Agent 自动选择 KB)" optional>
        <Textarea
          rows={2}
          value={info}
          placeholder="一句话描述这个库的内容,例如:公司产品 FAQ 与运营手册"
          onChange={(e) => setInfo(e.target.value)}
        />
      </Field>
      <Field label="可见性">
        <div className="flex items-center gap-2">
          <Switch checked={isPublic} onCheckedChange={setIsPublic} />
          <span className="text-xs text-[var(--cy-text-secondary)]">
            {isPublic ? '公开:所有登录用户可读' : '私有:仅自己 + 授权用户'}
          </span>
        </div>
      </Field>

      <div className="flex items-start gap-2 rounded-xl border border-[var(--cy-border-subtle)] bg-[var(--cy-surface-1)] px-3 py-2.5 text-[11px] leading-relaxed text-[var(--cy-text-secondary)]">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-emerald-600" />
        <div>
          向量库 / 嵌入模型 / 文件存储后端均沿用「设置面板」配置。如需调整,请到
          <span className="mx-1 rounded bg-[var(--cy-surface-base)] px-1 py-0.5 font-medium text-[var(--cy-text-primary)]">设置 · 知识库</span>
          统一修改。
        </div>
      </div>

      {missingModel && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50/70 px-3 py-2.5 text-[12px] leading-relaxed text-amber-900"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
          <div className="min-w-0 flex-1">
            <p className="font-medium">尚未配置文本向量(embedding)模型</p>
            <p className="mt-0.5 text-[11px] text-amber-800/80">
              在「模型广场」选择任一支持 embedding 的厂商并启用一个 embedding 模型后,即可创建文档库。
            </p>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="flex-shrink-0"
            onClick={() => void navigate({ to: '/marketplace' })}
          >
            <Cpu className="h-3.5 w-3.5" />
            去模型广场
          </Button>
        </div>
      )}

      <div className="flex justify-end gap-2 border-t border-[var(--cy-border-subtle)] pt-3">
        <Button
          size="sm"
          onClick={onSubmit}
          disabled={!validName || create.isPending || checking}
        >
          {(create.isPending || checking) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {checking ? '检查模型配置…' : '创建文档库'}
        </Button>
      </div>
    </div>
  );
};

const Field: React.FC<{
  label: string;
  required?: boolean;
  optional?: boolean;
  hint?: string;
  children: React.ReactNode;
}> = ({ label, required, optional, hint, children }) => (
  <div className="space-y-1">
    <div className="flex items-baseline justify-between">
      <label className="text-xs font-medium text-[var(--cy-text-secondary)]">
        {label}
        {required && <span className="ml-1 text-red-500">*</span>}
        {optional && <span className="ml-1 text-[10px] text-[var(--cy-text-tertiary)]">(可选)</span>}
      </label>
      {hint && <span className="text-[10px] text-[var(--cy-text-tertiary)]">{hint}</span>}
    </div>
    {children}
  </div>
);

function isValidKbName(value: string): boolean {
  const s = value.trim();
  return s.length > 0 && s.length <= 100 && !/[\\/\x00-\x1F]/.test(s);
}
