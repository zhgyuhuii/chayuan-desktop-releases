"""4 个 runtime HTTP wrapper(funasr / cosyvoice / rapidocr / paddleocr)的共享基类。

为什么需要这层 wrapper:
    - FunASR / CosyVoice / RapidOCR / PaddleOCR 都是 pip 包,**没有 CLI 二进制**,
      不能像 ollama 那样 `nohup ollama serve` 后台跑;
    - 但 chayuan-runtime 的 adapter 期望它们以 HTTP 形式存在(127.0.0.1:18180 等);
    - 所以统一做一层薄 FastAPI wrapper:
        `python -m chayuan.server.modality.funasr_server --host 127.0.0.1 --port 18180`

配置文件:
    ``<CHAYUAN_ROOT>/runtime/<framework>.yaml`` 首次启动自动生成默认配置;
    安装弹窗里的"配置编辑器"会读写该文件,保存后通过停->启 daemon 立即重启生效。
    路径由 :func:`chayuan.paths.runtime_config_dir` 统一解析,会:
      * 优先使用 ``$CHAYUAN_RUNTIME_HOME`` 环境变量(显式)
      * 退回 ``<CHAYUAN_ROOT>/runtime/``(默认)
      * 首次启动自动从老路径 ``~/.chayuan/runtime/`` 一次性迁移 yaml(兼容 1.x)

设计:
    - **懒加载**:启动时不加载任何模型(否则秒级启动变成几十秒),首次请求时才加载
    - **fail-soft**:库未安装时 /health 仍返回 200,但 ready=False + error 字段告知
    - **/v1/models** 兼容 OpenAI 端点格式,方便其他探活脚本通用
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("chayuan.modality.runtime_server")


def runtime_config_dir() -> Path:
    """统一的 runtime 配置目录 — 委托给 :mod:`chayuan.paths`。

    优先级:`CHAYUAN_RUNTIME_HOME` 环境变量 > `<CHAYUAN_ROOT>/runtime/`。
    1.x 时期 ``~/.chayuan/runtime/`` 里的 yaml 会被一次性自动迁移过来。
    """
    from chayuan.paths import runtime_config_dir as _rt_dir

    return _rt_dir()


def runtime_config_path(framework: str) -> Path:
    """指定 framework 的配置文件绝对路径。"""
    return runtime_config_dir() / f"{framework}.yaml"


def load_or_init_config(framework: str, default_config: Dict[str, Any]) -> Dict[str, Any]:
    """读配置;不存在则用 ``default_config`` 写一份带注释的默认 yaml。

    返回 dict 副本,后续运行时如需修改 in-memory 配置,改副本不会脏文件。
    """
    import yaml  # 延迟导入,wrapper 模块装载更轻量

    cfg_path = runtime_config_path(framework)
    if not cfg_path.exists():
        try:
            cfg_path.write_text(
                "# 该文件由 chayuan 自动生成,可在 UI 里编辑;保存后立即重启 daemon 生效\n"
                + yaml.safe_dump(default_config, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            logger.info("[%s] wrote default config: %s", framework, cfg_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] write default config failed: %r", framework, e)
        return dict(default_config)
    try:
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        # 用默认值兜底缺失字段
        merged = dict(default_config)
        if isinstance(loaded, dict):
            merged.update(loaded)
        return merged
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] load config failed (%r),fallback to default", framework, e)
        return dict(default_config)


def make_runtime_app(
    *,
    framework: str,
    title: str,
    default_config: Dict[str, Any],
    register_routes: Callable[[Any, Dict[str, Any]], None],
):
    """通用 runtime HTTP wrapper 工厂。

    Args:
        framework: 内部短名(funasr / cosyvoice / rapidocr / paddleocr),
                   决定配置文件名和 health 字段
        title:     FastAPI 文档标题
        default_config: 首次启动写入 yaml 的默认值
        register_routes: 给 app 挂额外路由的回调,签名 ``(app, cfg) -> None``;
                         典型用法是注册 ``/v1/audio/transcriptions`` 等业务端点
    """
    from fastapi import FastAPI

    cfg = load_or_init_config(framework, default_config)

    app = FastAPI(title=title, version="1.0.0")
    app.state.config = cfg
    app.state.framework = framework
    app.state.lib_loaded = False
    app.state.lib_error = ""
    app.state.lib_handle = None  # type: Optional[Any]

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {
            "framework": framework,
            "ok": True,                          # HTTP 探针看这个就够 → ping 通
            "ready": bool(app.state.lib_loaded),  # 业务模型是否加载完
            "error": app.state.lib_error,
            "config_path": str(runtime_config_path(framework)),
        }

    @app.get("/v1/models")
    async def list_models() -> Dict[str, Any]:
        # OpenAI 兼容格式 — 部分 adapter 用 /v1/models 探活
        return {
            "object": "list",
            "data": [{"id": framework, "object": "model", "owned_by": "chayuan"}],
        }

    register_routes(app, cfg)
    return app


def parse_serve_args(default_port: int) -> argparse.Namespace:
    """命令行参数:--host / --port / --model。

    --model 由 SidecarRuntimeManager 从 process_args.resolve_image_embedding_args
    透传,值是 scanner 给的 model_id(本地 path 或 HF repo)。各 sidecar 模块在
    __main__ 里把它写进 default_config['model'],覆盖 hardcoded fallback。
    """
    p = argparse.ArgumentParser(
        description="Chayuan runtime HTTP wrapper",
    )
    p.add_argument("--host", default="127.0.0.1", help="bind host")
    p.add_argument("--port", type=int, default=default_port, help="bind port")
    p.add_argument("--model", default=None, help="optional model_id / path; 覆盖模块 _DEFAULT_CONFIG['model']")
    return p.parse_args()


def serve(app: Any, *, host: str, port: int) -> None:
    """阻塞运行 uvicorn server。"""
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
