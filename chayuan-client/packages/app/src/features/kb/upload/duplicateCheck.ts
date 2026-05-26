import { kb as kbApi } from '@chayuan/api';
import { getPlatform } from '@chayuan/platform-shared';

// 走 platform.dialog.confirm(plugin-dialog ask),不要裸 window.confirm:
// Tauri 2 把 window.confirm 重定向到 plugin:dialog|confirm,部分版本下报
// "Command not found"。统一收敛到 platform 层一次。
function confirmAsk(message: string, title = '上传确认'): Promise<boolean> {
  return getPlatform().dialog.confirm(message, { title });
}

function uploadFileName(file: File): string {
  const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return rel && rel.length ? rel : file.name;
}

async function sha256File(file: File): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error('当前环境不支持 SHA-256 指纹计算，无法进行重复检测。');
  }
  const buf = await file.arrayBuffer();
  const hash = await globalThis.crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export async function confirmDuplicateUpload(kbName: string, files: File[]): Promise<boolean> {
  if (!files.length) return true;
  let items: Array<{ file_name: string; file_size: number; sha256: string }>;
  try {
    items = await Promise.all(files.map(async (file) => ({
      file_name: uploadFileName(file),
      file_size: file.size,
      sha256: await sha256File(file),
    })));
  } catch (e) {
    return confirmAsk(`${e instanceof Error ? e.message : String(e)}\n是否继续上传？`);
  }

  const sameBatch = new Map<string, Array<{ file_name: string; file_size: number }>>();
  for (const item of items) {
    const list = sameBatch.get(item.sha256) ?? [];
    list.push({ file_name: item.file_name, file_size: item.file_size });
    sameBatch.set(item.sha256, list);
  }
  const batchDuplicates = [...sameBatch.values()].filter((list) => list.length > 1);
  if (batchDuplicates.length > 0) {
    const lines = batchDuplicates.slice(0, 8).map((list) =>
      `- ${list.map((x) => x.file_name).join('、')} (${formatBytes(list[0]?.file_size ?? 0)})`,
    );
    const omitted = batchDuplicates.length > 8 ? `\n还有 ${batchDuplicates.length - 8} 组本次重复文件未展示。` : '';
    if (!(await confirmAsk(`本次选择的文件中存在重复内容，是否继续上传？\n\n${lines.join('\n')}${omitted}`))) {
      return false;
    }
  }

  let resp: Awaited<ReturnType<typeof kbApi.checkDuplicates>>;
  try {
    resp = await kbApi.checkDuplicates({
      knowledge_base_name: kbName,
      scope: 'accessible',
      items,
    });
  } catch (e) {
    return confirmAsk(`重复检测失败：${e instanceof Error ? e.message : String(e)}\n是否继续上传？`);
  }
  const duplicated = resp.filter((x) => (x.duplicate_count || 0) > 0);
  if (!duplicated.length) return true;
  const lines = duplicated.slice(0, 8).map((item) => {
    const hits = item.duplicates || [];
    const first = hits[0];
    const more = hits.length > 1 ? ` 等 ${hits.length} 份重复` : ' 1 份重复';
    const who = first?.uploader_name || (first?.uploader_id ? `用户 ${first.uploader_id}` : '未知用户');
    const when = first?.upload_time ? new Date(first.upload_time).toLocaleString() : '未知时间';
    const where = first ? `${first.kb_name}/${first.file_name}` : '';
    return `- ${item.file_name}: 已有${more}; ${who}; ${when}; ${formatBytes(item.file_size)}; ${where}`;
  });
  const omitted = duplicated.length > 8 ? `\n还有 ${duplicated.length - 8} 个重复文件未展示。` : '';
  return confirmAsk(
    `检测到 ${duplicated.length} 个文件与历史上传附件重复，是否继续上传？\n\n${lines.join('\n')}${omitted}`,
  );
}
