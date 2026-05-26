import type { Meta, StoryObj } from '@storybook/react';
import { Composer } from '../features/chat/Composer';
import { AttachmentBar } from '../features/chat/AttachmentBar';

const meta: Meta<typeof Composer> = {
  title: 'Chat / Composer',
  component: Composer,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
};
export default meta;
type Story = StoryObj<typeof Composer>;

export const Idle: Story = {
  args: {
    isStreaming: false,
    onSend: (s) => console.log('send', s),
  },
};

export const Streaming: Story = {
  args: { isStreaming: true, onStop: () => console.log('stop') },
};

export const WithAttachments: Story = {
  args: {
    isStreaming: false,
    onSend: () => undefined,
    topSlot: (
      <AttachmentBar
        items={[
          {
            id: '1',
            name: 'report.pdf',
            size: 32_000,
            mime: 'application/pdf',
            type: 'file',
            status: 'ready',
          },
          {
            id: '2',
            name: 'chart.png',
            size: 12_000,
            mime: 'image/png',
            type: 'image',
            previewUrl:
              'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="%236366f1"/></svg>',
            status: 'uploading',
          },
        ]}
        onRemove={() => undefined}
      />
    ),
  },
};
