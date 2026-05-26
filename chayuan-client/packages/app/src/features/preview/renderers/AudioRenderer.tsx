import { Music } from 'lucide-react';
import type * as React from 'react';
import type { RendererProps } from '../types';

export const AudioRenderer: React.FC<RendererProps> = ({ url, file, onReady, onError }) => {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 bg-gradient-to-br from-fuchsia-50 to-violet-50 px-8">
      <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-gradient-to-br from-fuchsia-500 to-violet-600 text-white shadow-xl">
        <Music className="h-10 w-10" />
      </div>
      <p className="max-w-md truncate text-sm font-medium text-[var(--cy-text-primary)]">
        {file.fileName}
      </p>
      <audio
        src={url}
        controls
        className="w-full max-w-lg"
        preload="metadata"
        onLoadedMetadata={() => onReady?.()}
        onError={() => onError?.(new Error('音频加载失败'))}
      />
    </div>
  );
};
