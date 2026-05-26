import type { Meta, StoryObj } from '@storybook/react';
import { Button } from '@chayuan/ui';
import { Send } from 'lucide-react';

const meta: Meta<typeof Button> = {
  title: 'UI / Button',
  component: Button,
  tags: ['autodocs'],
  args: { children: 'Click me' },
  argTypes: {
    variant: { control: 'select', options: ['default', 'outline', 'ghost', 'destructive', 'link'] },
    size: { control: 'select', options: ['default', 'sm', 'lg', 'icon'] },
  },
};
export default meta;
type Story = StoryObj<typeof Button>;

export const Default: Story = {};
export const Outline: Story = { args: { variant: 'outline' } };
export const Destructive: Story = { args: { variant: 'destructive' } };
export const WithIcon: Story = {
  args: {
    children: (
      <>
        <Send className="h-4 w-4" />
        发送
      </>
    ),
  },
};
export const IconOnly: Story = { args: { size: 'icon', children: <Send className="h-4 w-4" /> } };
