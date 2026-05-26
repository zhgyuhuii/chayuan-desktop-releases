/**
 * Storybook 故事：``ServerLoginModal`` 各种形态。
 *
 * 我们把 modal 的 ``open`` / 错误状态都放在 zustand store 里，所以这些
 * 故事不需要 mock 网络——只需要在组件挂载前把 store 调好。
 */
import * as React from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { ServerLoginModal } from '../features/auth/ServerLoginModal';
import { useThinClientStore } from '../store/thinClient';
import { useSettingsStore } from '../store/settings';

const Provider: React.FC<{ open: boolean; children: React.ReactNode }> = ({
  open,
  children,
}) => {
  React.useEffect(() => {
    useThinClientStore.setState({ enabled: true, requireServerLogin: open });
  }, [open]);
  return <>{children}</>;
};

const meta: Meta<typeof ServerLoginModal> = {
  title: 'Auth / ServerLoginModal',
  component: ServerLoginModal,
  parameters: {
    layout: 'fullscreen',
    docs: {
      description: {
        component:
          '瘦桌面客户端首次启动时弹出，由用户输入服务地址 + 凭据。Modal 强制锁定，无 close X / 不可点遮罩关闭。',
      },
    },
  },
};
export default meta;
type Story = StoryObj<typeof ServerLoginModal>;

export const Default: Story = {
  render: () => (
    <Provider open>
      <ServerLoginModal />
    </Provider>
  ),
};

export const PrefilledServer: Story = {
  render: () => {
    React.useEffect(() => {
      useSettingsStore.getState().setApiBaseOverride('http://chayuan.example.local:62581');
      return () => {
        useSettingsStore.getState().setApiBaseOverride('');
      };
    }, []);
    return (
      <Provider open>
        <ServerLoginModal />
      </Provider>
    );
  },
};

export const Hidden: Story = {
  render: () => (
    <Provider open={false}>
      <div className="p-8 text-sm text-zinc-500">
        ServerLoginModal 仅在 thinClient 模式 + 未登录时挂载；这里把 store
        关掉模拟"已登录后主路由接管"的态。
      </div>
      <ServerLoginModal />
    </Provider>
  ),
};
