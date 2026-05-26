/**
 * 4 类 KB 创建 mutation 统一封装。
 *
 * 后端契约:
 *   document  → POST /knowledge_base/create_knowledge_base
 *   image     → POST /knowledge_source/  (kind=image)
 *   vector    → POST /knowledge_source/  (kind=vector|vs)  外部向量库 BYO
 *   structured 由 AddStructuredSourceDialog 自带流程,这里不重复
 *
 * 写后:invalidate ku.list 刷出新卡片
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { kb, knowledgeSource } from '@chayuan/api';

export interface CreateDocumentKbInput {
  knowledge_base_name: string;
  kb_info?: string;
  visibility?: 'private' | 'public';
  /** @deprecated 已收敛到设置面板,前端创建时不再传;留位仅供二期 admin 工具使用 */
  vector_store_type?: string;
  /** @deprecated 同上 */
  embed_model?: string;
}

export function useCreateDocumentKb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateDocumentKbInput) =>
      kb.create({
        knowledge_base_name: input.knowledge_base_name,
        kb_info: input.kb_info,
        // 留 undefined 让服务器读 Settings.kb_settings.DEFAULT_VS_TYPE / DEFAULT_EMBEDDING_MODEL
        vector_store_type: input.vector_store_type,
        embed_model: input.embed_model,
        visibility: input.visibility ?? 'private',
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['ku.list'] });
    },
  });
}

export interface CreateImageKbInput {
  name: string;
  kb_info?: string;
  visibility?: 'private' | 'public';
  /** @deprecated 已收敛到设置面板;留位供二期 admin 工具使用 */
  embedder_model?: string;
}

export function useCreateImageKb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateImageKbInput) =>
      knowledgeSource.create({
        name: input.name,
        kind: 'image',
        display_name: input.name,
        description: input.kb_info ?? '',
        database: input.name,
        // 不传 options.embedder_model:服务器走 image_source/connector.default_model_name()
        options: input.embedder_model ? { embedder_model: input.embedder_model } : {},
        visibility: input.visibility ?? 'private',
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['ku.list'] });
    },
  });
}

export interface CreateVectorKbInput {
  name: string;
  description?: string;
  /** 外部向量库类型:milvus / pg_vector / es_vec / chroma */
  dialect: string;
  host: string;
  port: number;
  database?: string;
  username?: string;
  password?: string;
  options?: Record<string, unknown>;
  visibility?: 'private' | 'public';
}

export function useCreateVectorKb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateVectorKbInput) =>
      knowledgeSource.create({
        name: input.name,
        kind: 'vs',
        display_name: input.name,
        description: input.description ?? '',
        dialect: input.dialect,
        host: input.host,
        port: input.port,
        database: input.database,
        username: input.username,
        password: input.password,
        options: input.options,
        visibility: input.visibility ?? 'private',
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['ku.list'] });
    },
  });
}
