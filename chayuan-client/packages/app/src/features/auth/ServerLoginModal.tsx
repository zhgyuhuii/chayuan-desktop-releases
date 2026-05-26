/**
 * ServerLoginModal —— "瘦客户端登录" 弹框。
 *
 * 使用场景
 * --------
 * 服务端容器化部署时（``DEPLOY_MODE=server``），后端只暴露 API、不再发布 web
 * 资源；用户在本地装一个非常小的桌面壳（~15 MB），双击打开后第一件事是这
 * 个弹框：
 *
 *  1. 输入"服务地址"（http(s)://host:port，可为内网 IP）
 *  2. 输入"用户名" + "密码"
 *  3. "记住地址" / "记住凭据"
 *
 * 与 :class:`LoginModal` 的区别：
 *  * 多了"服务地址"输入；提交时先调 ``configureClient({baseURL})``，然后
 *    用同一个客户端打 ``/v1/auth/config`` 探活，再调 ``login``。
 *  * 锁屏：地址未填好之前 **不能** 关闭（``hideClose=true``）。
 *  * 不依赖 SettingsStore.apiBaseOverride 的初始化时机，因为登录之前 settings
 *    可能还没 hydrate。
 *
 * 校验
 * ----
 *  * URL：以 http:// / https:// 开头；自动去尾部斜杠。
 *  * username/password：长度 ≥ 1。
 */

import * as React from 'react';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  Button,
  Input,
  cn,
} from '@chayuan/ui';
import { isAuthPersistent, setAuthPersistent } from '@chayuan/platform-shared';
import { auth as authApi, configureClient, getClientConfig } from '@chayuan/api';
import { useAuthStore } from '../../store/auth';
import { useSettingsStore } from '../../store/settings';
import { useThinClientStore } from '../../store/thinClient';
import { Events, logEvent } from '@chayuan/observability';
import { useTranslation } from '../../i18n';
import { CHAYUAN_LOGO_URL } from '../../lib/brandAssets';
import { AssistantBrandLogo } from '../../components/AssistantBrandLogo';

const URL_RE = /^https?:\/\/[^\s]+$/i;

function normalizeServerUrl(raw: string): string | null {
  let s = raw.trim();
  if (!s) return null;
  if (!/^https?:\/\//i.test(s)) {
    // 默认补 http;真正部署时 IT 应该提供 https URL
    s = `http://${s}`;
  }
  s = s.replace(/\/+$/, '');
  return URL_RE.test(s) ? s : null;
}

export const ServerLoginModal: React.FC = () => {
  const { t } = useTranslation();
  const open = useThinClientStore((s) => s.requireServerLogin);
  const persistedServer = useSettingsStore((s) => s.apiBaseOverride);
  const setApiBaseOverride = useSettingsStore((s) => s.setApiBaseOverride);
  const finishServerLogin = useThinClientStore((s) => s.finishServerLogin);

  const login = useAuthStore((s) => s.login);

  const [server, setServer] = React.useState(persistedServer || '');
  const [username, setUsername] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [rememberServer, setRememberServer] = React.useState(true);
  const [rememberCreds, setRememberCreds] = React.useState<boolean>(() =>
    isAuthPersistent(),
  );
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!open) {
      setError(null);
      setBusy(false);
      // 不清空 server / username — 用户重开还想看到上次的值
    }
  }, [open]);

  const normalized = React.useMemo(() => normalizeServerUrl(server), [server]);
  const canSubmit = !!normalized && username.trim().length > 0 && password.length > 0 && !busy;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !normalized) return;
    setBusy(true);
    setError(null);
    setAuthPersistent(rememberCreds);
    // 1) 先把客户端 baseURL 切到目标服务器
    const previous = getClientConfig().baseURL;
    configureClient({ baseURL: normalized });
    try {
      // 2) 探活：能拉到 ``auth_config`` 才算 URL 是对的（404 / connection refused 都会抛）
      await authApi.config();
      // 3) 真正登录
      const user = await login(username.trim(), password);
      // 4) 成功 → 持久化 server URL（如果用户勾选了）
      if (rememberServer) {
        setApiBaseOverride(normalized);
      }
      logEvent(Events.AuthLogin, {
        metadata: { userId: user.id, remember: rememberCreds, server: normalized },
      });
      finishServerLogin();
    } catch (err: unknown) {
      // 失败时回滚 baseURL，避免后续业务请求打到坏地址
      configureClient({ baseURL: previous });
      const fallback = '无法连接到服务器，请检查地址 / 用户名 / 密码';
      setError(err instanceof Error ? err.message : fallback);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={() => { /* 始终锁定，不允许关闭 */ }}>
      <DialogContent
        hideClose
        className={cn(
          'max-w-[460px] gap-0 rounded-3xl border border-[var(--cy-border-subtle)]',
          'bg-[var(--cy-surface-base)] p-8 shadow-[var(--cy-shadow-lg)]',
        )}
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <div className="flex flex-col items-center text-center">
          <img src={CHAYUAN_LOGO_URL} alt="察元 AI" className="h-14 w-14 bg-transparent object-contain" />
          <span className="mt-3 text-sm font-semibold tracking-[0.18em] text-[var(--cy-brand-500)]">察元AI</span>
        </div>

        <DialogTitle className="mt-5 text-center text-2xl font-semibold text-[var(--cy-text-primary)]">
          {t('auth.serverLoginTitle', { defaultValue: '连接到察元 AI 服务器' })}
        </DialogTitle>
        <DialogDescription className="mt-1 text-center text-xs text-[var(--cy-text-tertiary)]">
          {t('auth.serverLoginHint', {
            defaultValue: '由您的管理员告诉您"服务地址"，再用您的账号登录。',
          })}
        </DialogDescription>

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <Input
            value={server}
            onChange={(e) => setServer(e.target.value)}
            placeholder="http(s)://your-chayuan-server.example:62581"
            autoFocus
            required
            className="h-12 rounded-xl border-0 bg-[var(--cy-surface-1)] px-4 font-mono text-sm focus-visible:ring-2"
            data-testid="server-url-input"
          />
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder={t('auth.usernamePlaceholder')}
            required
            className="h-12 rounded-xl border-0 bg-[var(--cy-surface-1)] px-4 focus-visible:ring-2"
            data-testid="server-username-input"
          />
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t('auth.passwordPlaceholder')}
            required
            className="h-12 rounded-xl border-0 bg-[var(--cy-surface-1)] px-4 focus-visible:ring-2"
            data-testid="server-password-input"
          />

          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 pt-1 text-xs text-[var(--cy-text-secondary)]">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={rememberServer}
                onChange={(e) => setRememberServer(e.target.checked)}
                className="h-3.5 w-3.5 accent-[var(--cy-brand-500)]"
              />
              <span>{t('auth.rememberServer', { defaultValue: '记住地址' })}</span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={rememberCreds}
                onChange={(e) => setRememberCreds(e.target.checked)}
                className="h-3.5 w-3.5 accent-[var(--cy-brand-500)]"
              />
              <span>{t('auth.rememberMe')}</span>
            </label>
          </div>

          {error && (
            <p
              className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600"
              data-testid="server-login-error"
            >
              {error}
            </p>
          )}

          <Button
            type="submit"
            disabled={!canSubmit}
            data-testid="server-login-submit"
            className={cn(
              'h-11 w-full rounded-xl px-8 text-sm font-medium',
              'bg-[var(--cy-brand-500)] text-white hover:bg-[var(--cy-brand-600)]',
              'disabled:cursor-not-allowed disabled:bg-[var(--cy-border-default)] disabled:text-[var(--cy-text-tertiary)]',
            )}
          >
            {busy ? (
              <>
                <AssistantBrandLogo running className="mr-1.5 h-4 w-4 rounded-full" />
                {t('common.loading')}
              </>
            ) : (
              t('auth.connectAndLogin', { defaultValue: '连接并登录' })
            )}
          </Button>

          <p className="pt-1 text-center text-[10px] leading-relaxed text-[var(--cy-text-tertiary)]">
            {t('auth.serverLoginFooter', {
              defaultValue: '应用不会把您的密码上传到任何"非该地址"的服务器。',
            })}
          </p>
        </form>
      </DialogContent>
    </Dialog>
  );
};
