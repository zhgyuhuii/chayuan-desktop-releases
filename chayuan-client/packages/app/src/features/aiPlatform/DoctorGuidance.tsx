/**
 * DoctorGuidance —— 把 ``/v1/admin/doctor`` 返回的 fatal/warn 检查项映射成
 * 三段式 ``GuidanceCard``,让用户看到 What/Why/How 而不是一行红字。
 *
 * 设计思路:
 *   * doctor 返回 ``checks: [{name, status, message, hint?}, ...]``;name 是
 *     ``mac.gatekeeper`` / ``windows.av.defender`` / ``ports.postgres`` 之类的
 *     稳定 ID;以前端为主导写一张映射表,按 name 转引导文案。
 *   * 未命中映射的 warn/fatal 兜底为"通用 GuidanceCard",至少把 message + hint
 *     展示出来,不淹没问题。
 *   * 不内置网络请求 / 不内置 toast;调用方负责。
 */

import * as React from 'react';
import {
  GuidanceCard,
  type GuidanceCardProps,
  type GuidancePlatformBlock,
  type GuidanceTone,
} from '@chayuan/ui';

export interface DoctorCheckLike {
  name: string;
  /**
   * 兼容多种 status 取值:
   *   - chayuan_preflight ``severity``: "ok" / "warn" / "fatal"
   *   - 客户端 DoctorReport ``status``: "ok" / "warn" / "fail" / "error"
   */
  status?: string;
  severity?: string;
  message?: string;
  detail?: string;
  hint?: string;
  fix?: string;
}

const STATUS_TO_TONE: Record<string, GuidanceTone> = {
  fatal: 'danger',
  fail: 'danger',
  error: 'danger',
  warn: 'warning',
  warning: 'warning',
  ok: 'success',
};

function checkTone(c: DoctorCheckLike): GuidanceTone {
  const raw = (c.severity ?? c.status ?? 'warn').toLowerCase();
  return STATUS_TO_TONE[raw] ?? 'warning';
}

function checkMessage(c: DoctorCheckLike): string {
  return c.message ?? c.detail ?? c.name;
}

function checkHint(c: DoctorCheckLike): string | undefined {
  return c.hint ?? c.fix ?? undefined;
}

/**
 * 把 check.name 映射成结构化引导。如果命中预设,就用预设;没命中就用兜底。
 *
 * 之所以走 lookup-by-name 而不是 backend-driven 文案:
 *   * 文案受 UI 设计 / 国际化 / 链接更新约束,前端迭代更快
 *   * 后端只负责"判断是否有问题",前端负责"教用户怎么解"
 *   * 真要后端驱动文案,可以让 ``c.fix`` 是结构化 markdown,本组件渲染它
 */
const GUIDANCE_PRESETS: Record<string, (c: DoctorCheckLike) => Partial<GuidanceCardProps>> = {
  // ============ macOS ============
  'mac.gatekeeper': () => ({
    what: 'macOS Gatekeeper 拦截了察元安装包',
    why: '安装包未做 Apple 公证 (notarization),首次启动会被默认安全策略拦截。',
    how: [
      {
        platform: 'macos',
        steps: [
          { text: '在 Finder 中找到 chayuan.app,按住 Control 键单击 → 在右键菜单选择"打开"。系统会弹一次确认,选"打开"即可。' },
          {
            text: '或在终端解除隔离属性 (一次性):',
            command: 'xattr -dr com.apple.quarantine /Applications/chayuan.app',
          },
          {
            text: '或在 系统设置 → 隐私与安全性 → 允许任何来源 临时放行。',
          },
        ],
      },
    ],
  }),
  'mac.tcc': () => ({
    what: 'macOS 隐私权限可能未授权',
    why: '语音识别 / 屏幕录制等能力首次使用时会弹窗;若误点拒绝,需到系统设置手动恢复。',
    how: [
      {
        platform: 'macos',
        steps: [
          { text: '系统设置 → 隐私与安全性 → 麦克风 / 屏幕录制 → 找到 chayuan 并打开开关。' },
          { text: '修改后重新启动察元让权限生效。' },
        ],
      },
    ],
  }),

  // ============ Windows ============
  'windows.av.defender': () => ({
    what: 'Windows Defender / SmartScreen 阻止了启动',
    why: '安装包尚未做代码签名 (code signing),Windows SmartScreen 默认会拦截未知发布者的应用。',
    how: [
      {
        platform: 'windows',
        steps: [
          { text: '出现"Windows 已保护你的电脑"提示时,点击"更多信息" → "仍要运行"。' },
          {
            text: '永久白名单 (PowerShell 管理员):',
            command: 'Add-MpPreference -ExclusionPath "C:\\Program Files\\chayuan"',
          },
          { text: '或在 Windows 安全中心 → 病毒和威胁防护 → 排除项 中手动添加察元安装目录。' },
        ],
      },
    ],
  }),
  'windows.smartscreen': () => ({
    what: 'SmartScreen 警告',
    why: '未识别的应用首次启动时,SmartScreen 会要求用户确认。',
    how: [
      {
        platform: 'windows',
        steps: [
          { text: '点击 "更多信息" → "仍要运行" 一次即可。后续启动不再提示。' },
        ],
      },
    ],
  }),

  // ============ Linux ============
  'linux.selinux': () => ({
    what: 'SELinux 可能限制察元访问数据目录',
    why: 'SELinux Enforcing 模式下,二进制对 /opt /var/lib 之外的目录可能没读写权限。',
    how: [
      {
        platform: 'linux',
        steps: [
          { text: '检查当前模式:', command: 'getenforce' },
          { text: '若返回 Enforcing,临时切换为 Permissive (重启失效):', command: 'sudo setenforce 0' },
          {
            text: '或为察元数据目录添加正确的 context:',
            command: 'sudo chcon -R -t var_lib_t /var/lib/chayuan',
          },
        ],
      },
    ],
  }),
  'linux.apparmor': () => ({
    what: 'AppArmor 可能阻止察元子进程',
    why: 'AppArmor profile 默认对 docker / systemd 启动的进程有限制,可能导致 vendor 服务起不来。',
    how: [
      {
        platform: 'linux',
        steps: [
          { text: '查看 chayuan 相关 profile 状态:', command: 'sudo aa-status | grep -i chayuan' },
          { text: '临时禁用某 profile:', command: 'sudo aa-disable /etc/apparmor.d/<profile>' },
        ],
      },
    ],
  }),

  // ============ 通用 / 端口 ============
  'ports.conflict': (c) => ({
    what: '端口被占用,察元已自动切换到备用端口',
    why: '默认端口被其它进程占用;PortAllocator 已自动 bump 到空闲端口。',
    how: [
      {
        platform: 'all',
        steps: [
          { text: c.message ?? '查看 ~/.chayuan/runtime.json 确认实际生效端口。' },
          {
            text: 'macOS / Linux 查找占用进程:',
            command: 'lsof -i :5432',
          },
          {
            text: 'Windows 查找占用进程:',
            command: 'netstat -ano | findstr 5432',
          },
        ],
      },
    ],
  }),
};

function buildGuidance(c: DoctorCheckLike): GuidanceCardProps {
  const tone = checkTone(c);
  const preset = GUIDANCE_PRESETS[c.name];
  if (preset) {
    const partial = preset(c);
    return {
      tone,
      what: partial.what ?? c.name,
      why: partial.why ?? checkMessage(c),
      how: partial.how ?? [{ platform: 'all', steps: [{ text: checkMessage(c) }] }],
    };
  }
  // 兜底:把 name 当标题、message 当 why、hint 当 step
  const fallback: GuidancePlatformBlock = {
    platform: 'all',
    steps: [
      { text: checkMessage(c) },
      ...(checkHint(c) ? [{ text: checkHint(c) as string }] : []),
    ],
  };
  return {
    tone,
    what: c.name,
    why: checkMessage(c),
    how: [fallback],
  };
}

export interface DoctorGuidanceListProps {
  checks: DoctorCheckLike[];
  /** 是否包含 ok 项 (默认 false,只显示 warn / fatal) */
  includeOk?: boolean;
  className?: string;
  emptyHint?: React.ReactNode;
  /** 复制命令时回调,可用于触发外部 toast */
  onCopy?: (command: string) => void;
}

export const DoctorGuidanceList: React.FC<DoctorGuidanceListProps> = ({
  checks,
  includeOk = false,
  className,
  emptyHint,
  onCopy,
}) => {
  const filtered = React.useMemo(
    () =>
      checks.filter((c) => {
        const t = checkTone(c);
        return includeOk ? true : t !== 'success';
      }),
    [checks, includeOk],
  );

  if (filtered.length === 0) {
    return emptyHint ? <div className={className}>{emptyHint}</div> : null;
  }

  return (
    <div className={className}>
      {filtered.map((c, i) => {
        const props = buildGuidance(c);
        return <GuidanceCard key={`${c.name}-${i}`} {...props} onCopy={onCopy} />;
      })}
    </div>
  );
};
