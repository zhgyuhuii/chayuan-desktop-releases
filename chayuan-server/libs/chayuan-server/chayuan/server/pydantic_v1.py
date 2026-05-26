try:
    from langchain_core.pydantic_v1 import *  # type: ignore[F403]
except Exception:
    from pydantic import *  # type: ignore[F403]
import warnings
from pydantic.fields import FieldInfo
try:
    from pydantic.v1.schema import model_schema
except Exception:
    def model_schema(model, *args, **kwargs):
        return model.model_json_schema(*args, **kwargs)
import typing as typing

# Pydantic v2 keeps root_validator as deprecated API but enforces
# skip_on_failure=True for post validators. Preserve v1-style defaults.
_root_validator = root_validator
def root_validator(*args, **kwargs):
    kwargs.pop("allow_reuse", None)
    if "pre" not in kwargs or kwargs.get("pre") is False:
        kwargs.setdefault("skip_on_failure", True)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Pydantic V1 style `@root_validator` validators are deprecated.*",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="`allow_reuse` is deprecated and will be ignored.*",
            category=DeprecationWarning,
        )
        return _root_validator(*args, **kwargs)
