import type { Meta, StoryObj } from '@storybook/react';
import { ToolCallCard } from '../features/chat/toolcards/registry';

const meta: Meta<typeof ToolCallCard> = {
  title: 'Chat / Tool Cards',
  component: ToolCallCard,
  tags: ['autodocs'],
};
export default meta;
type Story = StoryObj<typeof ToolCallCard>;

export const WebSearchCalling: Story = {
  args: { name: 'web_search', args: '{"q":"AGI 进展"}', status: 'start' },
};

export const WebSearchDone: Story = {
  args: {
    name: 'web_search',
    args: '{"q":"AGI 进展"}',
    status: 'end',
    result: {
      results: [
        { title: 'OpenAI 报告', url: 'https://openai.com', snippet: 'GPT-5 公开发布' },
        { title: 'Anthropic 论文', url: 'https://anthropic.com', snippet: 'Claude 4.7 上下文 200K' },
      ],
    },
  },
};

export const Weather: Story = {
  args: {
    name: 'amap_weather',
    args: '{"city":"上海"}',
    status: 'end',
    result: { city: '上海', weather: '小雨', temperature: 18, wind_direction: '东', wind_power: '4', humidity: 78 },
  },
};

export const Calculator: Story = {
  args: {
    name: 'calculator',
    args: '{"expression":"(12+34)*5"}',
    status: 'end',
    result: { result: 230 },
  },
};

export const GenericFallback: Story = {
  args: {
    name: 'custom_tool',
    args: '{"k":"v"}',
    status: 'end',
    result: { ok: true, data: [1, 2, 3] },
  },
};
