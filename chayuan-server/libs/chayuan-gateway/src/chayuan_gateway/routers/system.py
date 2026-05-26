from __future__ import annotations

from fastapi import APIRouter, Query

from chayuan_core import get_paths, get_platform_info, load_config
from chayuan_supervisor import get_runtime_info

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/v1/system/info")
def info():
    p = get_paths()
    return {
        "platform": get_platform_info().to_dict(),
        "paths": {k: str(v) for k, v in vars(p).items()},
        "config": load_config().model_dump(),
    }


def _mask_url(url: str, password: str) -> str:
    if not password or not url:
        return url
    return url.replace(password, "****")


@router.get("/v1/system/services")
def services(reveal: bool = Query(False, description="Include passwords in plaintext.")):
    """Final connection info for every embedded service.

    Without `?reveal=true` passwords (and the corresponding URL component) are
    masked for safe display in the UI.
    """
    eps = get_runtime_info().all_endpoints()
    if not reveal:
        masked: dict[str, dict] = {}
        for k, ep in eps.items():
            if ep.get("password"):
                copy = dict(ep)
                copy["url"] = _mask_url(ep.get("url", ""), ep["password"])
                copy["password"] = "****"
                masked[k] = copy
            else:
                masked[k] = ep
        eps = masked
    return {"object": "list", "data": [{"name": k, **v} for k, v in eps.items()]}
