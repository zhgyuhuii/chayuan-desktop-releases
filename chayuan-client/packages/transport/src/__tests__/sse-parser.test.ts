import { describe, expect, it } from 'vitest';
import { parseStructuredSSE } from '../sse-parser';
import type { StructuredSSEEvent } from '../types';

function streamFromString(s: string): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      c.enqueue(enc.encode(s));
      c.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<StructuredSSEEvent[]> {
  const out: StructuredSSEEvent[] = [];
  for await (const ev of parseStructuredSSE(stream)) out.push(ev);
  return out;
}

describe('parseStructuredSSE', () => {
  it('OpenAI 兼容 token + done', async () => {
    const body =
      `data: {"id":"x","choices":[{"delta":{"content":"Hi"}}]}\n\n` +
      `data: {"id":"x","choices":[{"delta":{"content":" there"}}]}\n\n` +
      `data: [DONE]\n\n`;
    const events = await collect(streamFromString(body));
    expect(events).toEqual([
      { type: 'token', delta: 'Hi', reasoning: undefined, messageId: 'x' },
      { type: 'token', delta: ' there', reasoning: undefined, messageId: 'x' },
      { type: 'done' },
    ]);
  });

  it('AG-UI 风格 tool_call_start + delta + end', async () => {
    const body =
      `event: tool_call_start\ndata: {"id":"t1","name":"web_search"}\n\n` +
      `event: tool_call_delta\ndata: {"id":"t1","arguments":"{\\"q\\":"}\n\n` +
      `event: tool_call_delta\ndata: {"id":"t1","arguments":"\\"hi\\"}"}\n\n` +
      `event: tool_call_end\ndata: {"id":"t1","result":{"items":[]}}\n\n` +
      `event: done\ndata: {"finish_reason":"stop"}\n\n`;
    const events = await collect(streamFromString(body));
    expect(events.filter((e) => e.type === 'tool_call')).toHaveLength(4);
    expect(events.at(-1)).toEqual({ type: 'done', finishReason: 'stop' });
  });

  it('reasoning_content 流式', async () => {
    const body =
      `data: {"choices":[{"delta":{"reasoning_content":"思考1..."}}]}\n\n` +
      `data: {"choices":[{"delta":{"content":"答"}}]}\n\n` +
      `data: [DONE]\n\n`;
    const events = await collect(streamFromString(body));
    expect(events[0]).toMatchObject({ type: 'token', reasoning: '思考1...' });
    expect(events[1]).toMatchObject({ type: 'token', delta: '答' });
  });

  it('keep-alive 注释跳过；CRLF 兼容', async () => {
    const body = `: keep-alive\r\n\r\ndata: {"choices":[{"delta":{"content":"Y"}}]}\r\n\r\ndata: [DONE]\r\n\r\n`;
    const events = await collect(streamFromString(body));
    expect(events).toEqual([
      { type: 'token', delta: 'Y', reasoning: undefined, messageId: undefined },
      { type: 'done' },
    ]);
  });

  it('error 事件即时终止', async () => {
    const body =
      `data: {"choices":[{"delta":{"content":"a"}}]}\n\n` +
      `data: {"error":{"message":"模型超载","code":429}}\n\n` +
      `data: {"choices":[{"delta":{"content":"b"}}]}\n\n`;
    const events = await collect(streamFromString(body));
    const last = events.at(-1)!;
    expect(last.type).toBe('error');
    if (last.type === 'error') expect(last.message).toBe('模型超载');
    expect(events.find((e) => e.type === 'token' && e.delta === 'b')).toBeUndefined();
  });

  it('sources 事件携带 mounted 摘要时同时产出挂载与引用事件', async () => {
    const body =
      `event: sources\n` +
      `data: {"sources":[{"title":"合同.pdf","kb_name":"kb1","file_name":"合同.pdf"}],"chunks":[],"mounted":{"mount_count":1,"fewshot_count":2,"boost_count":3},"mounted_sources":[{"mount_id":"m1","name":"合同问答挂载","artifact_type":"fewshot_examples","sample_ids":["s1"]}]}\n\n`;
    const events = await collect(streamFromString(body));
    expect(events[0]).toMatchObject({
      type: 'mounted',
      summary: { mount_count: 1, fewshot_count: 2, boost_count: 3 },
      sources: [{ mount_id: 'm1', name: '合同问答挂载', artifact_type: 'fewshot_examples', sample_ids: ['s1'] }],
    });
    expect(events[1]).toMatchObject({
      type: 'citation',
      sources: [{ title: '合同.pdf', kb_name: 'kb1', file_name: '合同.pdf' }],
    });
  });

  it('final 事件携带 mounted 摘要时产出挂载事件', async () => {
    const body = `event: final\ndata: {"run_id":"r1","mounted":{"mount_count":1,"safety_rule_count":4}}\n\n`;
    const events = await collect(streamFromString(body));
    expect(events).toEqual([
      {
        type: 'mounted',
        summary: { mount_count: 1, safety_rule_count: 4 },
        sources: undefined,
      },
    ]);
  });
});
