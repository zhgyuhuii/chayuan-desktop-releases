import type * as React from 'react';
import type { RendererProps } from '../types';

export const VideoRenderer: React.FC<RendererProps> = ({ url, onReady, onError }) => {
  return (
    <div className="flex h-full w-full items-center justify-center bg-black">
      {/* biome-ignore lint/a11y/useMediaCaption: 用户上传视频通常没字幕轨;后续接 KB 字幕能力时补 */}
      <video
        src={url}
        controls
        className="max-h-full max-w-full"
        preload="metadata"
        onLoadedMetadata={() => onReady?.()}
        onError={() => onError?.(new Error('视频加载失败'))}
      />
    </div>
  );
};
