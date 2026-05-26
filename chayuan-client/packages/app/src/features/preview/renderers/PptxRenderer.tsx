/**
 * .pptx 预览:目前没有特别可靠的纯前端方案。
 *
 * 策略:
 *   1) 优先尝试后端 preview_doc(若后端配置了 LibreOffice/Aspose 转 PDF,iframe 直接吃)
 *   2) iframe 加载失败 → 退到 FallbackRenderer(下载/外部打开)
 *
 * 检测"后端是否能预览":监听 iframe load/error;Content-Type 不是 pdf 时也算失败。
 * 这里采用宽松策略:先 iframe 试,5 秒内没 onLoad 视为失败。
 */

import * as React from 'react';
import type { RendererProps } from '../types';
import { FallbackRenderer } from './FallbackRenderer';

export const PptxRenderer: React.FC<RendererProps> = (props) => {
  const [failed, setFailed] = React.useState(false);
  const [loaded, setLoaded] = React.useState(false);
  const timerRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    timerRef.current = window.setTimeout(() => {
      if (!loaded) setFailed(true);
    }, 8000);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [loaded]);

  if (failed) return <FallbackRenderer {...props} />;

  return (
    <div className="relative h-full w-full">
      <iframe
        title={props.file.fileName}
        src={props.url}
        className="h-full w-full border-0 bg-[var(--cy-surface-base)]"
        onLoad={() => {
          setLoaded(true);
          props.onReady?.();
        }}
        onError={() => setFailed(true)}
      />
      {!loaded && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-[var(--cy-text-tertiary)]">
          正在拼图… 演示文稿较大,请稍候
        </div>
      )}
    </div>
  );
};
