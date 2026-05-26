"""Five-level model identification.

Order of precedence:
  L1. config.json `architectures` → mapped via signatures
  L2. model_index.json (diffusers)
  L3. characteristic files / suffixes (gguf, ckpt, onnx, paddle)
  L4. README.md / model_card.json `pipeline_tag`
  L5. Path-segment fallback: parent folder name == known category
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chayuan_identify.rules import RuleSet, get_default_ruleset
from chayuan_identify.signatures import Signature

KNOWN_CATEGORIES = {
    "chat", "embedding", "rerank", "clip", "t2i", "t2v", "tts", "asr", "ocr",
}


@dataclass
class ModelMeta:
    repo: str
    name: str
    category: str
    runtime: str
    format: str
    quantization: str = ""
    path: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    matched_rule: str = ""
    confidence: int = 0  # 1..5 → higher = stronger evidence

    def to_payload(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "name": self.name,
            "category": self.category,
            "runtime": self.runtime,
            "format": self.format,
            "quantization": self.quantization,
            "path": self.path,
            "capabilities": self.capabilities,
            "extra": {"matched_rule": self.matched_rule, "confidence": self.confidence},
        }


# ---- helpers ---------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _walk_files(d: Path, max_files: int = 500) -> list[Path]:
    out: list[Path] = []
    if not d.is_dir():
        return out
    for p in d.rglob("*"):
        if p.is_file():
            out.append(p)
            if len(out) >= max_files:
                break
    return out


def _path_to_repo(d: Path, models_root: Path | None = None) -> str:
    """`<root>/chat/Qwen--Qwen2.5-3B-Instruct` → `Qwen/Qwen2.5-3B-Instruct`."""
    name = d.name
    if "--" in name:
        return name.replace("--", "/", 1)
    if models_root:
        try:
            rel = d.relative_to(models_root)
            return rel.as_posix()
        except ValueError:
            pass
    return name


def _quant_from_filename(fname: str) -> str:
    fname = fname.lower()
    for q in ("q2_k", "q3_k_s", "q3_k_m", "q3_k_l", "q4_0", "q4_1", "q4_k_s", "q4_k_m",
             "q5_k_s", "q5_k_m", "q5_0", "q6_k", "q8_0", "f16", "f32", "int8", "int4"):
        if q in fname:
            return q.upper()
    return ""


# ---- matchers --------------------------------------------------------------


def _match_signature(sig: Signature, *, files: list[Path], arches: list[str], pipeline_tag: str) -> bool:
    fname_set = {p.name.lower() for p in files}
    suffix_set = {p.suffix.lower() for p in files}
    if sig.files and not any(f in fname_set for f in sig.files):
        return False
    if sig.file_suffix and not any(s in suffix_set or any(p.name.lower().endswith(s) for p in files) for s in sig.file_suffix):
        return False
    if sig.config_arch and not any(any(t in a for a in arches) for t in sig.config_arch):
        return False
    if sig.pipeline_tag and pipeline_tag not in sig.pipeline_tag:
        return False
    if not (sig.files or sig.file_suffix or sig.config_arch or sig.pipeline_tag):
        return False
    return True


def _read_pipeline_tag(model_dir: Path) -> str:
    for cand in ("model_card.json", "README.md", "readme.md"):
        f = model_dir / cand
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore").lower()
        for tag in ("text-generation", "feature-extraction", "sentence-similarity",
                    "text-classification", "automatic-speech-recognition",
                    "text-to-speech", "text-to-image", "text-to-video",
                    "image-classification", "object-detection"):
            if tag in text:
                return tag
    return ""


def _largest_file_size(files: list[Path]) -> int:
    return max((p.stat().st_size for p in files if p.is_file()), default=0)


# ---- public API ------------------------------------------------------------


def identify_dir(model_dir: Path, ruleset: RuleSet | None = None,
                 models_root: Path | None = None) -> ModelMeta | None:
    """Inspect a directory and produce ModelMeta. Returns None if nothing matches."""
    rs = ruleset or get_default_ruleset()
    if not model_dir.is_dir():
        return None
    files = _walk_files(model_dir)
    if not files:
        return None
    arches: list[str] = []
    cfg = _read_json(model_dir / "config.json")
    if cfg:
        arches.extend(cfg.get("architectures", []) or [])
        if model_type := cfg.get("model_type"):
            arches.append(model_type)
    diffusers_marker = (model_dir / "model_index.json").is_file()
    pipeline_tag = _read_pipeline_tag(model_dir)
    repo = _path_to_repo(model_dir, models_root)
    name = repo.split("/")[-1]

    quant = ""
    for p in files:
        q = _quant_from_filename(p.name)
        if q:
            quant = q
            break

    # Try every signature in order; first match wins, but track confidence.
    chosen: Signature | None = None
    confidence = 0
    for sig in rs.all():
        if _match_signature(sig, files=files, arches=arches, pipeline_tag=pipeline_tag):
            # Confidence heuristic
            c = 0
            if sig.config_arch and arches:
                c = max(c, 5)
            if sig.files and (model_dir / sig.files[0]).is_file():
                c = max(c, 4)
            if sig.file_suffix:
                c = max(c, 3)
            if sig.pipeline_tag and pipeline_tag:
                c = max(c, 2)
            if c > confidence:
                chosen = sig
                confidence = c

    if chosen is None and diffusers_marker:
        chosen = next((s for s in rs.all() if s.name == "diffusers-t2i"), None)
        confidence = 4

    if chosen is None:
        # L5: path fallback (parent folder is a known category)
        parent = model_dir.parent.name.lower()
        if parent in KNOWN_CATEGORIES:
            chosen = Signature(
                name="path-fallback",
                category=parent,
                runtime="auto",
                format="unknown",
            )
            confidence = 1

    if chosen is None:
        return None

    capabilities = {chosen.category: True}
    return ModelMeta(
        repo=repo,
        name=name,
        category=chosen.category,
        runtime=chosen.runtime,
        format=chosen.format,
        quantization=quant,
        path=str(model_dir),
        capabilities=capabilities,
        matched_rule=chosen.name,
        confidence=confidence,
    )


def identify(path: Path | str, ruleset: RuleSet | None = None,
             models_root: Path | None = None) -> ModelMeta | None:
    """Public entry point."""
    return identify_dir(Path(path), ruleset, models_root)


def main() -> None:  # pragma: no cover
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(prog="python -m chayuan_identify")
    ap.add_argument("path")
    args = ap.parse_args()
    m = identify(args.path)
    if m is None:
        print(_json.dumps({"matched": False, "path": args.path}, ensure_ascii=False))
    else:
        print(_json.dumps({"matched": True, **m.to_payload()}, indent=2, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    main()
