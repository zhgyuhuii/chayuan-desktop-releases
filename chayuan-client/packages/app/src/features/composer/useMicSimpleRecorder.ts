/**
 * 简化版录音 hook — 录完整段一次性回调,不做实时转写。
 *
 * 跟旧 useMicRecorder 的区别:
 *   - **不切片**:MediaRecorder 一次 start,stop 时拿到单一完整 webm Blob
 *   - **不实时转写**:录音过程不调 modality.transcribe,无文字反馈
 *   - **原生 pause/resume**:用 MediaRecorder.pause() / resume(),无缝继续
 *   - **录完回调**:onRecorded(blob, mimeType) 把整段 blob 给 caller,caller 决定怎么用
 *     (典型:调 modality.transcribeFile 整段转 → 高准确率,不会 hallucination)
 *
 * 设计动机:实时 chunk 模式 whisper 在 chunk 边界 / 静音时 hallucinate 严重
 * (用户报"獲勝荒謬將原裙封開" / "谢谢谢谢"反复刷)。整段一次性送模型,
 * whisper 内部 VAD + 上下文建模发挥得更好,体感"录完就有准确稿"。
 *
 * 暴露 stream 给 VoiceVisualizer 用,录音过程仍有声纹动画反馈。
 */
import * as React from 'react';

export interface UseMicSimpleRecorderOptions {
  /** 整段录音完成回调 — 用户点停止 / 自然结束时触发一次。
   *  blob 是单一完整 webm(包含 EBML 头),可直接送 transcribeFile。 */
  onRecorded?: (blob: Blob, mimeType: string) => void;
  /** 错误(权限拒绝 / 不支持 MediaRecorder)*/
  onError?: (err: Error) => void;
}

export interface UseMicSimpleRecorderResult {
  recording: boolean;
  paused: boolean;
  /** 已录秒数(暂停期间不增长)*/
  elapsed: number;
  /** 当前 MediaStream,供 VoiceVisualizer */
  stream: MediaStream | null;
  start: () => Promise<void>;
  pause: () => void;
  resume: () => void;
  /** 停止 → 触发 onRecorded(整段 blob) */
  stop: () => void;
  /** 取消 — 丢弃录音,不触发 onRecorded */
  cancel: () => void;
}

export function useMicSimpleRecorder(
  opts: UseMicSimpleRecorderOptions = {},
): UseMicSimpleRecorderResult {
  const [recording, setRecording] = React.useState(false);
  const [paused, setPaused] = React.useState(false);
  const [elapsed, setElapsed] = React.useState(0);
  const [stream, setStream] = React.useState<MediaStream | null>(null);

  const recorderRef = React.useRef<MediaRecorder | null>(null);
  const streamRef = React.useRef<MediaStream | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);
  const mimeTypeRef = React.useRef<string>('');
  // elapsed 计时:记录开始时间和已暂停累计时长
  const startTsRef = React.useRef<number>(0);
  const pausedElapsedMsRef = React.useRef<number>(0);
  const pauseStartTsRef = React.useRef<number>(0);
  const tickerRef = React.useRef<ReturnType<typeof setInterval> | null>(null);
  const cancelledRef = React.useRef(false);
  const optsRef = React.useRef(opts);
  optsRef.current = opts;

  const cleanup = React.useCallback(() => {
    if (tickerRef.current) {
      clearInterval(tickerRef.current);
      tickerRef.current = null;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setStream(null);
    recorderRef.current = null;
    mimeTypeRef.current = '';
    chunksRef.current = [];
    cancelledRef.current = false;
    setRecording(false);
    setPaused(false);
    setElapsed(0);
    pausedElapsedMsRef.current = 0;
    pauseStartTsRef.current = 0;
  }, []);

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
    let mediaStream: MediaStream;
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      optsRef.current.onError?.(e instanceof Error ? e : new Error('麦克风权限被拒绝'));
      return;
    }
    streamRef.current = mediaStream;
    setStream(mediaStream);
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : '';
    mimeTypeRef.current = mimeType;
    const mr = mimeType
      ? new MediaRecorder(mediaStream, { mimeType })
      : new MediaRecorder(mediaStream);
    recorderRef.current = mr;
    chunksRef.current = [];
    cancelledRef.current = false;

    mr.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
    };
    mr.onstop = () => {
      if (cancelledRef.current) {
        cleanup();
        return;
      }
      const blob = new Blob(chunksRef.current, {
        type: mr.mimeType || mimeTypeRef.current || 'audio/webm',
      });
      const usedMime = mr.mimeType || mimeTypeRef.current || 'audio/webm';
      // 先 cleanup 释放 stream,再回调(回调可能跑 async 网络任务,不想 hold stream)
      cleanup();
      if (blob.size > 0) {
        optsRef.current.onRecorded?.(blob, usedMime);
      }
    };
    // 不传 timeslice → 只在 mr.stop() 时触发一次 ondataavailable,拿到单一完整 webm
    mr.start();
    startTsRef.current = Date.now();
    pausedElapsedMsRef.current = 0;
    setRecording(true);
    setPaused(false);
    tickerRef.current = setInterval(() => {
      // elapsed = 当前时间 - 开始时间 - 累计暂停时长
      const now = Date.now();
      const pausing = pauseStartTsRef.current > 0 ? now - pauseStartTsRef.current : 0;
      const ms = now - startTsRef.current - pausedElapsedMsRef.current - pausing;
      setElapsed(Math.max(0, Math.floor(ms / 1000)));
    }, 500);
  }, [recording, cleanup]);

  const pause = React.useCallback(() => {
    const mr = recorderRef.current;
    if (!mr || mr.state !== 'recording') return;
    try {
      mr.pause();
      pauseStartTsRef.current = Date.now();
      setPaused(true);
    } catch (e) {
      console.warn('[mic-simple] pause 失败', e);
    }
  }, []);

  const resume = React.useCallback(() => {
    const mr = recorderRef.current;
    if (!mr || mr.state !== 'paused') return;
    try {
      mr.resume();
      // 累加这次暂停的时长
      if (pauseStartTsRef.current > 0) {
        pausedElapsedMsRef.current += Date.now() - pauseStartTsRef.current;
        pauseStartTsRef.current = 0;
      }
      setPaused(false);
    } catch (e) {
      console.warn('[mic-simple] resume 失败', e);
    }
  }, []);

  const stop = React.useCallback(() => {
    const mr = recorderRef.current;
    if (!mr) return;
    cancelledRef.current = false;
    try {
      if (mr.state !== 'inactive') mr.stop(); // 触发 ondataavailable + onstop
    } catch (e) {
      console.warn('[mic-simple] stop 失败', e);
      cleanup();
    }
  }, [cleanup]);

  const cancel = React.useCallback(() => {
    cancelledRef.current = true;
    const mr = recorderRef.current;
    try {
      if (mr && mr.state !== 'inactive') mr.stop();
    } catch {
      // ignore
    }
    cleanup();
  }, [cleanup]);

  React.useEffect(() => () => {
    // unmount 清理
    cancelledRef.current = true;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    if (tickerRef.current) clearInterval(tickerRef.current);
    const mr = recorderRef.current;
    try {
      if (mr && mr.state !== 'inactive') mr.stop();
    } catch {
      // ignore
    }
  }, []);

  return { recording, paused, elapsed, stream, start, pause, resume, stop, cancel };
}
