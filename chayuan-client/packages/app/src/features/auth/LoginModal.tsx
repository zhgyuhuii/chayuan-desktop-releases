/**
 * LoginModal —— 登录 / 注册二态弹框。
 *
 * - mode='login':账号 + 密码 + 记住我 + 提交;有"现在注册"链接(allowRegistration=true 时)
 * - mode='register':账号 + 密码 + 邮箱(可选) + 同意条款 + 提交;成功后自动登录
 *
 * 强制登录(`locked`):
 *   - 隐藏关闭 X / Esc / 点遮罩
 *   - 隐藏「以游客身份继续」
 *   - 注册流程仍可用(allowRegistration=true 时)
 *
 * 输入契约:JWT username/password。注册成功 → 调 login(同凭据)立刻拿 token,
 * 用户感知是"一步注册即登录"。
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
import { auth as authApi } from '@chayuan/api';
import { useAuthStore } from '../../store/auth';
import { useLoginModalStore } from '../../store/loginModal';
import { Events, logEvent } from '@chayuan/observability';
import { useTranslation } from '../../i18n';
import { CHAYUAN_LOGO_URL } from '../../lib/brandAssets';
import { notifySuccess } from '../../store/errorDialog';
import { AssistantBrandLogo } from '../../components/AssistantBrandLogo';

type Mode = 'login' | 'register';

export const LoginModal: React.FC = () => {
  const { t } = useTranslation();
  const open = useLoginModalStore((s) => s.open);
  const locked = useLoginModalStore((s) => s.locked);
  const allowRegistration = useLoginModalStore((s) => s.allowRegistration);
  const hide = useLoginModalStore((s) => s.hide);
  const setLocked = useLoginModalStore((s) => s.setLocked);
  const login = useAuthStore((s) => s.login);
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn);

  const [mode, setMode] = React.useState<Mode>('login');
  const [username, setUsername] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [email, setEmail] = React.useState('');
  const [remember, setRemember] = React.useState<boolean>(() => isAuthPersistent());
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  // 关闭时重置(remember 不重置 — 持久偏好)
  React.useEffect(() => {
    if (!open) {
      setUsername('');
      setPassword('');
      setEmail('');
      setError(null);
      setBusy(false);
      setMode('login');
    }
  }, [open]);

  // 登录后自动关
  React.useEffect(() => {
    if (open && isLoggedIn) {
      setLocked(false);
      hide();
    }
  }, [open, isLoggedIn, hide, setLocked]);

  // allowRegistration 关掉时,如果 mode 是 register,强制回 login
  React.useEffect(() => {
    if (!allowRegistration && mode === 'register') setMode('login');
  }, [allowRegistration, mode]);

  const canSubmit =
    username.trim().length > 0 &&
    password.length >= (mode === 'register' ? 6 : 1) &&
    !busy;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    setAuthPersistent(remember);
    try {
      if (mode === 'register') {
        await authApi.register({
          username: username.trim(),
          password,
          ...(email.trim() ? { email: email.trim() } : {}),
        });
        notifySuccess(t('auth.registerSuccess'));
        // 注册成功 → 立刻用同凭据登录;后端的 login 返回 token + user
        const user = await login(username.trim(), password);
        logEvent(Events.AuthLogin, {
          metadata: { userId: user.id, remember, registered: true },
        });
      } else {
        const user = await login(username.trim(), password);
        logEvent(Events.AuthLogin, { metadata: { userId: user.id, remember } });
      }
      hide();
    } catch (err: unknown) {
      const fallback = mode === 'register' ? t('auth.registerFailed') : t('auth.loginFailed');
      setError(err instanceof Error ? err.message : fallback);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          if (locked) return;
          hide();
        }
      }}
    >
      <DialogContent
        hideClose={locked}
        className={cn(
          'max-w-[440px] gap-0 rounded-3xl border border-[var(--cy-border-subtle)]',
          'bg-[var(--cy-surface-base)] p-8 shadow-[var(--cy-shadow-lg)]',
        )}
        onEscapeKeyDown={(e) => locked && e.preventDefault()}
        onPointerDownOutside={(e) => locked && e.preventDefault()}
        onInteractOutside={(e) => locked && e.preventDefault()}
      >
        {/* 顶部品牌行 */}
        <div className="flex flex-col items-center text-center">
          <img
            src={CHAYUAN_LOGO_URL}
            alt="察元 AI"
            className="h-14 w-14 bg-transparent object-contain"
          />
          <span className="mt-3 text-sm font-semibold tracking-[0.18em] text-[var(--cy-brand-500)]">
            察元AI
          </span>
        </div>

        <DialogTitle className="mt-5 text-center text-2xl font-semibold text-[var(--cy-text-primary)]">
          {mode === 'register' ? t('auth.registerTitle') : t('auth.modalTitle')}
        </DialogTitle>
        <DialogDescription className="sr-only">
          {t('auth.loginHint')}
        </DialogDescription>

        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <Input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder={t('auth.usernamePlaceholder')}
            autoFocus
            required
            className="h-12 rounded-xl border-0 bg-[var(--cy-surface-1)] px-4 focus-visible:ring-2"
          />
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={
              mode === 'register'
                ? `${t('auth.passwordPlaceholder')} (${t('auth.passwordHint')})`
                : t('auth.passwordPlaceholder')
            }
            required
            minLength={mode === 'register' ? 6 : undefined}
            className="h-12 rounded-xl border-0 bg-[var(--cy-surface-1)] px-4 focus-visible:ring-2"
          />
          {mode === 'register' && (
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={t('auth.emailPlaceholder')}
              className="h-12 rounded-xl border-0 bg-[var(--cy-surface-1)] px-4 focus-visible:ring-2"
            />
          )}

          <label className="flex items-center gap-2 pt-1 text-xs text-[var(--cy-text-secondary)]">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              className="h-3.5 w-3.5 accent-[var(--cy-brand-500)]"
            />
            <span>{t('auth.rememberMe')}</span>
          </label>

          {error && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">
              {error}
            </p>
          )}

          <div className="flex flex-col-reverse gap-3 pt-2 sm:flex-row sm:items-center sm:justify-between">
            {/* 注册/登录互切链接;allowRegistration=false 时整段隐藏 */}
            {allowRegistration ? (
              <span className="text-xs text-[var(--cy-text-tertiary)]">
                {mode === 'register' ? t('auth.haveAccount') : t('auth.noAccount')}{' '}
                <button
                  type="button"
                  className="text-[var(--cy-brand-500)] hover:underline"
                  onClick={() => {
                    setError(null);
                    setMode((m) => (m === 'register' ? 'login' : 'register'));
                  }}
                >
                  {mode === 'register' ? t('auth.backToLogin') : t('auth.registerNow')}
                </button>
              </span>
            ) : (
              <span />
            )}
            <Button
              type="submit"
              disabled={!canSubmit}
              className={cn(
                'h-11 w-full rounded-xl px-8 text-sm font-medium sm:w-auto sm:min-w-[128px]',
                'bg-[var(--cy-brand-500)] text-white hover:bg-[var(--cy-brand-600)]',
                'disabled:cursor-not-allowed disabled:bg-[var(--cy-border-default)] disabled:text-[var(--cy-text-tertiary)]',
              )}
            >
              {busy ? (
                <>
                  <AssistantBrandLogo running className="mr-1.5 h-4 w-4 rounded-full" />
                  {t('common.loading')}
                </>
              ) : mode === 'register'
                ? t('auth.register')
                : t('auth.login')}
            </Button>
          </div>

          {!locked && mode === 'login' && (
            <Button
              type="button"
              variant="ghost"
              className="mt-2 w-full text-xs text-[var(--cy-text-tertiary)]"
              onClick={hide}
            >
              {t('auth.continueAsGuest')}
            </Button>
          )}
        </form>
      </DialogContent>
    </Dialog>
  );
};
