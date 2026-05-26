/**
 * KB 管理 hooks：列表 / 创建 / 删除 / 文件 / 检索。
 *
 * 设计：
 * - mutations 都做 onMutate 乐观更新 + 错误回滚 + onSettled invalidate；
 * - 文件列表 query 与 KB 列表分开 cache，文件列表 staleTime 30s；
 * - 搜索调试不用 query（用户主动触发），返回 promise 即可。
 */

import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { kb, type KnowledgeBase, type KbFile } from '@chayuan/api';

export const KB_KEYS = {
  list: ['kb', 'list'] as const,
  files: (name: string) => ['kb', 'files', name] as const,
};

export function useKbList() {
  return useQuery({
    queryKey: KB_KEYS.list,
    queryFn: () => kb.list(),
    staleTime: 60_000,
    retry: 0,
  });
}

export function useKbFiles(name: string | null) {
  return useQuery({
    enabled: !!name,
    queryKey: KB_KEYS.files(name ?? ''),
    queryFn: () => kb.listFiles(name!),
    staleTime: 30_000,
    retry: 0,
    placeholderData: keepPreviousData,
  });
}

export function useCreateKb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: kb.create,
    onSuccess: () => qc.invalidateQueries({ queryKey: KB_KEYS.list }),
  });
}

export function useDeleteKb() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => kb.delete(name),
    onMutate: async (name) => {
      await qc.cancelQueries({ queryKey: KB_KEYS.list });
      const prev = qc.getQueryData<KnowledgeBase[]>(KB_KEYS.list);
      qc.setQueryData<KnowledgeBase[]>(KB_KEYS.list, (cur) => cur?.filter((k) => k.kb_name !== name) ?? []);
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(KB_KEYS.list, ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: KB_KEYS.list }),
  });
}

export function useUploadDocs(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (files: File[]) =>
      kb.uploadDocs({ files, knowledge_base_name: name, to_vector_store: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KB_KEYS.files(name) }),
  });
}

export function useDeleteDocs(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file_names: string[]) =>
      kb.deleteDocs({ knowledge_base_name: name, file_names, delete_content: true }),
    onMutate: async (file_names) => {
      await qc.cancelQueries({ queryKey: KB_KEYS.files(name) });
      const prev = qc.getQueryData<KbFile[]>(KB_KEYS.files(name));
      qc.setQueryData<KbFile[]>(KB_KEYS.files(name), (cur) =>
        cur?.filter((f) => !file_names.includes(f.file_name)) ?? [],
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(KB_KEYS.files(name), ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: KB_KEYS.files(name) }),
  });
}

export function useUpdateKbInfo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: kb.updateInfo,
    onSuccess: () => qc.invalidateQueries({ queryKey: KB_KEYS.list }),
  });
}
