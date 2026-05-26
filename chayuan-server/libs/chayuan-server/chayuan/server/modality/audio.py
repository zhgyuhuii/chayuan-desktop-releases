"""音频：ASR（Whisper / faster-whisper / OpenAI）+ TTS（Edge-TTS / OpenAI）。

所有依赖都是**运行时可选**：
- ASR 本地首选 ``faster-whisper``；未装则尝试 ``openai-whisper``；再不行降级到 OpenAI API
- TTS 本地首选 ``edge-tts``（免费、无 GPU）；云端走 OpenAI ``/audio/speech``

返回值全部是 bytes / str，方便在 dispatcher 里直接塞进 Streaming / Base64 响应。
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Tuple


def _safe_cleanup(cleanup) -> None:
    """tempfile.TemporaryDirectory cleanup,失败静默(测试用例传 None 跳过)。"""
    if cleanup is None:
        return
    try:
        cleanup.cleanup()
    except Exception:  # noqa: BLE001
        pass


import re as _re

# whisper / whisper.cpp 在静音 / 低音量 / 纯噪声片段 / 短音频被误判为外语时,
# 会返这些方括号 marker(`[INAUDIBLE]` `[Spanish]` 等),不是用户实际说的话。
# 前端拿到这些会把"[INAUDIBLE]"或"[Spanish]"当文字插入笔记 — 错误体感。
# 所有 ASR backend 拿到 text 后统一 strip 这类 token,strip 后空就视为"无有效语音"。
#
# 覆盖三类 marker:
#   1) 状态描述:INAUDIBLE / BLANK_AUDIO / SOUND / MUSIC / NOISE / silence
#   2) whisper 内置语言名(英文形式,~ 100 种 supported)— `[Spanish]` `[English]` 等
#   3) 中文等价标签:空白音频 / 无声 / 噪声
_WHISPER_STATE_MARKERS = (
    "INAUDIBLE", "BLANK[_\\s]?AUDIO", "SOUND", "MUSIC", "NOISE",
    "UNINTELLIGIBLE", "silence",
    "空白音频", "无声", "噪声",
)
# whisper 在静音 / 低信噪比上还会 hallucinate **圆括号 sound effect token**
# (跟方括号 marker 是独立的两类输出形态):
#   中:  (笑)  (笑声)  (掌声)  (咳嗽)  (音乐)  (噪声)  (叹气)  (哭)  (哭声)
#   日:  (笑い)  (笑い声)  (拍手)
#   英:  (laughter)  (applause)  (coughing)  (music)  (noise)
#         (silence)  (sigh)  (sneeze)  (crying)
# 用户报"全是 (笑)" — 中文 base 模型在背景噪声 / 麦克风音量低时高频 hallucinate
# 出 (笑);跟 [BLANK_AUDIO] 一样应判 artifact_only,空串返给前端,提示重录。
# 同时覆盖**全角圆括号**`(笑)` — 部分 ffmpeg/whisper.cpp build 输出全角。
_WHISPER_SOUND_EFFECTS = (
    "笑", "笑声", "笑い", "笑い声", "拍手", "掌声", "咳嗽", "咳", "音乐",
    "噪声", "叹气", "哭", "哭声", "喷嚏",
    "laughter", "laugh", "applause", "coughing", "cough", "music", "noise",
    "silence", "sigh", "sneeze", "crying",
)
# whisper.cpp src/whisper.cpp g_lang 表(主流 70+ 语言英文名,首字母大写形式)
_WHISPER_LANGUAGE_NAMES = (
    "English", "Chinese", "Spanish", "French", "German", "Japanese", "Korean",
    "Russian", "Italian", "Portuguese", "Dutch", "Polish", "Arabic", "Hindi",
    "Turkish", "Swedish", "Norwegian", "Danish", "Finnish", "Greek", "Czech",
    "Hungarian", "Romanian", "Ukrainian", "Vietnamese", "Thai", "Indonesian",
    "Malay", "Hebrew", "Catalan", "Bulgarian", "Croatian", "Serbian", "Slovak",
    "Slovenian", "Lithuanian", "Latvian", "Estonian", "Welsh", "Icelandic",
    "Maori", "Tamil", "Bengali", "Marathi", "Gujarati", "Punjabi", "Kannada",
    "Telugu", "Malayalam", "Sinhala", "Nepali", "Mongolian", "Belarusian",
    "Macedonian", "Albanian", "Armenian", "Azerbaijani", "Georgian", "Kazakh",
    "Uzbek", "Persian", "Urdu", "Pashto", "Yiddish", "Swahili", "Afrikaans",
    "Filipino", "Tagalog", "Burmese", "Khmer", "Lao", "Tibetan", "Tajik",
    "Turkmen", "Yoruba", "Zulu", "Hausa", "Igbo", "Esperanto", "Latin",
    "Maltese", "Tatar", "Bashkir", "Sundanese", "Javanese", "Cantonese",
    "Hawaiian", "Haitian", "Bosnian", "Galician", "Basque", "Breton",
    "Faroese", "Luxembourgish", "Occitan", "Sanskrit", "Somali", "Amharic",
)
_WHISPER_ARTIFACT_PATTERN = _re.compile(
    # 方括号 marker:[INAUDIBLE] / [BLANK_AUDIO] / [Spanish] 等
    r"\[\s*(?:"
    + "|".join(_WHISPER_STATE_MARKERS + _WHISPER_LANGUAGE_NAMES)
    + r")\s*\]"
    # 半角圆括号 sound effect:(笑) (laughter) (掌声) 等
    + r"|\(\s*(?:"
    + "|".join(_WHISPER_SOUND_EFFECTS)
    + r")\s*\)"
    # 全角圆括号 sound effect:（笑）（笑い）— whisper.cpp 偶尔输出全角
    # 注意:**必须**括号包裹,裸"笑"字会误吞正常句子里的字
    + r"|（\s*(?:"
    + "|".join(_WHISPER_SOUND_EFFECTS)
    + r")\s*）",
    flags=_re.IGNORECASE,
)


try:
    import zhconv as _zhconv  # type: ignore
    _HAS_ZHCONV = True
except Exception:  # noqa: BLE001 — ImportError 或 zhconv 内部 pkg_resources error
    _zhconv = None  # type: ignore[assignment]
    _HAS_ZHCONV = False


def _to_simplified_chinese(text: str) -> str:
    """繁体 → 简体。whisper.cpp 默认输出繁体(尤其大模型),用户笔记主诉求简体。
    zhconv 是纯 Python(无 native deps),无 zhconv 时 fallback 原样返回。

    ⚠ 静默 fallback 会让用户看到繁体却不知道为什么 — 调用方应同时调
    ``zhconv_missing_diag()`` 把状态加入 diagnostics,前端能给提示。
    """
    if not text or not _HAS_ZHCONV:
        return text
    try:
        return _zhconv.convert(text, "zh-cn")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return text


def zhconv_missing_diag() -> Optional[str]:
    """zhconv 缺失时返回一行 diag(给 transcribe-file 之类整段响应用);装好时返 None。
    前端拿到这行 diag 可弹"后端缺 zhconv,请在编辑器右上角手动 简/繁 切换"提示。"""
    if _HAS_ZHCONV:
        return None
    return (
        "[zhconv-missing] 后端未安装 zhconv,whisper 输出的繁体没自动转简体 — "
        "运行 `pip install zhconv` 或重装 chayuan-server 依赖。"
        "临时可在编辑器右上角点「简」按钮转。"
    )


def _is_whisper_hallucination_repetition(text: str, min_repeat: int = 3) -> bool:
    """检测 whisper 在静音/噪声上 hallucinate 的"同一子串重复 N 次"退化输出。

    典型样本(用户报):
      "獲勝荒謬將原裙封開獲勝荒謬將原裙封開獲勝荒謬將原裙封開..."
      "謝謝觀看謝謝觀看謝謝觀看..."
      "请订阅请订阅请订阅..."

    判定:长度 2-30 的子串若占据文本 ≥min_repeat 次连续出现且**几乎占满整段**
    (留 < 20% 余料容差),视为 hallucination 返回 True。

    保留正常重复(如 "你好你好" 二次),要求 ≥3 次才判定。
    """
    if not text or len(text) < 6:
        return False
    s = text.strip()
    n = len(s)
    # 试 2~min(30, n//min_repeat) 长度的子串
    max_slen = min(30, n // min_repeat)
    for slen in range(2, max_slen + 1):
        sub = s[:slen]
        # sub * min_repeat 已经覆盖了开头 slen*min_repeat 字符;若整段几乎全
        # 由 sub 重复构成 → hallucination
        repeats = 0
        i = 0
        while i + slen <= n and s[i:i + slen] == sub:
            repeats += 1
            i += slen
        if repeats >= min_repeat:
            covered = repeats * slen
            # 整段 ≥80% 被这个子串重复占据 → 判 hallucination
            if covered / n >= 0.8:
                return True
    return False


def _strip_whisper_artifacts(text: str) -> Tuple[str, bool]:
    """返 (cleaned_text, was_artifact_only)。

    was_artifact_only=True 表示原文本只含 marker / 空白 / hallucination repetition
    (整段没有任何有效内容)。caller 据此决定是否继续 fallback / 给用户提示。
    """
    if not text:
        return "", False
    raw_stripped = text.strip()
    if not raw_stripped:
        return "", False
    cleaned = _WHISPER_ARTIFACT_PATTERN.sub("", raw_stripped).strip()
    if not cleaned:
        return "", (raw_stripped != "")
    # whisper hallucination 兜底:静音 chunk 上 whisper 常返某子串重复 N 次
    # (用户报"獲勝荒謬將原裙封開"反复刷)。这种从语义上是噪声,直接判 artifact_only。
    if _is_whisper_hallucination_repetition(cleaned):
        return "", True
    return cleaned, False

logger = logging.getLogger("chayuan.modality.audio")

if not _HAS_ZHCONV:
    # 起服时打一次 warning,让运维 / 开发者立刻看到根因。许多"语音文件转化后
    # 还是繁体"的报告就是 zhconv 没装(pyproject 里列了 dep 但环境没同步)。
    logger.warning(
        "[audio] zhconv 未安装 — whisper.cpp 输出的繁体字符不会转简体。"
        "运行 `pip install zhconv` 即可,客户端用户也可点编辑器右上角的「简」按钮临时转。"
    )


class AudioPipeline:
    """ASR + TTS 两个入口，都 fail-soft。"""

    # ---- 通用：把各种源标准化为本地路径 ---------------------------------

    @staticmethod
    def _materialize(audio: Any) -> Tuple[str, Optional[tempfile.TemporaryDirectory]]:
        """接受 bytes / BytesIO / 本地路径 / http(s) URL；返回 (path, cleanup)。"""
        # bytes / BytesIO
        if isinstance(audio, (bytes, bytearray)):
            td = tempfile.TemporaryDirectory(prefix="chayuan_asr_")
            p = Path(td.name) / "input.audio"
            p.write_bytes(bytes(audio))
            return str(p), td
        if isinstance(audio, io.BytesIO):
            td = tempfile.TemporaryDirectory(prefix="chayuan_asr_")
            p = Path(td.name) / "input.audio"
            p.write_bytes(audio.getvalue())
            return str(p), td
        if isinstance(audio, str):
            if audio.startswith(("http://", "https://")):
                import urllib.request
                td = tempfile.TemporaryDirectory(prefix="chayuan_asr_")
                p = Path(td.name) / "input.audio"
                with urllib.request.urlopen(audio, timeout=30) as r:
                    p.write_bytes(r.read())
                return str(p), td
            if os.path.exists(audio):
                return audio, None
        raise ValueError(f"unsupported audio type: {type(audio).__name__}")

    # ==================================================================
    # ASR
    # ==================================================================

    @staticmethod
    def _resolve_ffmpeg() -> Optional[str]:
        """返回 ffmpeg exe 绝对路径(优先 imageio_ffmpeg 自带 binary,fallback 系统 PATH)。

        imageio-ffmpeg 是 chayuan-server 的依赖,pip 装时下载对应平台的 ffmpeg
        二进制(~30 MB),用户不必自己装系统 ffmpeg。极端情况下(wheel 下载失败
        / 离线安装) fallback 走系统 PATH 的 ffmpeg。两者都没,返 None。
        """
        try:
            import imageio_ffmpeg  # type: ignore
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.exists(exe):
                return exe
        except Exception:  # noqa: BLE001
            pass
        import shutil
        return shutil.which("ffmpeg")

    @staticmethod
    def _transcode_to_wav(src_path: str) -> Tuple[Optional[str], Optional["tempfile.TemporaryDirectory"], Optional[str]]:
        """用 ffmpeg 转任意音频 → 16kHz mono WAV(whisper.cpp / whisper 通吃)。

        whisper.cpp 的某些 build 不能解 webm/opus → POST /inference 直接返
        `{"error":"failed to read audio data"}`。转 wav 一劳永逸。

        返回 (wav_path, cleanup_dir, err_msg):
            - wav_path 非空 → 转码成功,用 wav_path 跑识别
            - wav_path None → 转码失败,err_msg 写明原因(ffmpeg 不在 / 转码出错)
        cleanup_dir 用于 caller 在用完后 .cleanup()。
        """
        import subprocess

        # 已经是 wav 就直接返回原路径,免一次落盘
        if src_path.lower().endswith(".wav"):
            return src_path, None, None
        ffmpeg_exe = AudioPipeline._resolve_ffmpeg()
        if not ffmpeg_exe:
            return None, None, (
                "ffmpeg 不可用(imageio-ffmpeg 也没装):pip install imageio-ffmpeg "
                "或装系统 ffmpeg"
            )
        td = tempfile.TemporaryDirectory(prefix="chayuan_asr_wav_")
        out_path = str(Path(td.name) / "audio.wav")
        try:
            r = subprocess.run(
                [ffmpeg_exe, "-loglevel", "error", "-y", "-i", src_path,
                 "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", out_path],
                check=False, capture_output=True, timeout=30,
            )
            if r.returncode != 0:
                err = (r.stderr or b"").decode("utf-8", errors="replace")[-300:]
                td.cleanup()
                return None, None, f"ffmpeg 转码失败 rc={r.returncode}: {err}"
            return out_path, td, None
        except subprocess.TimeoutExpired:
            try:
                td.cleanup()
            except Exception:  # noqa: BLE001
                pass
            return None, None, "ffmpeg 转码超时 (>30s)"
        except Exception as e:  # noqa: BLE001
            try:
                td.cleanup()
            except Exception:  # noqa: BLE001
                pass
            return None, None, f"ffmpeg 异常: {type(e).__name__}: {e}"

    def transcribe(self, audio: Any, *, language: Optional[str] = None,
                    model: Optional[str] = None) -> str:
        """旧 API,只回 text(向后兼容)。新代码用 transcribe_with_diag。"""
        text, _diag = self.transcribe_with_diag(
            audio, language=language, model=model,
        )
        return text

    def transcribe_with_diag(
        self, audio: Any, *,
        language: Optional[str] = None, model: Optional[str] = None,
    ) -> Tuple[str, list]:
        """Whisper 风格转录,顺带回每一层的具体失败原因。失败返回 ("", diagnostics)。

        ``model`` 不传 → 走 capability_router("asr");router 未配 → "base"。

        Plan 3C 起 fallback 顺序:
          1) 本地 sidecar (whisper-server, /inference)
          2) faster-whisper (Python in-process)
          3) openai-whisper (Python in-process)
          4) OpenAI API

        diagnostics 元素形如 "[sidecar] HTTP 200 returned empty text (1234B in)";
        modality_routes.transcribe_endpoint 把它写到 call_log 的 error 字段,
        用户在"调用日志"面板能直接看到每层挂在哪。
        """
        diag: list[str] = []

        if not model:
            try:
                from chayuan.server.capability_router import resolve_model
                model = resolve_model("asr") or "base"
            except Exception as e:  # noqa: BLE001
                diag.append(f"[router] resolve_model('asr') 异常:{e};fallback model='base'")
                model = "base"
        try:
            path, cleanup = self._materialize(audio)
        except Exception as e:  # noqa: BLE001
            logger.warning("materialize audio 失败：%r", e)
            diag.append(f"[materialize] {type(e).__name__}: {e}")
            return "", diag

        # 1) 本地 sidecar 优先
        try:
            text, sidecar_diag = self._transcribe_via_sidecar_with_diag(
                path, language=language,
            )
            diag.extend(sidecar_diag)
            if text:
                _safe_cleanup(cleanup)
                return text, diag
        except Exception as e:  # noqa: BLE001
            logger.info("[asr] sidecar 不可用,fallback Python:%r", e)
            diag.append(f"[sidecar] exception: {type(e).__name__}: {e}")

        try:
            # 2) faster-whisper(首选,速度 3-5x)
            try:
                from faster_whisper import WhisperModel  # type: ignore
                m = WhisperModel(model, device="cpu", compute_type="int8")
                segments, _info = m.transcribe(
                    path, language=language, beam_size=1,
                )
                raw_out = "".join(seg.text for seg in segments).strip()
                out, artifact_only = _strip_whisper_artifacts(raw_out)
                if out:
                    return out, diag
                if artifact_only:
                    diag.append(f"[faster-whisper] 只返回静音/噪声标记 {raw_out!r} (model={model})")
                else:
                    diag.append(f"[faster-whisper] returned empty text (model={model})")
            except Exception as e:  # noqa: BLE001
                logger.debug("faster-whisper 不可用：%r", e)
                diag.append(f"[faster-whisper] {type(e).__name__}: {e}")
            # 3) openai-whisper(CPU/GPU 皆可)
            try:
                import whisper  # type: ignore
                m = whisper.load_model(model)
                out_obj = m.transcribe(path, language=language)
                raw_out = (out_obj.get("text") or "").strip()
                out, artifact_only = _strip_whisper_artifacts(raw_out)
                if out:
                    return out, diag
                if artifact_only:
                    diag.append(f"[openai-whisper] 只返回静音/噪声标记 {raw_out!r} (model={model})")
                else:
                    diag.append(f"[openai-whisper] returned empty text (model={model})")
            except Exception as e:  # noqa: BLE001
                logger.debug("openai-whisper 不可用：%r", e)
                diag.append(f"[openai-whisper] {type(e).__name__}: {e}")
            # 4) OpenAI API(云端) — 用真 SDK,而非 chayuan.server.utils.get_OpenAI
            # (后者是 langchain ChatOpenAI wrapper,不暴露 audio.transcriptions)
            try:
                import openai as _oa  # type: ignore
                client = _oa.OpenAI()  # 读 env OPENAI_API_KEY / OPENAI_BASE_URL
                with open(path, "rb") as f:
                    resp = client.audio.transcriptions.create(
                        file=f, model="whisper-1", language=language or None,
                    )
                out = (getattr(resp, "text", None) or "").strip()
                if out:
                    return out, diag
                diag.append("[openai-api] returned empty text")
            except Exception as e:  # noqa: BLE001
                logger.warning("OpenAI ASR 失败：%r", e)
                diag.append(f"[openai-api] {type(e).__name__}: {e}")
            return "", diag
        finally:
            _safe_cleanup(cleanup)

    def _transcribe_via_sidecar(self, audio_path: str, *, language: Optional[str] = None) -> str:
        """旧 API,只回 text。新代码用 _transcribe_via_sidecar_with_diag。"""
        text, _diag = self._transcribe_via_sidecar_with_diag(
            audio_path, language=language,
        )
        return text

    def _transcribe_via_sidecar_with_diag(
        self, audio_path: str, *, language: Optional[str] = None,
    ) -> Tuple[str, list]:
        """Plan 3C: 走本地 whisper-server。

        步骤:
          1. 取 registry.get('asr') 当前 status
          2. 若 state != 'ready',触发 await start() (最多 30s)
          3. POST http://127.0.0.1:<port>/inference (multipart, file)
          4. 返 JSON text 字段
        sidecar 不可用任何环节抛 Exception,上层 fallback Python。

        除返 text 外,collect 详细诊断:HTTP 状态码 / 响应字节数 / body 预览 /
        异常类型,方便用户在"调用日志"面板看到 sidecar 实际返回。
        """
        import asyncio
        import httpx  # type: ignore

        diag: list[str] = []
        try:
            from chayuan.server.model_registry.local_runtime_registry import get_registry
            mgr = get_registry().get("asr")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"asr registry 取 manager 失败:{e}") from e

        # 确保 ready — 走 manager.ensure_ready,兼容 ready / stopped / failed / starting:
        #   - ready: no-op
        #   - failed (上次 mark_unhealthy 触发): 自动 stop + start 重启
        #   - stopped: 自动 start
        #   - starting/restarting: 等到 ready 或 30s
        if mgr.status.state != "ready":
            loop = asyncio.new_event_loop()
            try:
                status = loop.run_until_complete(asyncio.wait_for(mgr.ensure_ready(), timeout=60))
            finally:
                loop.close()
            if status.state != "ready":
                raise RuntimeError(
                    f"asr sidecar ensure_ready 失败 state={status.state} "
                    f"last_error={status.last_error}"
                )

        endpoint = mgr.status.endpoint or ""
        if not endpoint:
            raise RuntimeError("asr sidecar endpoint 空")

        url = f"{endpoint.rstrip('/')}/inference"
        data = {}
        if language:
            data["language"] = language

        # 转 16kHz mono wav — whisper.cpp 某些 build 不解 webm/opus,转 wav 解决
        wav_path, wav_cleanup, transcode_err = self._transcode_to_wav(audio_path)
        if wav_path is None:
            diag.append(f"[sidecar-pre] {transcode_err}(仍尝试直接喂原文件)")
            actual_path = audio_path
        else:
            if wav_path != audio_path:
                diag.append(f"[sidecar-pre] ffmpeg 转码 {Path(audio_path).suffix} → wav 16kHz mono OK")
            actual_path = wav_path

        file_size = os.path.getsize(actual_path)
        file_name = Path(actual_path).name
        logger.info(
            "[asr] sidecar POST %s file=%s size=%dB lang=%s",
            url, file_name, file_size, language or "auto",
        )
        try:
            with open(actual_path, "rb") as f:
                files = {"file": (file_name, f, "audio/wav" if actual_path.endswith(".wav") else "application/octet-stream")}
                # sidecar timeout 必须**对齐前端 transcribe 的 45s 预算**:
                #   - audio_pcm 硬上限 15s(asr_session.HARD_MAX_AUDIO_SECONDS)
                #   - whisper.cpp medium 在 CPU 上跑 15s 音频 ≈ 8-25s(看 CPU)
                #   - 旧 20s 太紧 — 任何慢一点的 CPU/碎片化负载都直接 ReadTimeout,
                #     即便后端马上能回也已经被砍 → 用户看到 "[session-transcribe]
                #     ReadTimeout: timed out" 反复刷屏。
                #   - 45s 给了 1.8× 的 CPU 退化裕度,且不超出前端等待预算
                #     (前端 timeoutMs=45_000,这里给 45s 让最坏情况下前端也能拿到结果)。
                with httpx.Client(timeout=45.0) as client:
                    resp = client.post(url, data=data, files=files)
        except httpx.ReadTimeout as e:
            # 单次推理超 45s 没回。**调 mark_unhealthy 累计** — 累计到阈值会把
            # sidecar 状态置 failed,下次 ensure_ready 自动 restart,无需用户手动操作。
            # 经定位:whisper-server 长跑被 stderr 写满 kernel pipe buffer 卡死是常见
            # 根因(local_runtime._start_pipe_drainers 已修),次根因是 GGML 内部死锁。
            # 自动 restart 是最稳的兜底。
            tail = mgr.recent_log_tail(20) if hasattr(mgr, "recent_log_tail") else ""
            triggered = (
                mgr.mark_unhealthy(reason=f"ReadTimeout: {e}")
                if hasattr(mgr, "mark_unhealthy") else False
            )
            diag.append(
                f"[sidecar] POST {url} ReadTimeout({e}) — sidecar 单次推理 >45s。"
                + (f"\n--- 最近日志(后 20 行) ---\n{tail}" if tail else "")
                + ("\n→ 累计达阈值,sidecar 已标记 failed,下次请求自动重启" if triggered
                   else "\n→ 累计未到阈值,本次只记录;持续出现会自动触发重启")
            )
            raise
        finally:
            if wav_cleanup is not None:
                try:
                    wav_cleanup.cleanup()
                except Exception:  # noqa: BLE001
                    pass
        body_preview = (resp.text or "")[:300]
        if resp.status_code != 200:
            diag.append(
                f"[sidecar] POST {url} → HTTP {resp.status_code}, "
                f"body={body_preview!r} (in={file_size}B file={file_name})"
            )
            raise RuntimeError(
                f"sidecar HTTP {resp.status_code}: {body_preview[:150]}"
            )
        try:
            payload = resp.json()
        except Exception as e:  # noqa: BLE001
            diag.append(
                f"[sidecar] POST {url} → 200 but invalid JSON: {body_preview!r} ({e})"
            )
            return "", diag
        raw_text = (payload.get("text") or "").strip()
        # 繁体 → 简体(whisper.cpp 默认偏繁体输出)
        raw_text = _to_simplified_chinese(raw_text)
        text, artifact_only = _strip_whisper_artifacts(raw_text)
        if artifact_only:
            # whisper 返了 [INAUDIBLE] / [BLANK_AUDIO] 等 marker → 这段录音无有效语音。
            # 常见原因:麦克风音量太低 / 离得远 / OS 选错输入设备 / 该 chunk 正好说话间隙。
            diag.append(
                f"[sidecar] whisper 只返回静音/噪声标记 {raw_text!r} — "
                f"通常是麦克风音量太低或没拾到声;离麦克风近点、调高 OS 输入音量、"
                f"或确认输入设备选对(in={file_size}B file={file_name})"
            )
            return "", diag
        if not text:
            # sidecar 通、HTTP 200、但 text 为空(已不在转码不通的场景下)。
            # 一般是音频过短(<1s)或全静音。
            diag.append(
                f"[sidecar] HTTP 200 但 text 为空 — 音频可能过短/全静音 "
                f"(in={file_size}B file={file_name} resp={body_preview!r})"
            )
            return "", diag
        logger.info("[asr] sidecar text len=%d preview=%r", len(text), text[:80])
        return text, diag

    # ==================================================================
    # TTS
    # ==================================================================

    def synthesize(self, text: str, *, voice: Optional[str] = None,
                    fmt: str = "mp3") -> bytes:
        """文本 → 音频 bytes。失败返回空 bytes。

        Fallback 链(**离线优先**):
          1. **piper-tts(纯本地 CPU,无网也工作)** — 首选,zh_CN-huayan-medium 默认中文女声
          2. edge-tts(微软云端,联网 + 不要 API key)
          3. OpenAI ``/audio/speech``(联网 + 要 API key)

        ``voice`` 不传 → 走 capability_router("tts");router 也无配置时 fallback
        "zh-CN-XiaoxiaoNeural"(edge-tts 风格默认名 — piper 收到这种风格名会
        自动改用 zh_CN-huayan-medium 默认音色)。
        """
        if not voice:
            try:
                from chayuan.server.capability_router import resolve_model
                voice = resolve_model("tts") or "zh-CN-XiaoxiaoNeural"
            except Exception:  # noqa: BLE001
                voice = "zh-CN-XiaoxiaoNeural"
        if not text:
            return b""
        # 0) piper-tts(离线优先)— 模型 ≤ 60 MB,装好后没网也能用
        piper_bytes = self._synthesize_via_piper(text, voice=voice, fmt=fmt)
        if piper_bytes:
            return piper_bytes
        # 1) edge-tts(联网,微软免费)
        try:
            import asyncio
            import edge_tts  # type: ignore
            async def _run() -> bytes:
                communicate = edge_tts.Communicate(text=text, voice=voice)
                buf = bytearray()
                async for chunk in communicate.stream():
                    if chunk.get("type") == "audio":
                        buf.extend(chunk["data"])
                return bytes(buf)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 已在 loop 内（路由中）：用 asyncio.run_coroutine_threadsafe 简单阻塞回本线程
                    import concurrent.futures
                    fut = asyncio.run_coroutine_threadsafe(_run(), loop)
                    return fut.result(timeout=30)
                return asyncio.run(_run())
            except RuntimeError:
                return asyncio.run(_run())
        except Exception as e:  # noqa: BLE001
            logger.debug("edge-tts 不可用：%r", e)
        # 2) OpenAI TTS
        try:
            from chayuan.server.utils import get_OpenAI
            client = get_OpenAI()
            resp = client.audio.speech.create(
                model="tts-1", voice="alloy", input=text, response_format=fmt,
            )
            # resp 支持 .content / iter_bytes
            data = getattr(resp, "content", None)
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
            if hasattr(resp, "iter_bytes"):
                return b"".join(resp.iter_bytes())
        except Exception as e:  # noqa: BLE001
            logger.warning("OpenAI TTS 失败：%r", e)
        return b""

    # ──────────────────────────────────────────────────────────────────
    # piper-tts 离线 TTS — synthesize() 的第 0 档
    # ──────────────────────────────────────────────────────────────────

    # piper voice 默认中文女声(参数量小、CPU 1× 实时、~60 MB)。huggingface
    # rhasspy/piper-voices 仓库下的标准命名,目录结构:
    # zh/zh_CN/huayan/medium/{zh_CN-huayan-medium.onnx, zh_CN-huayan-medium.onnx.json}
    _PIPER_DEFAULT_VOICE = "zh_CN-huayan-medium"

    def _synthesize_via_piper(
        self, text: str, *, voice: str, fmt: str,
    ) -> Optional[bytes]:
        """piper-tts 本地推理。任何环节失败/未配置返 None,由 caller fallback edge-tts。"""
        try:
            from piper.voice import PiperVoice  # type: ignore
        except Exception as e:  # noqa: BLE001
            logger.debug("[tts:piper] piper-tts 未装,跳过: %r", e)
            return None
        model_path = self._resolve_piper_voice_path(voice)
        if not model_path:
            return None
        # 实例缓存:PiperVoice.load 跑 ONNX 初始化 ~500ms-1s,反复合成时不能每次重建
        if not hasattr(self, "_piper_voice_cache"):
            self._piper_voice_cache: dict = {}
        pv = self._piper_voice_cache.get(model_path)
        if pv is None:
            try:
                pv = PiperVoice.load(model_path)
                self._piper_voice_cache[model_path] = pv
            except Exception as e:  # noqa: BLE001
                logger.warning("[tts:piper] PiperVoice.load 失败 %s: %r", model_path, e)
                return None
        # synthesize_wav 接 wave.Wave_write 对象;用内存 BytesIO 避免落盘
        import io
        import wave as _wave
        wav_io = io.BytesIO()
        try:
            with _wave.open(wav_io, "wb") as wf:
                pv.synthesize_wav(text, wf)
        except Exception as e:  # noqa: BLE001
            logger.warning("[tts:piper] synthesize_wav 失败: %r", e)
            return None
        wav_bytes = wav_io.getvalue()
        if not wav_bytes:
            return None
        if fmt == "wav":
            return wav_bytes
        # 转 mp3 / ogg via 已经在 deps 的 imageio-ffmpeg
        return self._wav_to_format(wav_bytes, fmt) or wav_bytes

    def _resolve_piper_voice_path(self, voice: str) -> Optional[str]:
        """voice 参数兼容多形态:
          - 绝对路径 ``/x/y/foo.onnx`` → 直接用
          - ``piper:zh_CN-huayan-medium`` 显式前缀 → 去前缀
          - edge-tts 风格(``zh-CN-XiaoxiaoNeural`` 等)或空 → 用默认 zh_CN-huayan-medium
          - 命名匹配 piper 风格 ``zh_CN-name-quality`` → 直接用

        查找顺序:
          1. **bundled**: <CHAYUAN_ROOT>/models/bundled/tts/piper/**/<name>.onnx
             (装机就有 — install-bundled-models.py 或安装包 seed 进来的;HF 保留
              源仓库 zh/zh_CN/huayan/medium/ 嵌套,所以递归找)
          2. **user-local 平铺**: <CHAYUAN_ROOT>/models/piper/<name>.onnx
             (用户手动放在这里覆盖打包版,或 auto-download 落地点)
          3. auto-download → user-local(默认开;CHAYUAN_PIPER_AUTO_DOWNLOAD=0 关)
          4. 默认 voice 也没 → 返 None,caller fallback edge-tts。
        """
        if voice and voice.endswith(".onnx") and os.path.isabs(voice):
            return voice if os.path.exists(voice) else None
        try:
            from chayuan.settings import CHAYUAN_ROOT
        except Exception:  # noqa: BLE001
            return None
        # 规整 voice name
        name = voice or ""
        if name.startswith("piper:"):
            name = name[len("piper:"):]
        if (not name) or "Neural" in name or "_" not in name:
            name = self._PIPER_DEFAULT_VOICE

        # 1) 装机 bundled 优先 — recurse 跳过 HF 嵌套
        bundled = self._find_bundled_piper_voice(name)
        if bundled:
            return bundled

        # 2) user-local 平铺(老版本 / 用户手动覆盖 / auto-download 落地)
        piper_dir = CHAYUAN_ROOT / "models" / "piper"
        candidate = piper_dir / f"{name}.onnx"
        cfg = piper_dir / f"{name}.onnx.json"
        if candidate.exists() and cfg.exists():
            return str(candidate)

        # 3) 尝试 auto-download(默认开,CHAYUAN_PIPER_AUTO_DOWNLOAD=0 关掉)
        if os.environ.get("CHAYUAN_PIPER_AUTO_DOWNLOAD", "1") != "0":
            ok = self._try_download_piper_voice(name, piper_dir)
            if ok and candidate.exists() and cfg.exists():
                return str(candidate)

        # 4) 兜底默认 voice
        if name != self._PIPER_DEFAULT_VOICE:
            default_bundled = self._find_bundled_piper_voice(self._PIPER_DEFAULT_VOICE)
            if default_bundled:
                return default_bundled
            default_user = piper_dir / f"{self._PIPER_DEFAULT_VOICE}.onnx"
            if default_user.exists():
                return str(default_user)

        logger.info(
            "[tts:piper] voice 模型未找到(期望 %s.onnx + .onnx.json)。"
            "装机版应在 %s/models/bundled/tts/piper/;用户自管在 %s。"
            "可手动下载或开 CHAYUAN_PIPER_AUTO_DOWNLOAD=1 自动拉。",
            name, CHAYUAN_ROOT, piper_dir,
        )
        return None

    def _find_bundled_piper_voice(self, name: str) -> Optional[str]:
        """在 <CHAYUAN_ROOT>/models/bundled/tts/piper/ 递归找 <name>.onnx + .onnx.json。

        HF snapshot_download 保留源仓库目录,所以打包版文件在:
          .../bundled/tts/piper/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx
        rglob 走 ≤几百 entries 不卡。.onnx 必须 .onnx.json 同目录 — piper 严格要 config。
        """
        try:
            from chayuan.settings import CHAYUAN_ROOT
            bundled_root = CHAYUAN_ROOT / "models" / "bundled" / "tts" / "piper"
            if not bundled_root.is_dir():
                return None
            target = f"{name}.onnx"
            for onnx in bundled_root.rglob(target):
                cfg = onnx.parent / f"{name}.onnx.json"
                if cfg.exists():
                    return str(onnx)
        except Exception as e:  # noqa: BLE001
            logger.debug("[tts:piper] bundled lookup 异常: %r", e)
        return None

    def _try_download_piper_voice(self, name: str, target_dir: Path) -> bool:
        """从 rhasspy/piper-voices HF 仓库一次性拉 voice 文件。
        成功返 True,任何错误返 False(caller 会 fallback edge-tts)。
        URL 模式: huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{locale}/{speaker}/{quality}/
        name 格式应为 ``{locale}-{speaker}-{quality}`` 例如 ``zh_CN-huayan-medium``。"""
        parts = name.split("-")
        if len(parts) < 3:
            logger.warning("[tts:piper] voice name 不符合 piper 命名: %s", name)
            return False
        locale, speaker, quality = parts[0], parts[1], parts[2]
        lang = locale.split("_")[0] if "_" in locale else locale
        base = (
            f"https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            f"{lang}/{locale}/{speaker}/{quality}"
        )
        files = [
            (f"{base}/{name}.onnx", target_dir / f"{name}.onnx"),
            (f"{base}/{name}.onnx.json", target_dir / f"{name}.onnx.json"),
        ]
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            import httpx
            logger.info("[tts:piper] 拉 voice 模型 %s ← %s ...", name, base)
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                for url, dest in files:
                    if dest.exists() and dest.stat().st_size > 0:
                        continue
                    resp = client.get(url)
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                    logger.info("[tts:piper]   ✓ %s (%d KB)", dest.name, len(resp.content) // 1024)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[tts:piper] 下载 %s 失败: %r — 若是离线环境,请手动从 "
                "https://huggingface.co/rhasspy/piper-voices 下载 %s.onnx + .json "
                "放到 %s,或设 CHAYUAN_PIPER_AUTO_DOWNLOAD=0 关闭自动下载尝试",
                name, e, name, target_dir,
            )
            return False

    def _wav_to_format(self, wav_bytes: bytes, fmt: str) -> Optional[bytes]:
        """piper 输出是 WAV;TTS 路由按 fmt(mp3/ogg/wav)设 Content-Type,
        所以非 wav 时要 ffmpeg 转。失败返 None,caller 兜底原 wav。"""
        if fmt == "wav":
            return wav_bytes
        ffmpeg = self._resolve_ffmpeg()
        if not ffmpeg:
            logger.debug("[tts:piper] 无 ffmpeg,不能从 WAV 转 %s — 直接返 WAV", fmt)
            return None
        import subprocess
        try:
            proc = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error",
                 "-f", "wav", "-i", "pipe:0", "-f", fmt, "pipe:1"],
                input=wav_bytes, capture_output=True, timeout=15,
            )
            if proc.returncode == 0 and proc.stdout:
                return proc.stdout
            logger.warning(
                "[tts:piper] ffmpeg WAV→%s rc=%d stderr=%s",
                fmt, proc.returncode,
                proc.stderr[:200].decode("utf-8", "replace"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[tts:piper] ffmpeg transcode 异常: %r", e)
        return None
