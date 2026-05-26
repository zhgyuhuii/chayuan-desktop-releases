"""Model lifecycle: download / import / verify / mirror routing."""
from chayuan_modelmgr.downloader import DownloadOptions, ModelDownloader, pull
from chayuan_modelmgr.importer import import_model
from chayuan_modelmgr.lifecycle import (
    Lifecycle,
    LifecycleStore,
    Stage,
    StageEvent,
    WireCallable,
    WireOutcome,
    get_lifecycle,
    register_wire_impl,
)
from chayuan_modelmgr.mirrors import (
    MIRRORS,
    Mirror,
    SpeedtestResult,
    modelscope_repo_id,
    pick_fastest_mirror,
    resolve_mirror,
    set_mirror,
    speedtest_mirrors,
)
from chayuan_modelmgr.progress import ProgressEvent, ProgressSink
from chayuan_modelmgr.recommended import (
    RecommendedModel,
    get_default_for_capability,
    get_recommended,
    list_capabilities,
)
from chayuan_modelmgr.verifier import sha256_of_file, verify_directory, write_manifest

__all__ = [
    "DownloadOptions",
    "Lifecycle",
    "LifecycleStore",
    "MIRRORS",
    "Mirror",
    "ModelDownloader",
    "ProgressEvent",
    "ProgressSink",
    "RecommendedModel",
    "SpeedtestResult",
    "Stage",
    "StageEvent",
    "WireCallable",
    "WireOutcome",
    "get_default_for_capability",
    "get_lifecycle",
    "get_recommended",
    "import_model",
    "list_capabilities",
    "modelscope_repo_id",
    "pick_fastest_mirror",
    "pull",
    "register_wire_impl",
    "resolve_mirror",
    "set_mirror",
    "speedtest_mirrors",
    "sha256_of_file",
    "verify_directory",
    "write_manifest",
]
