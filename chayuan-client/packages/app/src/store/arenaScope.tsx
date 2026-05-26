/**
 * ArenaScope:把当前 tab.id 注入到 modelArena 消费链路。
 *
 * 由 KeepAliveOutlet 在每个 tab 子树包一层 Provider,scope 即 tab.id;
 * 消费方(ChatPage / MultiLaneShell / Lane / useLaneDnD)通过 `useArenaScope()`
 * 拿到 scope,再调 useModelArenaStore 的 scoped 接口。
 *
 * 不在 Provider 里使用 useArenaScope 会抛错 — 早 fail,避免静默走全局共享。
 */
import * as React from 'react';

const ArenaScopeContext = React.createContext<string | null>(null);

export interface ArenaScopeProviderProps {
  scope: string;
  children: React.ReactNode;
}

export const ArenaScopeProvider: React.FC<ArenaScopeProviderProps> = ({ scope, children }) => (
  <ArenaScopeContext.Provider value={scope}>{children}</ArenaScopeContext.Provider>
);

export function useArenaScope(): string {
  const scope = React.useContext(ArenaScopeContext);
  if (!scope) {
    throw new Error('useArenaScope must be used inside <ArenaScopeProvider>');
  }
  return scope;
}

/** 非 React 路径(如 BroadcastChannel 回调)拿不到 context 时,允许传 null */
export function useOptionalArenaScope(): string | null {
  return React.useContext(ArenaScopeContext);
}
