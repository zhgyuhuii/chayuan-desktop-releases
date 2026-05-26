"""桌面版「轻量版 / 全量版」打包清单的**单一真源**。

谁用:
  * ``chayuan-server/packaging/pyinstaller/build.py``
      sync_bundled_models(lite=True)  → 只拷 LITE_CAPS 列出的 cap 目录
      sync_bundled_models(lite=False) → 拷 FULL_CAPS 全部
      sync 时还按 CANONICAL_MODEL_SUBDIRS 过滤,只拷"当前规范的模型子目录"
      (历史遗留的同 cap 内其它子目录被忽略)
  * ``scripts/install-bundled-models.py``
      ``--lite`` flag → 只跑 LITE_CAPS 子集
      ``--clean-stale`` → 删除 cap 目录里非 CANONICAL_MODEL_SUBDIRS 的子目录

谁不用:
  * ``chayuan_packaging`` 包(``--release lite/standard/pro``)是另一套 layout.yaml
    驱动的 release matrix,跟桌面打包链路独立,不复用本清单。

设计:
  * "lite installer" ≈ ~0.6 GB 安装包,自带:文本嵌入 + 重排 + 语音识别 + OCR;
    对话(chat) 由用户运行时按需下载(或对接云端 LLM),图像嵌入同理。
  * "full installer" ≈ ~3.3 GB,全 6 类能力本地化。
  * embedding 用 GGUF Q5_K_M、rerank 用 Q4_K_M(2026-05 从 Q8_0 下调,
    省 ~365 MB;量化档位真源在 scripts/install-bundled-models.py 的 MANIFEST)。
  * cap 名称跟 ``install-bundled-models.py`` 的 MANIFEST key 一致:
    chat / embedding / rerank / asr / ocr / image (=image-embedding)。

未来调整(比如把 image-embedding 也归 lite)只改这里,build.py 和
install-bundled-models 都跟着走。
"""
from __future__ import annotations

#: 轻量版安装包随包嵌入的 capability(磁盘 ~ 625 MB)
LITE_CAPS: tuple[str, ...] = (
    "embedding",
    "rerank",
    "asr",
    "ocr",
    "tts",   # piper zh_CN-huayan-medium ~60 MB,离线 CPU TTS,装机即用
)

#: 全量版安装包随包嵌入的 capability(磁盘 ~ 3.1 GB,含 chat 主力模型)
FULL_CAPS: tuple[str, ...] = LITE_CAPS + (
    "chat",
    "image",          # 图像嵌入(image-embedding 的短名,跟 install-bundled-models MANIFEST 一致)
)

#: 集成清单 alias 表,便于按 flavor 字符串拿对应清单
FLAVOR_CAPS: dict[str, tuple[str, ...]] = {
    "lite": LITE_CAPS,
    "full": FULL_CAPS,
}


# ────────────────────────────────────────────────────────────────────
# 每个 cap 在 vendor/bundled_models/<cap>/ 下"当前规范"的模型子目录名。
# 历史背景:install-bundled-models.py 的 MANIFEST 演化过几次(例如 embedding
# 从 ``gte-multilingual-base`` HF transformers 改成 ``bge-m3`` GGUF Q8;
# rerank 也从 ``gte-multilingual-reranker-base`` 改成 ``bge-reranker-v2-m3``)。
# 老用户 vendor/ 目录里可能有遗留旧子目录,sync_bundled_models / installer
# 不应该把它们打进去 — 只认下面 CANONICAL_MODEL_SUBDIRS 这份白名单。
#
# 每个值是 tuple[str, ...] 而不是 str,留余地给多变体共存(例如 rerank 可同时
# 接受 bge-reranker-v2-m3 和 gte-multilingual-reranker-base-GGUF)。
# ────────────────────────────────────────────────────────────────────
CANONICAL_MODEL_SUBDIRS: dict[str, tuple[str, ...]] = {
    "chat":      ("Qwen3-4B-Instruct-2507-GGUF",),
    # 2026-05-18:换成 bge-m3 GGUF(llama.cpp embedding 只吃 GGUF)。
    # 旧 gte-multilingual-base 是 HF transformers,llama-server 拒绝加载。
    "embedding": ("bge-m3",),
    "rerank":    ("bge-reranker-v2-m3", "gte-multilingual-reranker-base"),
    "asr":       ("whisper.cpp",),
    "ocr":       ("RapidOCR",),
    "tts":       ("piper",),
    "image":     ("clip-vit-base-patch32",),
}


def canonical_subdirs_for(cap: str) -> tuple[str, ...]:
    """返回 cap 下"当前规范"的子目录名 tuple,未知 cap 返回空 tuple
    (此时调用方应按"全收"语义处理 — 不破坏旧调用兼容)。"""
    return CANONICAL_MODEL_SUBDIRS.get(cap, ())


def is_canonical_subdir(cap: str, subdir_name: str) -> bool:
    """子目录名是否属于 cap 的规范列表;cap 未在表里时一律 True
    (open-by-default,避免 cap_manifest 没更新就阻塞 sync)。"""
    canon = canonical_subdirs_for(cap)
    if not canon:
        return True
    return subdir_name in canon


def caps_for(flavor: str) -> tuple[str, ...]:
    """`flavor` ∈ {'lite','full'};未知值抛 ValueError。"""
    if flavor not in FLAVOR_CAPS:
        raise ValueError(
            f"unknown flavor {flavor!r}; expected one of {sorted(FLAVOR_CAPS)}"
        )
    return FLAVOR_CAPS[flavor]


__all__ = [
    "LITE_CAPS",
    "FULL_CAPS",
    "FLAVOR_CAPS",
    "CANONICAL_MODEL_SUBDIRS",
    "canonical_subdirs_for",
    "is_canonical_subdir",
    "caps_for",
]
