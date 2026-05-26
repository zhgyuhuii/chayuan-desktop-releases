"""Adapter registry — discovers adapters via entry_points or built-in list."""
from __future__ import annotations

import importlib
import importlib.metadata as md
import threading
from typing import Iterable

from chayuan_registry.models import Model
from chayuan_runtime.base import RuntimeAdapter


_BUILTIN: tuple[tuple[str, str], ...] = (
    ("ollama", "chayuan_runtime.adapters.ollama_adapter:OllamaAdapter"),
    ("infinity", "chayuan_runtime.adapters.infinity_adapter:InfinityAdapter"),
    ("llamacpp", "chayuan_runtime.adapters.llamacpp_adapter:LlamaCppAdapter"),
    ("vllm", "chayuan_runtime.adapters.vllm_adapter:VllmAdapter"),
    ("whispercpp", "chayuan_runtime.adapters.whispercpp_adapter:WhisperCppAdapter"),
    ("funasr", "chayuan_runtime.adapters.funasr_adapter:FunAsrAdapter"),
    ("piper", "chayuan_runtime.adapters.piper_adapter:PiperAdapter"),
    ("cosyvoice", "chayuan_runtime.adapters.cosyvoice_adapter:CosyVoiceAdapter"),
    ("voxcpm2", "chayuan_runtime.adapters.voxcpm2_adapter:VoxCpm2Adapter"),
    ("rapidocr", "chayuan_runtime.adapters.rapidocr_adapter:RapidOcrAdapter"),
    ("paddleocr", "chayuan_runtime.adapters.paddleocr_adapter:PaddleOcrAdapter"),
    ("comfyui", "chayuan_runtime.adapters.comfyui_adapter:ComfyUIAdapter"),
)


def _import(spec: str):
    mod, _, attr = spec.partition(":")
    m = importlib.import_module(mod)
    return getattr(m, attr)


class AdapterRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapters: dict[str, RuntimeAdapter] = {}
        self._loaded = False

    def load(self, mock: bool = True) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            # entry_points (preferred — extensible by 3rd-party plugins)
            try:
                eps = md.entry_points(group="chayuan.runtime_adapters")
            except Exception:
                eps = ()
            for ep in eps:
                try:
                    cls = ep.load()
                    self._adapters[ep.name] = cls(mock=mock)
                except Exception:
                    pass
            # built-in fallback (covers dev mode where the workspace isn't installed)
            for name, spec in _BUILTIN:
                if name in self._adapters:
                    continue
                try:
                    cls = _import(spec)
                    self._adapters[name] = cls(mock=mock)
                except Exception:
                    continue
            self._loaded = True

    def all(self) -> Iterable[RuntimeAdapter]:
        self.load()
        return list(self._adapters.values())

    def by_name(self, name: str) -> RuntimeAdapter | None:
        self.load()
        return self._adapters.get(name)

    def register(self, adapter: RuntimeAdapter, *, replace: bool = True) -> None:
        """注册一个 adapter 实例（覆盖 / 新增）。

        给 smoke / 测试 / 第三方插件用：内建只在 ``load()`` 时建一次实例，
        如果你想在跑时把 ``mock=True`` 换成 ``mock=False``，或者注册一个
        自定义路由的 adapter，调用这个方法。
        """
        self.load()
        with self._lock:
            if not replace and adapter.name in self._adapters:
                return
            self._adapters[adapter.name] = adapter

    def pick(self, model: Model) -> RuntimeAdapter | None:
        self.load()
        # exact runtime match wins
        if (a := self._adapters.get(model.runtime)) is not None and a.supports(model):
            return a
        # category-based fallback
        for a in self._adapters.values():
            if model.category in a.capabilities.categories and a.supports(model):
                return a
        return None


_REGISTRY: AdapterRegistry | None = None
_LOCK = threading.Lock()


def get_registry(mock: bool = True) -> AdapterRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        with _LOCK:
            if _REGISTRY is None:
                _REGISTRY = AdapterRegistry()
                _REGISTRY.load(mock=mock)
    return _REGISTRY


def pick_adapter(model: Model, mock: bool = True) -> RuntimeAdapter | None:
    return get_registry(mock=mock).pick(model)
