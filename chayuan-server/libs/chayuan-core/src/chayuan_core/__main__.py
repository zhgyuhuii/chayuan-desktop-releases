"""`python -m chayuan_core` -> human-readable platform/paths report."""
from __future__ import annotations

import json

from chayuan_core.paths import CHAYUAN_HOME, get_paths
from chayuan_core.platform_info import get_platform_info


def main() -> None:
    info = get_platform_info().to_dict()
    paths = get_paths()
    out = {
        "chayuan_home": str(CHAYUAN_HOME),
        "paths": {k: str(v) for k, v in vars(paths).items()},
        "platform": info,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
