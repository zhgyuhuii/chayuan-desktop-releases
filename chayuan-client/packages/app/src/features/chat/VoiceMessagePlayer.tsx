/**
 * 用户消息底部的录音播放器 — 紧凑两图标(播放 + 变速)+ 时长。
 *
 * 用法:<VoiceMessagePlayer audioUrl={url} />
 *  - audioUrl 是 URL.createObjectURL(blob) 出来的 blob: 链接(caller 负责 revoke)
 *
 * 设计:
 *  - 不用 <audio controls> 默认 UI(浏览器原生 UI 主题不可控,且部分用户报"看不见播放键")
 *  - 自己渲染 ▶/⏸ + 速度循环按钮(1x → 1.25x → 1.5x → 2x → 1x)+ 当前时长 / 总时长
 *  - audio 元素隐藏,逻辑上还在,UI 由我们控
 *  - error 事件触发时显示 ⚠ + 错误说明(没声音常见是 MIME / 编解码 / 设备问题)
 *  - duration NaN/Infinity(webm 头某些 build 不携带时长)时显示"--:--"不报错
 */
import * as React from 'react';
import { Gauge, Pause, Play } from 'lucide-react';

const SPEEDS = [1, 1.25, 1.5, 2] as const;

const fmtTime = (sec: number): string => {
  if (!Number.isFinite(sec) || sec < 0) return '--:--';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

export interface VoiceMessagePlayerProps {
  audioUrl: string;
  /** 可选,如果 caller 知道录音 MIME,优先用以加速 audio 元素 codec 决策 */
  mimeType?: string;
}

export const VoiceMessagePlayer: React.FC<VoiceMessagePlayerProps> = ({
  audioUrl, mimeType,
}) => {
  const audioRef = React.useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = React.useState(false);
  const [speedIdx, setSpeedIdx] = React.useState(0);
  const [duration, setDuration] = React.useState<number>(NaN);
  const [position, setPosition] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);

  const togglePlay = async () => {
    const a = audioRef.current;
    if (!a) return;
    setError(null);
    try {
      if (a.paused || a.ended) {
        // 显式 user gesture → 不应被 autoplay policy 拦,若被拦会 reject
        await a.play();
      } else {
        a.pause();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const cycleSpeed = () => {
    const next = (speedIdx + 1) % SPEEDS.length;
    setSpeedIdx(next);
    if (audioRef.current) audioRef.current.playbackRate = SPEEDS[next]!;
  };

  // 挂载后立即触发 load,这样 onLoadedMetadata 能在用户点播放前就 fire,拿到 duration
  React.useEffect(() => {
    audioRef.current?.load();
  }, [audioUrl]);

  return (
    <div className="mt-2 inline-flex items-center gap-1.5 rounded-full bg-black/15 px-2 py-1 text-[11px]">
      <audio
        ref={audioRef}
        src={audioUrl}
        // 给 audio 元素一个明确的 type,WebView2 / 旧 Chromium 偶尔在 blob URL 上
        // 嗅探不准时靠 type 选 codec 路径
        // (注:audio 元素本身不接受 type prop,只能在 <source> 上;mimeType 这里仅作 hint
        //  保留给将来若改成 <audio><source type=… /></audio> 用)
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
        onError={() => {
          const a = audioRef.current;
          const code = a?.error?.code;
          const codeMap: Record<number, string> = {
            1: 'MEDIA_ERR_ABORTED',
            2: 'MEDIA_ERR_NETWORK',
            3: 'MEDIA_ERR_DECODE — 解码失败(常见:webm/opus 不支持)',
            4: 'MEDIA_ERR_SRC_NOT_SUPPORTED — 浏览器不支持该格式',
          };
          setError(code ? codeMap[code] ?? `code=${code}` : '未知音频错误');
        }}
        style={{ display: 'none' }}
        data-mime={mimeType}
      />

      <button
        type="button"
        onClick={() => void togglePlay()}
        className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-white/30 text-current hover:bg-white/50"
        title={playing ? '暂停' : '播放原录音'}
        aria-label={playing ? '暂停' : '播放'}
      >
        {playing ? <Pause className="h-3 w-3" /> : <Play className="h-3 w-3 translate-x-[0.5px]" />}
      </button>

      <button
        type="button"
        onClick={cycleSpeed}
        className="inline-flex h-5 items-center gap-0.5 rounded-full bg-white/20 px-1.5 hover:bg-white/40"
        title="变速播放(点击循环 1× / 1.25× / 1.5× / 2×)"
        aria-label={`变速:当前 ${SPEEDS[speedIdx]}x`}
      >
        <Gauge className="h-3 w-3" />
        <span className="font-mono tabular-nums">{SPEEDS[speedIdx]}×</span>
      </button>

      <span className="font-mono tabular-nums opacity-75">
        {fmtTime(position)} / {fmtTime(duration)}
      </span>

      {error && (
        <span
          className="ml-1 rounded bg-red-500/30 px-1 text-[10px]"
          title={`播放错误: ${error}\n常见原因: 1) 系统输出设备/音量;2) WebView2 不支持 webm/opus 解码(很罕见);3) Blob 为空`}
        >
          ⚠
        </span>
      )}
    </div>
  );
};
