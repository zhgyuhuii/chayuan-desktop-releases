/**
 * Storybook: GuidanceCard 多 tone × 多平台用例。
 *
 * 涵盖:
 *   - 4 种 tone (warning / info / success / danger)
 *   - 多平台 Tab (macos / windows / linux / all)
 *   - 复制按钮 / actionLabel / docHref / 单平台简化态
 *
 * 这套故事进 chromatic 后,任何 GuidanceCard 视觉漂移会被自动捕获。
 */
import type { Meta, StoryObj } from '@storybook/react';
import { GuidanceCard } from '@chayuan/ui';

const meta: Meta<typeof GuidanceCard> = {
  title: 'aiPlatform/GuidanceCard',
  component: GuidanceCard,
  parameters: { layout: 'centered' },
  argTypes: {
    tone: {
      control: { type: 'select' },
      options: ['warning', 'info', 'success', 'danger'],
    },
  },
};

export default meta;
type Story = StoryObj<typeof GuidanceCard>;

// ---- Gatekeeper 拦截 (warning) ----
export const GatekeeperBlocked: Story = {
  args: {
    tone: 'warning',
    what: 'macOS Gatekeeper 拦截了察元安装包',
    why: '安装包未做 Apple 公证 (notarization),首次启动会被默认安全策略拦截。',
    how: [
      {
        platform: 'macos',
        steps: [
          { text: '在 Finder 中找到 chayuan.app, 按住 Control 键单击 → "打开"。' },
          {
            text: '或在终端解除隔离属性:',
            command: 'xattr -dr com.apple.quarantine /Applications/chayuan.app',
          },
        ],
      },
    ],
  },
};

// ---- 跨平台多 Tab (info) ----
export const MultiPlatform: Story = {
  args: {
    tone: 'info',
    what: '端口被占用,察元已自动切换到备用端口',
    why: '默认 5432 被其他进程占用; PortAllocator 已自动 bump 到 35432。',
    how: [
      {
        platform: 'macos',
        steps: [
          { text: '查找占用进程:', command: 'lsof -i :5432' },
        ],
      },
      {
        platform: 'linux',
        steps: [
          { text: '查找占用进程:', command: 'sudo lsof -i :5432' },
          { text: '或:', command: 'sudo ss -lptn "sport = :5432"' },
        ],
      },
      {
        platform: 'windows',
        steps: [
          { text: '查找占用进程:', command: 'netstat -ano | findstr 5432' },
        ],
      },
    ],
  },
};

// ---- success: 一切就绪 ----
export const ReadyState: Story = {
  args: {
    tone: 'success',
    what: '所有依赖服务都健康',
    why: '7 项检查全部通过, 察元可以正常工作。',
    how: [],
  },
};

// ---- danger: 致命错误 ----
export const FatalError: Story = {
  args: {
    tone: 'danger',
    what: 'Postgres 连接失败',
    why: '后端无法访问 ${POSTGRES_HOST}; 业务路由全部 500。',
    how: [
      {
        platform: 'all',
        steps: [
          { text: '检查 runtime.json 中的实际 host/port' },
          { text: '尝试连接:', command: 'pg_isready -h $POSTGRES_HOST -p 35432' },
          {
            text: '若是 docker 启的,重启:',
            command: 'docker compose -f docker/dev-stack-alt-ports/docker-compose.yml restart postgres',
          },
        ],
        docHref: 'https://chayuan.dev/docs/troubleshooting/postgres',
      },
    ],
  },
};

// ---- 带主操作按钮 (warning + actionLabel) ----
export const WithAction: Story = {
  args: {
    tone: 'warning',
    what: 'kkFileView 文件预览未启用',
    why: '本地未配置 kkFileView 服务地址;部分非 office 文档将走前端兜底 renderer。',
    actionLabel: '前往设置',
    onAction: () => {
      // eslint-disable-next-line no-alert
      alert('触发跳转 (Story 演示)');
    },
    how: [
      {
        platform: 'all',
        steps: [
          { text: '前往设置页填入 kkFileView 服务地址(KKFILEVIEW_URL)。' },
          { text: '保存后客户端预览会自动接管 100+ 文件格式,无需重启察元。' },
        ],
      },
    ],
  },
};

// ---- 单平台简化 (无 Tab) ----
export const SinglePlatform: Story = {
  args: {
    tone: 'info',
    what: 'Windows arm64 暂时回退 ZIP',
    why: 'MSIX 工具链 / 代码签名证书在当前构建环境不可用。',
    how: [
      {
        platform: 'windows',
        steps: [
          { text: '解压 zip 到任意目录' },
          { text: '右键 chayuan.exe → 属性 → 解除阻止' },
          { text: '双击启动' },
        ],
      },
    ],
  },
};
