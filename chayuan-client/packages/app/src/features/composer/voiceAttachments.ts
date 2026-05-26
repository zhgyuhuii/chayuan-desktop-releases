/**
 * 内存里的"语音附件队列" — ChatComposer 录完一段送出去后,把 Blob 押进来;
 * MessageBubble 挂载新 user 消息时按 content 文本匹配 consume,得到 Blob 渲染播放按钮。
 *
 * 匹配规则:**严格相等**(text === message.content)+ FIFO(同 text 多条按时间)。
 * - 用户录音后通常 1~5 秒内就送上去并进 thread,在场景里 text 完全等同
 * - 跨刷新丢:刷新页面后内存清空,user 消息不再有播放按钮。这是 in-memory 限制,
 *   后续若要持久化得改 chat 消息 schema(role / content 之外新增 audio_ref)
 *
 * 用 Object URL 渲染播放:MessageBubble 自己 URL.createObjectURL(blob) +
 * 卸载时 revoke,本模块只持有 Blob ref,不管 URL 生命周期。
 */

interface PendingEntry {
  text: string;
  blob: Blob;
  pushedAt: number;
}

const pending: PendingEntry[] = [];

/** 录音转写完发送时 push;text 是转写出来的最终文本(用户看到 + onSend 收到的字符串)。 */
export function pushVoiceAttachment(text: string, blob: Blob): void {
  if (!text || !blob) return;
  pending.push({ text, blob, pushedAt: Date.now() });
  // 清理 5 分钟前的孤儿(消息流挂了 / 用户没匹配到的)
  const cutoff = Date.now() - 5 * 60_000;
  while (pending.length > 0 && pending[0]!.pushedAt < cutoff) {
    pending.shift();
  }
}

/** MessageBubble 挂载时调一次;命中返回 Blob 并出队(同样 text 多条不互窜)。 */
export function consumeVoiceAttachment(text: string): Blob | null {
  if (!text) return null;
  const idx = pending.findIndex((p) => p.text === text);
  if (idx === -1) return null;
  return pending.splice(idx, 1)[0]!.blob;
}

/** 测试 / 诊断用。 */
export function _pendingSize(): number {
  return pending.length;
}
