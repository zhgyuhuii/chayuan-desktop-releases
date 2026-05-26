import type { Meta, StoryObj } from '@storybook/react';
import { MessageBubble } from '../features/chat/MessageBubble';
import type { ChatMessage } from '../features/chat/useChayuanChat';

const meta: Meta<typeof MessageBubble> = {
  title: 'Chat / MessageBubble',
  component: MessageBubble,
  tags: ['autodocs'],
};
export default meta;
type Story = StoryObj<typeof MessageBubble>;

const baseUser: ChatMessage = {
  id: 'u1',
  role: 'user',
  content: '请总结一下《百年孤独》的主题',
  createdAt: Date.now(),
};

const baseAssistant: ChatMessage = {
  id: 'a1',
  role: 'assistant',
  content: '《百年孤独》通过布恩迪亚家族的故事，讲述拉丁美洲历史的轮回与魔幻现实主义的精神图景。',
  createdAt: Date.now(),
  traceId: '00000000-0000-4000-8000-000000000000',
};

export const User: Story = { args: { message: baseUser } };

export const Assistant: Story = { args: { message: baseAssistant } };

export const Streaming: Story = {
  args: { message: { ...baseAssistant, content: '思考中', streaming: true } },
};

export const WithReasoning: Story = {
  args: {
    message: {
      ...baseAssistant,
      reasoning: '先回顾原著主题：家族 / 孤独 / 历史循环 / 魔幻现实主义……',
    },
  },
};

export const WithToolCalls: Story = {
  args: {
    message: {
      ...baseAssistant,
      toolCalls: [
        { id: 't1', name: 'web_search', arguments: '{"q":"百年孤独 主题"}', status: 'end', result: { items: [{ title: '维基百科', url: 'https://wikipedia.org' }] } },
      ],
    },
  },
};

export const WithError: Story = {
  args: { message: { ...baseAssistant, content: '', error: '上游模型超时' } },
};

export const InterruptHIL: Story = {
  args: {
    message: { ...baseAssistant, content: '', interrupt: { reason: '即将调用 web_search，需要确认' } },
    onResume: (approved) => console.log('resume', approved),
  },
};
