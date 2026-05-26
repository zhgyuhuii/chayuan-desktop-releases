"""Cross-platform installer build pipeline.

Workflow:
    scan(workspace)  → manifest of every drop-in component (vendor + models)
    filter(manifest, release)  → keep only components allowed by lite/standard/pro
    verify(manifest)  → sha256 + license sanity check
    bundle(manifest, target)  → produce platform-specific artifact

The point of having scan() be drop-in-aware is that **dev never has to
update a YAML** to add a new model: just put files into models/<cat>/, run
`chayuan-pack build`, and the manifest reflects reality.
"""
from chayuan_packager.bundle import bundle
from chayuan_packager.cli import main
from chayuan_packager.filter import RELEASE_PRESETS, filter_manifest
from chayuan_packager.scan import Component, ScanManifest, scan
from chayuan_packager.verify import verify_manifest

__all__ = [
    "Component",
    "RELEASE_PRESETS",
    "ScanManifest",
    "bundle",
    "filter_manifest",
    "main",
    "scan",
    "verify_manifest",
]
