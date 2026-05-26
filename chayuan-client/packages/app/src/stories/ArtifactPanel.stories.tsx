import type { Meta, StoryObj } from '@storybook/react';
import * as React from 'react';
import { ArtifactPanel } from '../features/artifact/ArtifactPanel';
import { useArtifactStore } from '../store/artifact';

const meta: Meta<typeof ArtifactPanel> = {
  title: 'Artifact / Panel',
  component: ArtifactPanel,
  tags: ['autodocs'],
  parameters: { layout: 'fullscreen' },
};
export default meta;
type Story = StoryObj<typeof ArtifactPanel>;

const Wrap: React.FC<{ items: Parameters<ReturnType<typeof useArtifactStore.getState>['upsert']>[0][] }> = ({ items }) => {
  React.useEffect(() => {
    const { upsert, setOpen } = useArtifactStore.getState();
    items.forEach(upsert);
    setOpen(true);
  }, [items]);
  return (
    <div className="flex h-screen w-screen">
      <div className="flex-1 bg-muted/30 p-6 text-sm text-muted-foreground">主区域占位</div>
      <ArtifactPanel />
    </div>
  );
};

export const TypeScriptCode: Story = {
  render: () => (
    <Wrap
      items={[
        {
          id: 'a1',
          key: 'a1',
          title: 'fibonacci.ts',
          kind: 'code',
          language: 'typescript',
          content: 'export function fib(n: number): number {\n  if (n < 2) return n;\n  return fib(n - 1) + fib(n - 2);\n}',
          createdAt: Date.now(),
          updatedAt: Date.now(),
        },
      ]}
    />
  ),
};

export const MermaidDiagram: Story = {
  render: () => (
    <Wrap
      items={[
        {
          id: 'a2',
          key: 'a2',
          title: '工作流',
          kind: 'mermaid',
          content: 'graph TD\nA[用户] --> B[Composer]\nB --> C[Transport]\nC --> D[ChatGraph]\nD --> E[Token]\nE --> A',
          createdAt: Date.now(),
          updatedAt: Date.now(),
        },
      ]}
    />
  ),
};

export const JsonPretty: Story = {
  render: () => (
    <Wrap
      items={[
        {
          id: 'a3',
          key: 'a3',
          title: 'response.json',
          kind: 'json',
          content: '{"hits":[{"id":1,"name":"a"},{"id":2,"name":"b"}]}',
          createdAt: Date.now(),
          updatedAt: Date.now(),
        },
      ]}
    />
  ),
};
