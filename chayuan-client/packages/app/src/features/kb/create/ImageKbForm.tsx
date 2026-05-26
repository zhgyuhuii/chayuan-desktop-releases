/**
 * 图像型 KB 创建表单。
 *
 * 字段:
 *   - name(唯一标识)
 *   - kb_info(简介)
 *   - visibility
 *
 * **图像 embedding 模型由设置面板统一配置**(IMAGE_EMBEDDING_MODEL),
 * 这里不再让用户在创建时挑;文件存储后端(local / minio)同样沿用设置。
 *
 * 创建后回调 onSubmitted(source_id),父级用 src:<id> 高亮新卡。
 */

import * as React from 'react';
import { useNavigate } from '@tanstack/react-router';
import { AlertTriangle, Cpu, Loader2, ShieldCheck } from 'lucide-react';
import { Button, Input, Switch, Textarea } from '@chayuan/ui';
import { serverCapabilityDefaults } from '@chayuan/api';
import { useCreateImageKb } from './useCreateKb';
import { reportError, notifySuccess, notifyInfo } from '../../../store/errorDialog';
import { BizError } from '@chayuan/api';

export const ImageKbForm: React.FC<{ onSubmitted(sourceId: number): void }> = ({ onSubmitted }) => {
  const navigate = useNavigate();
  const [name, setName] = React.useState('');
  const [info, setInfo] = React.useState('');
  const [isPublic, setIsPublic] = React.useState(false);
  const [checking, setChecking] = React.useState(false);
  const [missingModel, setMissingModel] = React.useState(false);
  const create = useCreateImageKb();

  const validName = isValidKbName(name);

  const onSubmit = async () => {
    if (!validName) return;

    // 守卫:无 clip(图像嵌入)模型时,显示内联横幅引导到模型广场。
    // 不用 window.confirm — Tauri 下会触发 dialog plugin ACL 错误。
    setChecking(true);
    setMissingModel(false);
    let needConfig = false;
    try {
      const d = await serverCapabilityDefaults.list();
      const hasDefault = !!d.defaults?.clip;
      const hasCandidate = (d.candidates?.clip ?? []).length > 0;
      needConfig = !hasDefault && !hasCandidate;
    } catch {
      needConfig = false;
    } finally {
      setChecking(false);
    }
    if (needConfig) {
      setMissingModel(true);
      return;
    }

    try {
      // 不传 embedder_model:走 image_source/connector.default_model_name() 默认。
      const r = await create.mutateAsync({
        name: name.trim(),
        kb_info: info.trim() || undefined,
        visibility: isPublic ? 'public' : 'private',
      });
      notifySuccess(`已创建图像库:${name}`);
      onSubmitted(r.id);
    } catch (e) {
      // 后端 422 = "图像 embedding 未配置,用户操作可解决",同 DocumentKbForm 处理
      if (e instanceof BizError && e.code === 422) {
        notifyInfo('请先配置图像嵌入模型', e.message, 10000);
        return;
      }
      reportError(e, '创建图像库失败');
    }
  };

  return (
    <div className="space-y-4">
      <Field label="名称" required hint="支持中文和常见符号,1-100 字">
        <Input
          value={name}
          autoFocus
          placeholder="例如:产品图片 / 设计素材"
          onChange={(e) => setName(e.target.value)}
        />
        {name && !validName && (
          <p className="mt-1 text-[11px] text-red-600">名称不能为空,且不能包含 / \ 或控制字符</p>
        )}
      </Field>
      <Field label="简介" optional>
        <Textarea
          rows={2}
          value={info}
          placeholder="一句话描述这批图像,例如:产品宣传图 / 服装搭配图"
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
          图像嵌入模型 / 文件存储后端均沿用「设置面板」配置。
          创建后把图片拖入卡片即可自动 embed 入索引(PNG / JPG / WEBP)。
        </div>
      </div>

      {missingModel && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50/70 px-3 py-2.5 text-[12px] leading-relaxed text-amber-900"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-600" />
          <div className="min-w-0 flex-1">
            <p className="font-medium">尚未配置图像嵌入(clip)模型</p>
            <p className="mt-0.5 text-[11px] text-amber-800/80">
              在「模型广场」选择支持 image2text / clip 的厂商并启用一个图像 embedding 模型后,即可创建图像库。
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
        <Button size="sm" onClick={onSubmit} disabled={!validName || create.isPending || checking}>
          {(create.isPending || checking) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {checking ? '检查模型配置…' : '创建图像库'}
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
