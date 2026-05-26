"""视频：抽帧 + Vision 描述 + ASR 字幕 组合理解。

策略：
1. 用 ``ffmpeg`` 按固定间隔抽帧（默认每 8 秒 1 张）；无 ffmpeg 则回退到 opencv
2. 抽出轨道上的音频，丢给 :class:`AudioPipeline.transcribe`
3. 每张帧压到 1024px 后 base64，喂给 Vision 模型（OpenAI content-list 格式）
4. 组装："ASR 字幕 + 每帧描述" 成一个结构化 markdown，返回给 LLM 当 context

依赖：``ffmpeg-python`` 或系统 ffmpeg，可选 ``opencv-python``；都不装时只返回 ASR
"""
from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from chayuan.server.modality.audio import AudioPipeline

logger = logging.getLogger("chayuan.modality.video")


class VideoPipeline:

    def __init__(self, audio: Optional[AudioPipeline] = None):
        self.audio = audio or AudioPipeline()

    # -----------------------------------------------------------------
    # Frame sampling
    # -----------------------------------------------------------------

    @staticmethod
    def _has_ffmpeg() -> bool:
        return shutil.which("ffmpeg") is not None

    @staticmethod
    def _sample_frames_ffmpeg(video_path: str, out_dir: str,
                               every_sec: int = 8, max_frames: int = 24) -> List[str]:
        pattern = str(Path(out_dir) / "frame_%04d.jpg")
        # fps=1/every_sec；scale 限长边 1024
        cmd = [
            "ffmpeg", "-loglevel", "error", "-y", "-i", video_path,
            "-vf", f"fps=1/{int(every_sec)},scale='min(1024,iw)':-1",
            "-frames:v", str(max_frames), pattern,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return sorted(str(p) for p in Path(out_dir).glob("frame_*.jpg"))

    @staticmethod
    def _extract_audio_ffmpeg(video_path: str, out_path: str) -> bool:
        try:
            cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", video_path,
                   "-vn", "-ac", "1", "-ar", "16000", out_path]
            subprocess.run(cmd, check=True, capture_output=True)
            return os.path.exists(out_path) and os.path.getsize(out_path) > 0
        except Exception as e:  # noqa: BLE001
            logger.debug("ffmpeg audio extract 失败：%r", e)
            return False

    @staticmethod
    def _sample_frames_cv2(video_path: str, out_dir: str,
                            every_sec: int = 8, max_frames: int = 24) -> List[str]:
        try:
            import cv2  # type: ignore
        except Exception:
            return []
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        step = int(max(1, fps * every_sec))
        idx = 0
        saved = 0
        paths: List[str] = []
        while saved < max_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                break
            p = str(Path(out_dir) / f"frame_{saved:04d}.jpg")
            cv2.imwrite(p, frame)
            paths.append(p)
            saved += 1
            idx += step
        cap.release()
        return paths

    # -----------------------------------------------------------------
    # 理解 pipeline
    # -----------------------------------------------------------------

    def understand(self, video: Any, *, query: str = "",
                    every_sec: int = 8, max_frames: int = 16,
                    vision_model: Optional[str] = None) -> Dict[str, Any]:
        """对一段视频做"抽帧 + ASR + 组合理解"。

        返回：``{"asr": str, "frames": [{"path": ..., "caption": ...}], "summary": str}``
        """
        # 1) 物料落地
        try:
            path = _materialize_video(video)
        except Exception as e:  # noqa: BLE001
            return {"error": f"materialize: {e!r}"}

        tmpdir = tempfile.TemporaryDirectory(prefix="chayuan_video_")
        try:
            frames: List[str] = []
            audio_text = ""
            if self._has_ffmpeg():
                try:
                    frames = self._sample_frames_ffmpeg(path, tmpdir.name, every_sec, max_frames)
                except Exception as e:  # noqa: BLE001
                    logger.debug("ffmpeg frame sampling 失败：%r", e)
                audio_path = str(Path(tmpdir.name) / "audio.wav")
                if self._extract_audio_ffmpeg(path, audio_path):
                    audio_text = self.audio.transcribe(audio_path) or ""
            if not frames:
                frames = self._sample_frames_cv2(path, tmpdir.name, every_sec, max_frames)

            # 2) 逐帧问 vision
            frame_items: List[Dict[str, str]] = []
            for p in frames:
                try:
                    caption = _caption_frame(p, query=query, model=vision_model)
                except Exception as e:  # noqa: BLE001
                    caption = f"(caption failed: {e!r})"
                frame_items.append({"path": p, "caption": caption})

            # 3) Summary：拼 ASR + 所有 caption → 再问一次 LLM
            summary = _summarize(
                query=query, asr=audio_text,
                frame_captions=[it["caption"] for it in frame_items],
                model=vision_model,
            )
            return {"asr": audio_text, "frames": frame_items, "summary": summary}
        finally:
            try:
                tmpdir.cleanup()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _materialize_video(video: Any) -> str:
    if isinstance(video, str):
        if video.startswith(("http://", "https://")):
            import urllib.request
            td = tempfile.mkdtemp(prefix="chayuan_video_")
            p = os.path.join(td, "input.mp4")
            with urllib.request.urlopen(video, timeout=60) as r:
                Path(p).write_bytes(r.read())
            return p
        if os.path.exists(video):
            return video
    if isinstance(video, (bytes, bytearray)):
        td = tempfile.mkdtemp(prefix="chayuan_video_")
        p = os.path.join(td, "input.mp4")
        Path(p).write_bytes(bytes(video))
        return p
    raise ValueError("unsupported video type")


def _caption_frame(image_path: str, *, query: str = "",
                    model: Optional[str] = None) -> str:
    """调 Vision 模型对一帧出描述。用 content-list 格式喂。"""
    try:
        from chayuan.server.utils import get_ChatOpenAI, get_default_llm
        data = Path(image_path).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        m = (model or get_default_llm()).strip()
        llm = get_ChatOpenAI(model_name=m, temperature=0.0, streaming=False)
        prompt = (query or "描述这一帧的关键内容；一句话；若包含文字请直接转录。")[:400]
        msg = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }]
        resp = llm.invoke(msg)
        return (getattr(resp, "content", None) or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("caption frame 失败：%r", e)
        return ""


def _summarize(*, query: str, asr: str, frame_captions: List[str],
                model: Optional[str]) -> str:
    try:
        from chayuan.server.utils import get_ChatOpenAI, get_default_llm
        m = (model or get_default_llm()).strip()
        llm = get_ChatOpenAI(model_name=m, temperature=0.2, streaming=False)
        bullet = "\n".join(f"- f{i+1}: {c}" for i, c in enumerate(frame_captions) if c)
        user = (
            f"【用户问题】{query or '请综合总结视频主旨'}\n\n"
            f"【语音转写】\n{asr[:3000] or '(无)'}\n\n"
            f"【关键帧描述】\n{bullet[:5000] or '(无)'}\n\n"
            "请输出 300 字以内的综合回答；引用时用 f1/f2 等帧编号。"
        )
        resp = llm.invoke([{"role": "user", "content": user}])
        return (getattr(resp, "content", None) or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("video summarize 失败：%r", e)
        return ""
