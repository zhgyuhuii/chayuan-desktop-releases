"""Single source of truth for desktop bundle version.

repo_root/VERSION holds the canonical 3-part SemVer (e.g. 1.0.0).
This script bumps the patch number, writes it back to VERSION, and syncs
the new value into:
  - chayuan-client/apps/desktop/src-tauri/tauri.conf.json    ("version": ...)
  - chayuan-server/libs/chayuan-server/pyproject.toml        (version = ...)

Called from build-desktop.{sh,ps1,cmd} BEFORE any build step, so every
packaged installer increments by 1.

Usage:
    python scripts/bump-version.py            # bump patch + sync
    python scripts/bump-version.py --no-bump  # only sync current to files
    python scripts/bump-version.py --set 1.2.3  # set explicit + sync
    python scripts/bump-version.py --print    # print current, no change

The output (single line "X.Y.Z") goes to stdout so caller scripts can
capture it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "VERSION"
TAURI_CONF = REPO_ROOT / "chayuan-client" / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
PYPROJECT = REPO_ROOT / "chayuan-server" / "libs" / "chayuan-server" / "pyproject.toml"


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def read_version() -> tuple[int, int, int]:
    if not VERSION_FILE.is_file():
        raise SystemExit(f"VERSION file missing: {VERSION_FILE}")
    raw = VERSION_FILE.read_text(encoding="utf-8").strip()
    m = _SEMVER_RE.match(raw)
    if not m:
        raise SystemExit(f"VERSION must be MAJOR.MINOR.PATCH, got {raw!r}")
    return int(m[1]), int(m[2]), int(m[3])


def write_version(major: int, minor: int, patch: int) -> str:
    text = f"{major}.{minor}.{patch}"
    VERSION_FILE.write_text(text + "\n", encoding="utf-8")
    return text


def sync_tauri(new_version: str) -> bool:
    if not TAURI_CONF.is_file():
        print(f"[bump-version] WARN tauri.conf.json missing: {TAURI_CONF}", file=sys.stderr)
        return False
    text = TAURI_CONF.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'("version"\s*:\s*")[^"]+(")',
        rf"\g<1>{new_version}\g<2>",
        text,
        count=1,
    )
    if n == 0:
        print(f"[bump-version] WARN no \"version\" key in {TAURI_CONF}", file=sys.stderr)
        return False
    if new_text != text:
        TAURI_CONF.write_text(new_text, encoding="utf-8")
    return True


def sync_pyproject(new_version: str) -> bool:
    if not PYPROJECT.is_file():
        print(f"[bump-version] WARN pyproject.toml missing: {PYPROJECT}", file=sys.stderr)
        return False
    text = PYPROJECT.read_text(encoding="utf-8")
    # match `version = "x.y.z"` or `version = "x.y.z.b"` -> rewrite to new 3-part
    new_text, n = re.subn(
        r'(^\s*version\s*=\s*")[^"]+(")',
        rf"\g<1>{new_version}\g<2>",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n == 0:
        print(f"[bump-version] WARN no `version = \"...\"` line in {PYPROJECT}", file=sys.stderr)
        return False
    if new_text != text:
        PYPROJECT.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-bump", action="store_true",
                    help="Only sync the current VERSION to downstream files, do not increment")
    ap.add_argument("--set", dest="set_to", default="",
                    help="Set VERSION to an explicit value (X.Y.Z), then sync. Implies no auto-bump.")
    ap.add_argument("--print", action="store_true",
                    help="Print current VERSION, do nothing else")
    args = ap.parse_args()

    cur_major, cur_minor, cur_patch = read_version()
    current = f"{cur_major}.{cur_minor}.{cur_patch}"

    if args.print:
        print(current)
        return 0

    if args.set_to:
        m = _SEMVER_RE.match(args.set_to.strip())
        if not m:
            raise SystemExit(f"--set requires MAJOR.MINOR.PATCH, got {args.set_to!r}")
        new = write_version(int(m[1]), int(m[2]), int(m[3]))
        action = f"set {current} -> {new}"
    elif args.no_bump:
        new = current
        action = f"sync {current} (no bump)"
    else:
        new = write_version(cur_major, cur_minor, cur_patch + 1)
        action = f"bump {current} -> {new}"

    ok_tauri = sync_tauri(new)
    ok_py = sync_pyproject(new)

    msg = f"[bump-version] {action}"
    msg += f"  tauri={'OK' if ok_tauri else 'SKIP'}"
    msg += f"  pyproject={'OK' if ok_py else 'SKIP'}"
    print(msg, file=sys.stderr)
    print(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
