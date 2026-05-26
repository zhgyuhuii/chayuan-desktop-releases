/**
 * WindowDock —— 参考图菜单中的"窗口位置(左/中/右)"。
 *
 * 仅 Tauri 桌面端有效;Web 端调用方应隐藏。
 */

import * as React from 'react';
import { PanelLeft, PanelRight, Square } from 'lucide-react';
import { Pill } from '@chayuan/ui';
import { getPlatform, type WindowDockPosition } from '@chayuan/platform-shared';
import { useSettingsStore } from '../../store/settings';

const OPTIONS: ReadonlyArray<{
  value: WindowDockPosition;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
}> = [
  { value: 'left', icon: PanelLeft, label: '靠左' },
  { value: 'center', icon: Square, label: '居中' },
  { value: 'right', icon: PanelRight, label: '靠右' },
];

export const WindowDock: React.FC = () => {
  const dock = useSettingsStore((s) => s.windowDock);
  const setDock = useSettingsStore((s) => s.setWindowDock);

  const apply = async (pos: WindowDockPosition) => {
    setDock(pos);
    try {
      const win = getPlatform().window;
      await win.setDock?.(pos);
    } catch (e) {
      console.warn('[WindowDock] setDock failed', e);
    }
  };

  return (
    <div className="flex gap-1">
      {OPTIONS.map((o) => {
        const Icon = o.icon;
        const active = dock === o.value;
        return (
          <Pill
            key={o.value}
            size="sm"
            tone={active ? 'ink' : 'ghost'}
            active={active}
            onClick={() => void apply(o.value)}
            aria-label={o.label}
          >
            <Icon className="h-3.5 w-3.5" />
          </Pill>
        );
      })}
    </div>
  );
};
