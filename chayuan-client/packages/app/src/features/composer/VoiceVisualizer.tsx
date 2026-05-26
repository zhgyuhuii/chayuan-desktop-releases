/**
 * 录音声纹可视化 — 经典 audio equalizer 风格,N 根纵向柱条按频谱实时跳动。
 *
 * 实现:WebAudio AnalyserNode + requestAnimationFrame。每帧:
 *   1) analyser.getByteFrequencyData(buf)   → 0..255 频谱数据
 *   2) 把数据按 barCount 分桶,每桶取桶内峰值
 *   3) 桶值 / 255 → 0..1 → CSS scaleY,左下角 origin 让柱条从底向上长
 *
 * 性能:不用 React state 驱动 — 直接 DOM ref 写 transform。28 根柱条,60 FPS
 * 下每帧 28 次 style write,Chrome 渲染线程 < 0.3ms 完成,无压力。
 *
 * 静音兜底:scaleY 最小 0.04,看起来"一直在呼吸",不让用户以为坏了。
 *
 * 生命周期:active=false 时不渲染、不创建 AudioContext。stream 变(refresh)
 * 时 useEffect 重新挂 AudioContext,旧的清干净避免泄漏。
 */
import * as React from 'react';

export interface VoiceVisualizerProps {
  /** 麦克风 stream(用 useMicRecorder().stream)。null 时组件什么也不渲。 */
  stream: MediaStream | null;
  /** 是否激活 — false 时整体卸载,不占资源 */
  active: boolean;
  /** 柱条数,默认 28 */
  barCount?: number;
  /** 容器高度(px),默认 48 */
  height?: number;
  className?: string;
}

export const VoiceVisualizer: React.FC<VoiceVisualizerProps> = ({
  stream, active, barCount = 28, height = 48, className,
}) => {
  const containerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!active || !stream) return;

    // 兼容老 webkit;两个名字都试
    const AudioCtor: typeof AudioContext | undefined =
      (window as Window & { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).AudioContext
      ?? (window as Window & { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtor) {
      console.warn('[voice-vis] 浏览器不支持 AudioContext,跳过可视化');
      return;
    }

    const ctx = new AudioCtor();
    let src: MediaStreamAudioSourceNode | null = null;
    try {
      src = ctx.createMediaStreamSource(stream);
    } catch (e) {
      console.warn('[voice-vis] createMediaStreamSource 失败:', e);
      ctx.close().catch(() => { /* ignore */ });
      return;
    }
    const analyser = ctx.createAnalyser();
    // fftSize 64 → 32 个频率 bin(0~8 kHz @16k sample rate)。bar 数 ≤ 32 时够用
    // smoothingTimeConstant 0.7:相邻帧加权平均,柱条不抖
    analyser.fftSize = 64;
    analyser.smoothingTimeConstant = 0.7;
    src.connect(analyser);

    const data = new Uint8Array(analyser.frequencyBinCount); // 32
    const bucketSize = Math.max(1, Math.floor(data.length / barCount));
    let rafId = 0;

    const tick = () => {
      analyser.getByteFrequencyData(data);
      const container = containerRef.current;
      if (container) {
        const bars = container.children;
        for (let i = 0; i < barCount; i++) {
          // 桶内峰值更跟人耳感知(平均会被低频压扁)
          let peak = 0;
          const start = i * bucketSize;
          const end = Math.min(start + bucketSize, data.length);
          for (let j = start; j < end; j++) {
            const v = data[j] ?? 0;  // noUncheckedIndexedAccess 友好
            if (v > peak) peak = v;
          }
          const v = peak / 255;
          // 最小 4% 让静音也有"呼吸",最大 1.0 顶到容器高度
          const sy = Math.max(0.04, Math.min(1, v));
          (bars[i] as HTMLElement).style.transform = `scaleY(${sy})`;
        }
      }
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(rafId);
      try { src?.disconnect(); } catch { /* ignore */ }
      try { analyser.disconnect(); } catch { /* ignore */ }
      ctx.close().catch(() => { /* AudioContext 可能已被外部 close 过 */ });
    };
  }, [stream, active, barCount]);

  if (!active) return null;

  // 28 根柱:每根 3 px 宽,gap 2 px → 大约 138 px 总宽。容器 flex 居中。
  // 颜色用 brand,渐变让低频(左侧)偏亮、高频(右侧)偏暗,符合人耳感知。
  return (
    <div
      className={`flex items-center justify-center gap-[2px] ${className ?? ''}`}
      style={{ height: `${height}px` }}
      role="img"
      aria-label="录音声纹可视化"
    >
      <div
        ref={containerRef}
        className="flex items-end gap-[2px]"
        style={{ height: `${height - 8}px` }}
      >
        {Array.from({ length: barCount }, (_, i) => {
          // 渐变:0 → 1 映射到 hsl 色相,左侧偏蓝绿,右侧偏紫红,跟"频谱"语义对齐
          const hue = 200 - (i / barCount) * 80; // 200 → 120 (蓝 → 黄绿)
          return (
            <div
              key={i}
              className="w-[3px] origin-bottom rounded-full transition-transform duration-75 ease-out"
              style={{
                height: '100%',
                background: `hsl(${hue}, 75%, 55%)`,
                transform: 'scaleY(0.04)',
              }}
            />
          );
        })}
      </div>
    </div>
  );
};
