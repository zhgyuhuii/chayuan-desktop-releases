/**
 * 实时语音输入 hook — 每 ~2.5s 重启 MediaRecorder 出独立 WebM 文件 → 转写 → onPartial。
 *
 * 用法:
 *   const mic = useMicRecorder({
 *     sliceMs: 2500,
 *     onPartial: (text, idx) => setDraft((p) => p + text + ' '),
 *     onError: (err) => toast(err.message),
 *   });
 *   <button onClick={mic.recording ? mic.stop : mic.start} />
 *   {mic.recording && <span>{mic.elapsed}s</span>}
 *
 * 切片策略 — 为什么 stop/restart 而不是 MediaRecorder.start(timeslice):
 *  timeslice 模式下只有 chunk #0 含完整 EBML/Tracks 头,chunk #1+ 是裸 Cluster 段,
 *  whisper / faster-whisper 单独解码必定失败 → 返回空字符串 → 体感"完全没识别"。
 *  改成"每 sliceMs 调一次 mr.stop() → 在同一 stream 上 new MediaRecorder 继续",
 *  每片都是自包含可解码的 WebM 文件。切片边界会丢 ~ms 级音频,可接受。
 *
 * 关键约束:
 *  - 每个 chunk 独立 transcribe POST,结果按 chunkIndex 排序后 commit
 *  - stop 时停止滚动 + 等所有 in-flight transcribe 完成再调 onFinal
 *  - cancel 时 abort 所有 in-flight 请求 + stop MediaRecorder
 *  - 浏览器不支持 audio/webm 时降级到默认 MIME(MediaRecorder 自选)
 */
import * as React from 'react';
import { modality } from '@chayuan/api';
import { uuid } from '@chayuan/platform-shared';

export interface UseMicRecorderOptions {
  /** 切片时长(ms),默认 4000 */
  sliceMs?: number;
  /** 语言提示("zh"/"en"/""为自动) */
  language?: string;
  /** 每片转写完成后回调(按 chunkIndex 顺序;乱序到达会 buffer 等齐)
   *  session 模式下 text = stable_increment(LocalAgreement-2 二次确认后新 commit
   *  的字符);非 session 模式下 text = 整 chunk 的转写。 */
  onPartial?: (text: string, chunkIndex: number) => void;
  /** 流式 ASR 实时校准回调:每个 chunk 返时调,带"当前 hypothesis 的未确认尾部"。
   *  caller 应该:
   *    - delete 上次 volatile 占位
   *    - 在尾部 insert 新 volatile(灰色斜体)
   *    - onPartial 触发时再 commit 新 stable 文字
   *  没传时只走 onPartial 路径,不显示实时 volatile(老行为)。 */
  onVolatile?: (volatile: string, chunkIndex: number) => void;
  /** 所有 chunk 转写完成 + stop 后整段文本回调 */
  onFinal?: (fullText: string) => void;
  /** 错误回调(getUserMedia 拒绝 / MediaRecorder unsupported) */
  onError?: (err: Error) => void;
}

export interface UseMicRecorderResult {
  recording: boolean;
  elapsed: number;
  /** 当前 MediaStream(refresh 会换;视觉化组件用它做 AnalyserNode)。
   *  非 recording / 还没 getUserMedia 时为 null。 */
  stream: MediaStream | null;
  /** 开始录音 */
  start: () => Promise<void>;
  /** 正常停止(等 in-flight 完成 + flush) */
  stop: () => void;
  /** 立刻取消(abort 所有 in-flight) */
  cancel: () => void;
}

export function useMicRecorder(opts: UseMicRecorderOptions = {}): UseMicRecorderResult {
  // 默认 2.5s 切片:首字反馈在 ~3-4s,体感"近实时";继续录音持续追加。
  const sliceMs = opts.sliceMs ?? 2500;
  const [recording, setRecording] = React.useState(false);
  const [elapsed, setElapsed] = React.useState(0);
  // VoiceVisualizer 需要拿到 MediaStream 跑 AnalyserNode;refresh 时换 stream
  // 会触发 state 更新 → 可视化组件 useEffect 重新挂 AudioContext。开销很低。
  const [stream, setStream] = React.useState<MediaStream | null>(null);

  const recorderRef = React.useRef<MediaRecorder | null>(null);
  const streamRef = React.useRef<MediaStream | null>(null);
  const mimeTypeRef = React.useRef<string>('');
  const chunkIndexRef = React.useRef(0);
  const expectedRef = React.useRef(0);
  const pendingRef = React.useRef<Map<number, string>>(new Map());
  // chunk idx → 该 chunk 返回时的 volatile_suffix。在 flushBuffered 推进 expected
  // 之后取最后已 flush idx 对应的 volatile 触发 onVolatile,确保顺序是
  // **onPartial 先(stable 文字 commit)→ onVolatile 后(灰色显示)**,避免
  // 之前"onVolatile 插完灰色,onPartial 把它删了"的 bug。
  const volatilesRef = React.useRef<Map<number, string>>(new Map());
  // 前端"已 commit 全局文本"对账锚点:每个 chunk 后端会返 committed_text(全局
  // 累积),前端比对 lastCommittedAllRef 算真增量,不依赖后端 stable_increment
  // 字段。这避免某些 corner case(retroactive_change / LocalAgreement 不进展)
  // 导致 stable_increment="" 但 committed_text 实际增长了 → 用户看到的"文字丢失"。
  const lastCommittedAllRef = React.useRef<string>('');
  const fullTextRef = React.useRef<string[]>([]);
  const abortersRef = React.useRef<AbortController[]>([]);
  const startTsRef = React.useRef(0);
  const tickerRef = React.useRef<ReturnType<typeof setInterval> | null>(null);
  const sliceTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopRequestedRef = React.useRef(false);
  // 流式 LocalAgreement-2 session id — start 时生成,每个 chunk POST 带上。
  // 后端拿到 session_id 时走累积+二次校验路径,返 stable_increment(本次新 commit)
  // 而不是整 chunk 文本;比单 chunk 转写准多了。is_full 时(后端积累 30s 上限)
  // 客户端这层自动换新 session id 续录。
  const sessionIdRef = React.useRef<string>('');
  // 长跑稳定性:每 N 个 chunk(~20s)refresh 一次 MediaStream — Tauri WebView2
  // 上反复 stop/start MediaRecorder 同一 stream 会逐步 leak 内部状态,16-20 次
  // 后 ondataavailable 不再触发(用户报告"40s 卡死"即此现象)。close 老 stream
  // + getUserMedia 拿新 stream 后立刻恢复,只丢 ~100ms 音频,可接受。
  const SLICES_BEFORE_STREAM_REFRESH = 8;
  // 后端 ASR Semaphore(1) 串行跑;若 in-flight 超过这个数就丢新 chunk(详见 mr.ondataavailable)
  const MAX_IN_FLIGHT = 3;
  // ── VAD(Voice Activity Detection):RMS < 阈值 视为静音 → 跳过 chunk 不发后端 ──
  // 不做 VAD 时,whisper 在静音 chunk 上会 hallucinate 出"獲勝荒謬將原裙封開"
  // 这种乱码 + 重复刷。前端在送 chunk 前用 AnalyserNode 算 RMS,持续静音直接丢。
  //  - SILENCE_RMS_THRESHOLD: 经验值,人声 ~0.05-0.15,白噪 ~0.005-0.02,完全静音 ~0.0005
  //  - 取整 chunk 时间窗内的 max RMS 比平均更鲁棒(咳嗽/呼吸不漏)
  const SILENCE_RMS_THRESHOLD = 0.015;
  const audioCtxRef = React.useRef<AudioContext | null>(null);
  const analyserRef = React.useRef<AnalyserNode | null>(null);
  const rmsSamplesRef = React.useRef<number[]>([]);
  const rmsIntervalRef = React.useRef<ReturnType<typeof setInterval> | null>(null);
  const slicesOnStreamRef = React.useRef(0);
  // finalize 防重入 — stop 调一次、watchdog 调一次、mr.onstop 调一次都可能同时
  // 排队,onFinal 必须只 fire 一次(用户拿到一次 full text 提示)
  const finalizedRef = React.useRef(false);
  const optsRef = React.useRef(opts);
  optsRef.current = opts;

  const flushBuffered = React.useCallback(() => {
    while (pendingRef.current.has(expectedRef.current)) {
      const idx = expectedRef.current;
      const txt = pendingRef.current.get(idx)!;
      pendingRef.current.delete(idx);
      if (txt) {
        fullTextRef.current.push(txt);
        // strip 换行 — Tiptap insertContent 会把 \n 解析成 hardBreak,导致
        // 用户看到"换行太多";语音转写本身不应有换行
        optsRef.current.onPartial?.(txt.replace(/\r?\n+/g, ' '), idx);
      }
      expectedRef.current++;
    }
    // 所有 in-order onPartial 都触发完后,**最后**用最新已 flush idx 的 volatile
    // 触发 onVolatile。这个顺序保证 stable 先 commit、volatile 后插,避免
    // "onVolatile 先插灰色 → onPartial 调 removeVolatileRange 删掉刚插的"bug。
    const lastFlushedIdx = expectedRef.current - 1;
    if (lastFlushedIdx >= 0) {
      const v = (volatilesRef.current.get(lastFlushedIdx) ?? '').replace(/\r?\n+/g, ' ');
      // 清掉已用的 volatile(只保留最新一个的索引上限以下的不再有用)
      for (const k of [...volatilesRef.current.keys()]) {
        if (k <= lastFlushedIdx) volatilesRef.current.delete(k);
      }
      optsRef.current.onVolatile?.(v, lastFlushedIdx);
    }
  }, []);

  // VAD:对当前 stream 起 AnalyserNode + 50ms 周期采 RMS 进 ring buffer
  const startVad = React.useCallback((mediaStream: MediaStream) => {
    // 先收掉旧的(refresh stream 时会重挂)
    if (rmsIntervalRef.current) {
      clearInterval(rmsIntervalRef.current);
      rmsIntervalRef.current = null;
    }
    if (audioCtxRef.current) {
      try { audioCtxRef.current.close(); } catch { /* ignore */ }
      audioCtxRef.current = null;
    }
    analyserRef.current = null;
    const AudioCtor =
      (window as Window & { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).AudioContext
      ?? (window as Window & { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtor) return;
    try {
      const ctx = new AudioCtor();
      audioCtxRef.current = ctx;
      const src = ctx.createMediaStreamSource(mediaStream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      src.connect(analyser);
      analyserRef.current = analyser;
      const buf = new Uint8Array(analyser.fftSize);
      rmsIntervalRef.current = setInterval(() => {
        const a = analyserRef.current;
        if (!a) return;
        a.getByteTimeDomainData(buf);
        // RMS in [0, 1]:byte 128 = 静音中线
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = ((buf[i] ?? 128) - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        rmsSamplesRef.current.push(rms);
        // 只保留最近 ~5 秒(50ms × 100 = 5s)足够覆盖最长 sliceMs
        if (rmsSamplesRef.current.length > 100) rmsSamplesRef.current.shift();
      }, 50);
    } catch (e) {
      console.warn('[mic-vad] 启动 AnalyserNode 失败,VAD 退化为不做(可能会有 hallucination):', e);
    }
  }, []);

  const stopVad = React.useCallback(() => {
    if (rmsIntervalRef.current) {
      clearInterval(rmsIntervalRef.current);
      rmsIntervalRef.current = null;
    }
    analyserRef.current = null;
    if (audioCtxRef.current) {
      try { audioCtxRef.current.close(); } catch { /* ignore */ }
      audioCtxRef.current = null;
    }
    rmsSamplesRef.current = [];
  }, []);

  /** 取最近 windowMs 的 RMS 峰值 — 用来判 chunk 是不是静音。 */
  const recentRmsPeak = React.useCallback((windowMs: number): number => {
    const sampleCount = Math.max(1, Math.floor(windowMs / 50));
    const samples = rmsSamplesRef.current.slice(-sampleCount);
    let peak = 0;
    for (const v of samples) if (v > peak) peak = v;
    return peak;
  }, []);

  const cleanup = React.useCallback(() => {
    if (tickerRef.current) {
      clearInterval(tickerRef.current);
      tickerRef.current = null;
    }
    if (sliceTimerRef.current) {
      clearTimeout(sliceTimerRef.current);
      sliceTimerRef.current = null;
    }
    stopVad();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setStream(null);
    recorderRef.current = null;
    mimeTypeRef.current = '';
    abortersRef.current = [];
    setRecording(false);
    setElapsed(0);
  }, [stopVad]);

  /** 等所有 in-flight transcribe 完成,触发 onFinal + cleanup。stop / cancel /
   *  watchdog 强制收尾都走这里,不依赖 mr.onstop(MediaRecorder 进入 stale 状态
   *  时它不会 fire)。幂等:finalizedRef 保证 onFinal 只 fire 一次。 */
  const finalizeAndCleanup = React.useCallback(() => {
    if (finalizedRef.current) return;
    finalizedRef.current = true;
    // ⚠ 不在这里主动 onVolatile('',-1) — 让 onFinal 让 NoteEditor 决定怎么处理
    // 残留 volatile。当前 NoteEditor 策略是"把灰色 volatile 转黑色保留",
    // 而不是直接删掉(用户已经说出来了,不该被吞掉)
    const checkDone = () => {
      if (chunkIndexRef.current === expectedRef.current) {
        const full = fullTextRef.current.join(' ').trim();
        optsRef.current.onFinal?.(full);
        cleanup();
      } else {
        setTimeout(checkDone, 200);
      }
    };
    checkDone();
  }, [cleanup]);

  /** 重新拿 MediaStream — long-running 时 stale stream 自愈。返 true 表示新 stream
   *  已就绪可继续录;false 表示获取失败(用户拒绝权限 / 设备失联),caller 应停。 */
  const refreshStream = React.useCallback(async (): Promise<boolean> => {
    try {
      const next = await navigator.mediaDevices.getUserMedia({ audio: true });
      const old = streamRef.current;
      streamRef.current = next;
      setStream(next);
      startVad(next);  // 新 stream → 重挂 VAD analyser
      slicesOnStreamRef.current = 0;
      // 老 stream 释放放后面,避免某些浏览器 close 时连带 disrupt 新 stream
      try { old?.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
      console.debug('[mic] stream refreshed');
      return true;
    } catch (e) {
      console.warn('[mic] stream refresh failed', e);
      optsRef.current.onError?.(
        e instanceof Error ? e : new Error('麦克风 stream 刷新失败'),
      );
      return false;
    }
  }, []);

  /** 启动单个 slice 的 MediaRecorder;mr.onstop 里要么开下一片,要么走 final。*/
  const startSlice = React.useCallback(() => {
    const stream = streamRef.current;
    if (!stream) return;
    const mimeType = mimeTypeRef.current;
    const mr = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);
    recorderRef.current = mr;

    // watchdog:这个 slice 应该在 sliceMs + 2s 内产生 chunk(stop + ondataavailable
    // 都触发完)。若超时未拿到 chunk,认定 stream stale → 强制 refresh stream
    // + 重启 slice。这是 stale 退化的主要兜底,用户 40s 后不再出文字就是这种情况。
    let chunkArrived = false;
    let watchdog: ReturnType<typeof setTimeout> | null = null;
    const armWatchdog = () => {
      watchdog = setTimeout(async () => {
        if (chunkArrived || stopRequestedRef.current) return;
        if (recorderRef.current !== mr) return;
        console.warn(
          '[mic] watchdog fired — chunk 未在 %dms 内到达,认定 stream stale,refresh',
          sliceMs * 3,
        );
        try { mr.stop(); } catch { /* ignore — stale */ }
        const ok = await refreshStream();
        if (!ok || stopRequestedRef.current) {
          // refresh 也失败 → 强制结束
          stopRequestedRef.current = true;
          finalizeAndCleanup();
          return;
        }
        startSlice();
      }, sliceMs * 3);
    };

    mr.ondataavailable = (e) => {
      chunkArrived = true;
      if (watchdog) { clearTimeout(watchdog); watchdog = null; }
      if (!e.data || e.data.size === 0) return;
      const idx = chunkIndexRef.current++;

      // backpressure:实时模式下后端 Semaphore(1) 串行跑 whisper,单 chunk
      // ~0.5-2s。若用户 CPU 慢、每 chunk > 2.5s 切片间隔,队列就会无限增长 —
      // stop 时一堆 in-flight,5s 兜底 abort 会刷一行"abort N in-flight"警告,
      // 用户体感"卡死"。这里硬性丢弃超出 MAX_IN_FLIGHT 的新 chunk(标空让
      // flushBuffered 推 expectedRef),牺牲少量音频换"录到哪显示到哪"。
      const inFlight = idx - expectedRef.current;
      if (inFlight > MAX_IN_FLIGHT) {
        console.warn(
          `[mic] chunk #${idx} 跳过(后端跟不上,in-flight=${inFlight} > ${MAX_IN_FLIGHT}) — ` +
            `若长期出现,说明 ASR 模型对 CPU 太重,换 small/tiny 或启用 GPU`,
        );
        pendingRef.current.set(idx, '');
        flushBuffered();
        return;
      }

      // VAD 静音跳过 — whisper 在静音 chunk 上 hallucinate 出"獲勝荒謬將原裙
      // 封開"反复刷的乱码,根因就是这。前端取整 chunk 时间窗的 RMS 峰值,低
      // 于阈值整段都没"信号" → 直接丢,根本不让 whisper 见到静音
      const sliceRmsPeak = recentRmsPeak(sliceMs);
      if (sliceRmsPeak < SILENCE_RMS_THRESHOLD) {
        console.debug(
          `[mic] chunk #${idx} 静音跳过 (peak RMS ${sliceRmsPeak.toFixed(4)} < ` +
            `${SILENCE_RMS_THRESHOLD})`,
        );
        pendingRef.current.set(idx, '');
        flushBuffered();
        return;
      }

      const ac = new AbortController();
      abortersRef.current.push(ac);
      // 此时 blob 是一个完整的 WebM 文件(含 EBML 头),whisper 可独立解码
      const ext = e.data.type.includes('webm')
        ? 'webm' : e.data.type.includes('ogg')
        ? 'ogg' : 'webm';
      const named = new File(
        [e.data], `chunk-${idx}.${ext}`,
        { type: e.data.type || 'audio/webm' },
      );
      console.debug(
        `[mic] chunk #${idx} size=${named.size}B type=${named.type}`,
      );
      modality
        .transcribe(named, {
          language: optsRef.current.language,
          signal: ac.signal,
          sessionId: sessionIdRef.current,
        })
        .then((r) => {
          // session 模式:用 committed_text 全局对账算真增量,不依赖后端
          // stable_increment 字段(可能为空但 committed_text 增长了)。
          const stableInc = (r.text || '').trim();
          const committedAll = (r.committed_text || '').trim();
          const volatileText = (r.volatile_suffix || '').trim();
          const isFull = !!r.is_full;

          // 增量优先用 committed_text - lastCommittedAll;只有当 committed_text
          // 缺失(非 session 路径 / 后端老版本)或两者不一致时,fallback stableInc
          let increment = stableInc;
          if (committedAll) {
            if (committedAll.startsWith(lastCommittedAllRef.current)) {
              increment = committedAll.slice(lastCommittedAllRef.current.length);
            } else {
              // committed_text 倒退或分叉(罕见,sessionId rotate 时可能发生)
              // → 用 stableInc 兜底,但更新锚点保证下次对账正确
              console.warn(
                '[mic] committed_text divergence:',
                'last=', lastCommittedAllRef.current.slice(-30),
                'new=', committedAll.slice(-30),
              );
            }
            lastCommittedAllRef.current = committedAll;
          }

          console.debug(
            `[mic] chunk #${idx} sid=${sessionIdRef.current.slice(0, 6)} ` +
              `+"${increment}" (raw stable="${stableInc}", committed=${committedAll.length}c) ` +
              `vola="${volatileText.slice(0, 30)}" full=${isFull}`,
          );
          if (isFull) {
            // 后端旧版若返 is_full,自动换新 sid;新版滑窗后 is_full 永远 false
            sessionIdRef.current = uuid();
            lastCommittedAllRef.current = '';  // 新 session 锚点重置
            console.debug('[mic] session full,rotated to', sessionIdRef.current.slice(0, 6));
          }
          // 把 volatile 暂存到 volatilesRef,flushBuffered 推进 expected 后才用
          // 最新已 flush idx 对应的 volatile 触发 onVolatile — 这样**onPartial
          // 先 commit 黑字,onVolatile 后插灰色尾部**,顺序对了
          volatilesRef.current.set(idx, volatileText);
          pendingRef.current.set(idx, increment);
          flushBuffered();
        })
        .catch((err) => {
          // 单 chunk 失败(sidecar ReadTimeout / 网络抖动)— LocalAgreement-2 在
          // 下一 chunk 会自愈,这次只是没贡献文字。降级为 debug 不刷屏。真长期
          // 不通会在 onFinal 的"零文字"分支走 asrDiag 报给用户。
          console.debug(`[mic] chunk #${idx} transcribe failed (容错继续)`, err);
          pendingRef.current.set(idx, '');
          flushBuffered();
        });
    };

    mr.onstop = () => {
      if (watchdog) { clearTimeout(watchdog); watchdog = null; }
      if (stopRequestedRef.current) {
        finalizeAndCleanup();
        return;
      }
      // slice 边界:N 次后 refresh stream 避免 long-running 累积
      slicesOnStreamRef.current++;
      if (slicesOnStreamRef.current >= SLICES_BEFORE_STREAM_REFRESH) {
        void (async () => {
          if (stopRequestedRef.current) { finalizeAndCleanup(); return; }
          const ok = await refreshStream();
          if (!ok || stopRequestedRef.current) {
            stopRequestedRef.current = true;
            finalizeAndCleanup();
            return;
          }
          startSlice();
        })();
      } else {
        startSlice();
      }
    };

    mr.start();
    armWatchdog();
    // 定时停 → 触发 ondataavailable(完整文件)→ onstop → 起下一片
    sliceTimerRef.current = setTimeout(() => {
      try {
        if (recorderRef.current === mr && mr.state === 'recording') mr.stop();
      } catch {
        // ignore
      }
    }, sliceMs);
  }, [sliceMs, finalizeAndCleanup, refreshStream, flushBuffered]);

  const start = React.useCallback(async () => {
    if (recording) return;
    if (typeof window === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      optsRef.current.onError?.(new Error('浏览器不支持录音(MediaDevices)'));
      return;
    }
    if (typeof MediaRecorder === 'undefined') {
      optsRef.current.onError?.(new Error('浏览器不支持 MediaRecorder'));
      return;
    }
    chunkIndexRef.current = 0;
    expectedRef.current = 0;
    pendingRef.current.clear();
    volatilesRef.current.clear();
    lastCommittedAllRef.current = '';
    fullTextRef.current = [];
    abortersRef.current = [];
    stopRequestedRef.current = false;
    finalizedRef.current = false;
    slicesOnStreamRef.current = 0;
    // 每次开录新一个 session id,后端用它做 LocalAgreement-2 上下文修正
    sessionIdRef.current = uuid();
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      optsRef.current.onError?.(e instanceof Error ? e : new Error('麦克风权限被拒绝'));
      return;
    }
    streamRef.current = stream;
    setStream(stream);
    startVad(stream);  // 起 VAD analyser:50ms 采 RMS,送 chunk 前判静音用
    mimeTypeRef.current = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : '';

    startTsRef.current = Date.now();
    setRecording(true);
    tickerRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTsRef.current) / 1000));
    }, 500);

    startSlice();
  }, [recording, startSlice]);

  const stop = React.useCallback(() => {
    if (!recording) return;
    stopRequestedRef.current = true;
    if (sliceTimerRef.current) {
      clearTimeout(sliceTimerRef.current);
      sliceTimerRef.current = null;
    }
    const mr = recorderRef.current;
    let onstopWillFire = false;
    try {
      if (mr && mr.state === 'recording') {
        mr.stop();
        onstopWillFire = true;
      }
    } catch (e) {
      console.warn('[mic] mr.stop() throw,直接 force finalize', e);
    }
    // 如果 mr 已 stale → onstop 不 fire → finalizeAndCleanup 永不被触发 → UI
    // 停不下来。主动 finalize 走兜底路径,双保险幂等(finalizedRef 保证 onFinal
    // 只 fire 一次)。
    if (!onstopWillFire) {
      finalizeAndCleanup();
    }
    // 兜底 5s:in-flight transcribe 可能 stuck(后端 sidecar ReadTimeout),
    // 那么 chunkIndexRef 永远不等 expectedRef,finalizeAndCleanup 卡死在
    // checkDone 循环。force abort 所有 in-flight → fetch reject → catch 设 ""
    // → expectedRef 推进 → checkDone 完成 → cleanup → UI 退出录音态。
    setTimeout(() => {
      if (finalizedRef.current && abortersRef.current.length === 0) return;
      const pending = chunkIndexRef.current - expectedRef.current;
      if (pending > 0) {
        console.warn(
          `[mic] stop 5s 兜底:abort ${pending} in-flight transcribe(s),后端 sidecar 可能 stuck`,
        );
      }
      abortersRef.current.forEach((ac) => { try { ac.abort(); } catch { /* ignore */ } });
      abortersRef.current = [];
    }, 5000);
  }, [recording, finalizeAndCleanup]);

  const cancel = React.useCallback(() => {
    stopRequestedRef.current = true;
    if (sliceTimerRef.current) {
      clearTimeout(sliceTimerRef.current);
      sliceTimerRef.current = null;
    }
    abortersRef.current.forEach((ac) => ac.abort());
    abortersRef.current = [];
    const mr = recorderRef.current;
    try {
      if (recording && mr && mr.state === 'recording') mr.stop();
    } catch {
      // ignore
    }
    cleanup();
  }, [recording, cleanup]);

  React.useEffect(() => () => {
    // unmount 清理
    stopRequestedRef.current = true;
    abortersRef.current.forEach((ac) => ac.abort());
    streamRef.current?.getTracks().forEach((t) => t.stop());
    if (tickerRef.current) clearInterval(tickerRef.current);
    if (sliceTimerRef.current) clearTimeout(sliceTimerRef.current);
  }, []);

  return { recording, elapsed, stream, start, stop, cancel };
}
