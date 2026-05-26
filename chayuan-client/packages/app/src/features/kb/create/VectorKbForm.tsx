/**
 * 外部向量库(BYO Milvus / pgvector / es vec / Chroma)创建表单。
 *
 * 后端会用 ExternalVsConnector 探活后落库,登录信息错时直接拒绝。
 *
 * 字段:
 *   - name(唯一标识)/ 描述
 *   - dialect(milvus / pg_vector / es_vec / chroma)
 *   - host / port / database / username / password
 *   - visibility
 */

import * as React from 'react';
import { Loader2, Wifi } from 'lucide-react';
import { Button, Input, Switch } from '@chayuan/ui';
import { knowledgeSource } from '@chayuan/api';
import { useCreateVectorKb } from './useCreateKb';
import { reportError, notifySuccess } from '../../../store/errorDialog';

interface VsDialect {
  value: string;
  label: string;
  defaultPort: number;
}

const VS_DIALECTS: VsDialect[] = [
  { value: 'milvus', label: 'Milvus / Zilliz(向量数据库)', defaultPort: 19530 },
  { value: 'pg', label: 'PostgreSQL(向量扩展)', defaultPort: 5432 },
  { value: 'es', label: 'Elasticsearch(向量字段)', defaultPort: 9200 },
  { value: 'chromadb', label: 'Chroma(向量数据库)', defaultPort: 8000 },
];

export const VectorKbForm: React.FC<{ onSubmitted(sourceId: number): void }> = ({
  onSubmitted,
}) => {
  const [name, setName] = React.useState('');
  const [desc, setDesc] = React.useState('');
  const [dialect, setDialect] = React.useState(VS_DIALECTS[0]!.value);
  const [host, setHost] = React.useState('');
  const [port, setPort] = React.useState(VS_DIALECTS[0]!.defaultPort);
  const [database, setDatabase] = React.useState('');
  const [username, setUsername] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [isPublic, setIsPublic] = React.useState(false);
  const [testing, setTesting] = React.useState(false);
  const [testMsg, setTestMsg] = React.useState<string | null>(null);
  const create = useCreateVectorKb();

  const validName = isValidKbName(name);

  const onChangeDialect = (v: string) => {
    setDialect(v);
    const d = VS_DIALECTS.find((x) => x.value === v);
    if (d) setPort(d.defaultPort);
  };

  const onTest = async () => {
    setTesting(true);
    setTestMsg(null);
    try {
      const r = await knowledgeSource.testConnection({
        dialect, host, port, database, username, password, kind: 'vs',
      });
      setTestMsg(r.ok ? `✅ ${r.msg || '连通正常'}` : `❌ ${r.msg || '连接失败'}`);
    } catch (e) {
      setTestMsg(`❌ ${(e as Error).message}`);
    } finally {
      setTesting(false);
    }
  };

  const onSubmit = async () => {
    if (!validName || !host) return;
    try {
      const r = await create.mutateAsync({
        name: name.trim(),
        description: desc.trim() || undefined,
        dialect,
        host: host.trim(),
        port: Number(port),
        database: database.trim() || undefined,
        username: username.trim() || undefined,
        password: password || undefined,
        visibility: isPublic ? 'public' : 'private',
      });
      notifySuccess(`已连接外部向量库:${name}`);
      onSubmitted(r.id);
    } catch (e) {
      reportError(e, '连接外部向量库失败');
    }
  };

  return (
    <div className="space-y-4">
      <Field label="名称" required>
        <Input
          value={name}
          autoFocus
          placeholder="例如:业务向量库 / 产品检索"
          onChange={(e) => setName(e.target.value)}
        />
        {name && !validName && (
          <p className="mt-1 text-[11px] text-red-600">名称不能为空,且不能包含 / \ 或控制字符</p>
        )}
      </Field>
      <Field label="描述" optional>
        <Input
          value={desc}
          placeholder="可选,用于卡片副标题"
          onChange={(e) => setDesc(e.target.value)}
        />
      </Field>
      <Field label="向量库类型">
        <select
          value={dialect}
          onChange={(e) => onChangeDialect(e.target.value)}
          className="h-9 w-full rounded-md border border-[var(--cy-border-default)] bg-[var(--cy-surface-base)] px-3 text-sm focus-visible:border-[var(--cy-brand-400)] focus-visible:outline-none"
        >
          {VS_DIALECTS.map((d) => (
            <option key={d.value} value={d.value}>{d.label}</option>
          ))}
        </select>
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <div className="col-span-2">
          <Field label="主机 host" required>
            <Input
              value={host}
              placeholder="127.0.0.1"
              onChange={(e) => setHost(e.target.value)}
            />
          </Field>
        </div>
        <Field label="端口" required>
          <Input
            type="number"
            value={port}
            onChange={(e) => setPort(Number(e.target.value) || 0)}
          />
        </Field>
      </div>
      <Field label="数据库 / 索引(可选)">
        <Input
          value={database}
          placeholder="default / collection_name"
          onChange={(e) => setDatabase(e.target.value)}
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="用户名" optional>
          <Input value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field label="密码 / Token" optional>
          <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
      </div>
      <Field label="可见性">
        <div className="flex items-center gap-2">
          <Switch checked={isPublic} onCheckedChange={setIsPublic} />
          <span className="text-xs text-[var(--cy-text-secondary)]">
            {isPublic ? '公开:所有登录用户可读' : '私有:仅自己'}
          </span>
        </div>
      </Field>

      {testMsg && (
        <p
          className={`rounded-md px-3 py-2 text-xs ${
            testMsg.startsWith('✅')
              ? 'bg-emerald-50 text-emerald-800'
              : 'bg-red-50 text-red-800'
          }`}
        >
          {testMsg}
        </p>
      )}

      <div className="flex items-center justify-between gap-2 border-t border-[var(--cy-border-subtle)] pt-3">
        <Button size="sm" variant="outline" onClick={onTest} disabled={testing || !host}>
          {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wifi className="h-3.5 w-3.5" />}
          测试连通
        </Button>
        <Button
          size="sm"
          onClick={onSubmit}
          disabled={!validName || !host || create.isPending}
        >
          {create.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          挂接向量库
        </Button>
      </div>
    </div>
  );
};

const Field: React.FC<{
  label: string;
  required?: boolean;
  optional?: boolean;
  children: React.ReactNode;
}> = ({ label, required, optional, children }) => (
  <div className="space-y-1">
    <label className="text-xs font-medium text-[var(--cy-text-secondary)]">
      {label}
      {required && <span className="ml-1 text-red-500">*</span>}
      {optional && <span className="ml-1 text-[10px] text-[var(--cy-text-tertiary)]">(可选)</span>}
    </label>
    {children}
  </div>
);

function isValidKbName(value: string): boolean {
  const s = value.trim();
  return s.length > 0 && s.length <= 100 && !/[\\/\x00-\x1F]/.test(s);
}
