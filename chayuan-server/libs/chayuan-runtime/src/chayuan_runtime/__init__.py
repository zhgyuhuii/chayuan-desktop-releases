"""Runtime adapters: a thin layer that maps a registered model to the
sub-process backend that should serve it (Ollama / Infinity / vLLM / ...).
"""
from chayuan_runtime.base import (
    AdapterCapabilities,
    AdapterRequest,
    AdapterResponse,
    RuntimeAdapter,
)
from chayuan_runtime.framework_wiring import (
    install_into_lifecycle,
    wire,
)
from chayuan_runtime.registry import (
    AdapterRegistry,
    get_registry,
    pick_adapter,
)

__all__ = [
    "AdapterCapabilities",
    "AdapterRegistry",
    "AdapterRequest",
    "AdapterResponse",
    "RuntimeAdapter",
    "get_registry",
    "install_into_lifecycle",
    "pick_adapter",
    "wire",
]
