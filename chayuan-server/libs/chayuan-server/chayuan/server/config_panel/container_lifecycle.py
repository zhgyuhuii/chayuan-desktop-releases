"""容器生命周期统一抽象(Phase 1 — 42 题架构改造)。

为什么需要这层
==============
之前 chayuan 的容器操作散落在多处:
  * ``install_task_manager._START_RECIPES["vllm"] = ["docker","compose","up","-d","vllm"]``
  * ``install_recipes.make_compose_recipe(service)``
  * ``compose_manager.make_up_cmd / make_stop_cmd / ...``
  * 每处都用同步 ``subprocess.run`` 或 ``Popen`` 阻塞 asyncio loop
  * 无统一健康等待、流式日志、错误模型 — UI 看不到部署进度

本模块提供 **单一面板**:全 async、流式日志、统一错误码。

设计参考
========
* Docker Compose v2.17+ 的 ``--wait`` 选项:启动后阻塞直到 healthcheck 通过
* Portainer 的容器生命周期 API
* Coolify 的服务编排操作

复用面
======
* ``install_task_manager``:start/stop/restart 调本模块
* ``install_dialog``:部署按钮调 ``up_with_wait``,日志 tab 调 ``logs(follow=True)``
* ``runtime_framework_panel``:probe 用 ``health()``
* ``auto_register``:Phase 3 的容器 ready 触发挂模型清单也走这里

错误模型
========
所有方法失败抛 :class:`LifecycleError`,带:
  * ``code``:类型化错误,如 ``DOCKER_NOT_INSTALLED`` / ``IMAGE_PULL_FAILED`` / ``HEALTHCHECK_FAILED``
  * ``service``:操作的目标 service
  * ``stderr``:子进程的 stderr 摘要
  * ``hint``:中文修复建议

调用方应只 ``except LifecycleError as e:``,根据 ``e.code`` 分支处理。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

logger = logging.getLogger("chayuan.container_lifecycle")


# ============================================================================
# 错误模型
# ============================================================================

class LifecycleErrorCode(str, Enum):
    DOCKER_NOT_INSTALLED = "DOCKER_NOT_INSTALLED"
    DOCKER_DAEMON_DOWN = "DOCKER_DAEMON_DOWN"
    COMPOSE_NOT_INSTALLED = "COMPOSE_NOT_INSTALLED"
    COMPOSE_FILE_MISSING = "COMPOSE_FILE_MISSING"
    SERVICE_NOT_DEFINED = "SERVICE_NOT_DEFINED"
    IMAGE_PULL_FAILED = "IMAGE_PULL_FAILED"
    CONTAINER_START_FAILED = "CONTAINER_START_FAILED"
    HEALTHCHECK_FAILED = "HEALTHCHECK_FAILED"
    HEALTHCHECK_TIMEOUT = "HEALTHCHECK_TIMEOUT"
    OPERATION_TIMEOUT = "OPERATION_TIMEOUT"
    UNKNOWN = "UNKNOWN"


class LifecycleError(Exception):
    def __init__(
        self,
        code: LifecycleErrorCode,
        message: str,
        *,
        service: Optional[str] = None,
        stderr: str = "",
        hint: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.service = service
        self.stderr = stderr[:1000] if stderr else ""
        self.hint = hint

    def __repr__(self) -> str:
        return f"LifecycleError({self.code.value}, service={self.service!r}, msg={self.args[0]!r})"


# ============================================================================
# 健康状态
# ============================================================================

class HealthState(str, Enum):
    """容器级健康状态(对应 docker inspect .State.Health.Status + .State.Status)。"""
    HEALTHY = "healthy"          # healthcheck 通过
    STARTING = "starting"        # healthcheck 在 start_period 内或 retries 中
    UNHEALTHY = "unhealthy"      # healthcheck 连续失败
    RUNNING_NO_CHECK = "running" # 容器在跑但 yaml 里没定义 healthcheck
    EXITED = "exited"            # 容器已退出
    CREATED = "created"          # 容器已创建未启动
    MISSING = "missing"          # docker 中找不到


@dataclass
class ContainerHealth:
    service: str
    state: HealthState
    container_id: str = ""
    container_name: str = ""
    image: str = ""
    ports: List[str] = field(default_factory=list)
    health_log_tail: List[str] = field(default_factory=list)  # 最近几次 healthcheck 输出
    raw_status: str = ""  # docker inspect .State.Status 原文,debug 用
    # 45 题:up 流程检测到 host port 被占而自动换端口时,把新端口写这里。
    # UI 据此提示用户"原 37997 被占,已自动换 38000"。
    port_reallocated_to: Optional[int] = None
    port_reallocated_from: Optional[int] = None

    @property
    def is_ready(self) -> bool:
        """业务可用 = healthy 或 (running but no healthcheck defined)。"""
        return self.state in (HealthState.HEALTHY, HealthState.RUNNING_NO_CHECK)


# ============================================================================
# 流式日志行
# ============================================================================

@dataclass
class LogLine:
    """容器日志一行 — 带源标识,UI 可着色。"""
    source: str  # "stdout" / "stderr" / "internal"(我们打的元信息)
    text: str
    timestamp: float = 0.0  # epoch seconds(可选)


# ============================================================================
# 主类
# ============================================================================

class ContainerLifecycle:
    """全 async 容器生命周期管理器。

    无内部状态(除 ``compose_file_path``),线程安全。可在多个协程并发调用同一
    service 的不同操作(由 docker daemon 自身做互斥),但**避免**对同一 service
    并发跑 ``up`` 和 ``down``。

    Usage::

        lc = ContainerLifecycle()
        async for line in lc.pull("vllm"):
            print(line.text)
        try:
            health = await lc.up("vllm", wait_healthy=True, timeout=120)
            print(f"vllm ready: {health}")
        except LifecycleError as e:
            print(f"start failed: {e.code} {e.hint}")
    """

    def __init__(self, compose_file_path: Optional[Path] = None) -> None:
        # 延迟绑定:首次操作时才 ensure_compose_file(避免 import 时副作用)
        self._compose_file: Optional[Path] = compose_file_path

    # ------------------------------------------------------------------------
    # 内部 helper
    # ------------------------------------------------------------------------

    def _get_compose_file(self) -> Path:
        if self._compose_file is None:
            try:
                from chayuan.server.config_panel.compose_manager import ensure_compose_file
                self._compose_file = ensure_compose_file()
            except Exception as e:
                raise LifecycleError(
                    LifecycleErrorCode.COMPOSE_FILE_MISSING,
                    f"无法定位或创建 docker-compose.yaml:{e}",
                    hint="检查 <CHAYUAN_ROOT>/compose/ 目录权限",
                )
        return self._compose_file

    def _get_compose_binary(self) -> List[str]:
        if not shutil.which("docker"):
            raise LifecycleError(
                LifecycleErrorCode.DOCKER_NOT_INSTALLED,
                "docker 不在 PATH",
                hint="Linux: apt install docker.io 或 https://docs.docker.com/engine/install/;"
                     "macOS/Windows: 装 Docker Desktop",
            )
        # docker compose v2 plugin 是默认形态
        return ["docker", "compose"]

    def _base_cmd(
        self,
        yaml_path: Optional[Path] = None,
    ) -> List[str]:
        """``docker compose -f <yaml>`` 前缀。

        Args:
            yaml_path: 显式指定 yaml(55 题:每服务独立 yaml 时传)。
                None 时退回单一聚合 ``docker-compose.yaml``(向后兼容)。
        """
        if yaml_path is None:
            yaml_path = self._get_compose_file()
        return [*self._get_compose_binary(), "-f", str(yaml_path)]

    @staticmethod
    def _resolve_service_yaml(service: str) -> Optional[Path]:
        """55 题:按 service 名找其独立 yaml(``<CHAYUAN_ROOT>/compose/<service>.yaml``);
        没找到返 None,调用方退回聚合主 yaml。"""
        try:
            from chayuan.server.config_panel.compose_manager import (
                get_compose_file_for_service,
            )
            return get_compose_file_for_service(service)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _needs_thread_subprocess() -> bool:
        """54-A:Windows + SelectorEventLoop **不支持** asyncio subprocess
        (``NotImplementedError`` from ``base_events._make_subprocess_transport``)。

        NiceGUI 在 Windows 下需要 Selector loop(socket select),所以子进程内
        我们的 event loop 是 Selector。检测到就降级走 thread + sync subprocess。
        Linux/macOS 永远 False(原生 async subprocess 没问题)。
        """
        if os.name != "nt":
            return False
        try:
            loop = asyncio.get_event_loop()
            return "Selector" in type(loop).__name__
        except Exception:  # noqa: BLE001
            # 没活动 loop 时(在 thread 池里调用?)按 Windows 默认 Selector 处理
            return True

    async def _run_capture(
        self,
        args: List[str],
        *,
        timeout: float = 60.0,
        input_text: Optional[str] = None,
    ) -> tuple[int, str, str]:
        """跑 subprocess,捕获 stdout/stderr。timeout 超时抛 LifecycleError.OPERATION_TIMEOUT。

        Windows + Selector loop 自动降级到 thread + sync subprocess(54-A 修)。
        """
        if self._needs_thread_subprocess():
            return await asyncio.to_thread(
                self._run_capture_sync, args, timeout, input_text,
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if input_text else None,
            )
        except FileNotFoundError as e:
            raise LifecycleError(
                LifecycleErrorCode.DOCKER_NOT_INSTALLED,
                f"无法启动 {args[0]}:{e}",
                hint="检查 docker / docker compose 是否在 PATH",
            )
        except NotImplementedError:
            # 防御性兜底:即使 _needs_thread_subprocess 没识别到,捕获后也降级
            return await asyncio.to_thread(
                self._run_capture_sync, args, timeout, input_text,
            )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_text.encode() if input_text else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise LifecycleError(
                LifecycleErrorCode.OPERATION_TIMEOUT,
                f"操作超时 ({timeout}s):{' '.join(args)}",
                hint=f"docker daemon 可能慢或挂了,试 'docker info'",
            )
        return proc.returncode or 0, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")

    def _run_capture_sync(
        self,
        args: List[str],
        timeout: float,
        input_text: Optional[str],
    ) -> tuple[int, str, str]:
        """sync 版本(thread 内跑) — 给 Windows Selector loop 用。"""
        try:
            result = subprocess.run(  # noqa: S603
                args, capture_output=True, text=True,
                timeout=timeout, input=input_text, check=False,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError as e:
            raise LifecycleError(
                LifecycleErrorCode.DOCKER_NOT_INSTALLED,
                f"无法启动 {args[0]}:{e}",
                hint="检查 docker / docker compose 是否在 PATH",
            )
        except subprocess.TimeoutExpired:
            raise LifecycleError(
                LifecycleErrorCode.OPERATION_TIMEOUT,
                f"操作超时 ({timeout}s):{' '.join(args)}",
                hint="docker daemon 可能慢或挂了,试 'docker info'",
            )

    async def _stream(
        self,
        args: List[str],
        *,
        timeout: Optional[float] = None,
    ) -> AsyncIterator[LogLine]:
        """跑 subprocess,逐行 yield 输出(stdout + stderr 合并)。

        Windows + Selector loop 自动降级:用 thread 跑 sync Popen,
        通过 ``asyncio.Queue`` 桥接到 async generator(54-A)。
        """
        if self._needs_thread_subprocess():
            async for line in self._stream_via_thread(args):
                yield line
            return

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # 合并到一个流
            )
        except FileNotFoundError as e:
            raise LifecycleError(
                LifecycleErrorCode.DOCKER_NOT_INSTALLED,
                f"无法启动 {args[0]}:{e}",
            )
        except NotImplementedError:
            # 防御性兜底
            async for line in self._stream_via_thread(args):
                yield line
            return

        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip("\n")
                yield LogLine(source="stdout", text=line)
        finally:
            await proc.wait()

    async def _stream_via_thread(
        self,
        args: List[str],
    ) -> AsyncIterator[LogLine]:
        """54-A 兜底:thread 跑 sync Popen,asyncio.Queue 桥接到 async generator。

        thread 内同步读 ``proc.stdout`` 一行一行 put 到 queue;主协程 await
        queue.get() 拉行 yield。结束信号用 sentinel(None)传递。
        """
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        SENTINEL = object()

        def _bg_run() -> None:
            try:
                proc = subprocess.Popen(  # noqa: S603
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                    ),
                )
            except FileNotFoundError as e:
                # 把异常包成消息塞队列,主协程拿到再 raise
                asyncio.run_coroutine_threadsafe(
                    q.put(("ERROR", f"FileNotFoundError: {e}")),
                    loop,
                )
                asyncio.run_coroutine_threadsafe(q.put(SENTINEL), loop)
                return
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    asyncio.run_coroutine_threadsafe(
                        q.put(("LINE", line)), loop,
                    )
            except Exception as e:  # noqa: BLE001
                asyncio.run_coroutine_threadsafe(
                    q.put(("ERROR", str(e))), loop,
                )
            finally:
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
                asyncio.run_coroutine_threadsafe(q.put(SENTINEL), loop)

        threading.Thread(
            target=_bg_run, daemon=True, name=f"lifecycle-stream-{args[0]}",
        ).start()

        while True:
            item = await q.get()
            if item is SENTINEL:
                return
            kind, payload = item
            if kind == "ERROR":
                raise LifecycleError(
                    LifecycleErrorCode.UNKNOWN,
                    f"thread subprocess error: {payload}",
                )
            yield LogLine(source="stdout", text=payload)

    # ------------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------------

    async def pull(
        self,
        service: str,
        *,
        timeout: float = 600.0,  # 10 分钟,大镜像
    ) -> AsyncIterator[LogLine]:
        """``docker compose -f <service>.yaml pull <service>``,流式 yield 日志。

        55 题:优先用 ``<CHAYUAN_ROOT>/compose/<service>.yaml``(每服务独立);
        没找到退回聚合 ``docker-compose.yaml``。

        失败抛 ``LifecycleError(IMAGE_PULL_FAILED)``。
        """
        yaml_p = self._resolve_service_yaml(service)
        cmd = [*self._base_cmd(yaml_p), "pull", service]
        async for line in self._stream(cmd, timeout=timeout):
            yield line

    async def up(
        self,
        service: str,
        *,
        wait_healthy: bool = True,
        timeout: float = 180.0,
        auto_reallocate_port: bool = True,
    ) -> ContainerHealth:
        """``docker compose up -d <service>``。

        Args:
            wait_healthy: True 时加 ``--wait`` flag,容器 healthcheck 通过才返回。
                需 docker compose v2.17+(用户已确认 v5.1.2)。
                若 yaml 里没定义 healthcheck,docker compose 会立即认为 ready。
            timeout: 整个 up 操作的硬上限(包含 wait_healthy)。
            auto_reallocate_port: 45 题 — True 时启动前若 host port 被占,
                自动找一个空闲端口写回 yaml,再启动。给 ``compose_manager.
                auto_reallocate_port_if_occupied`` 兜底。设 False 走原行为
                (碰到 "port is already allocated" 直接抛 LifecycleError)。

        返回 :class:`ContainerHealth`(需 wait_healthy=True 才保证 .is_ready)。
        失败抛 :class:`LifecycleError`。
        """
        # 45 题 P0:启动前 preflight 端口冲突 — 占用就自动换
        # 用户期望:host 上 37997 被别的进程占了,chayuan 应自动用 37998 起
        reallocated_port: Optional[int] = None
        original_port: Optional[int] = None
        if auto_reallocate_port:
            try:
                from chayuan.server.config_panel.compose_manager import (
                    auto_reallocate_port_if_occupied, get_service_host_port,
                )
                original_port = await asyncio.to_thread(get_service_host_port, service)
                reallocated_port = await asyncio.to_thread(
                    auto_reallocate_port_if_occupied, service,
                )
                if reallocated_port:
                    logger.info(
                        "[lifecycle] %s host port 冲突 (%s),已自动切换到 %d",
                        service, original_port, reallocated_port,
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("[lifecycle] auto-reallocate failed (ignored): %r", e)

        # 55 题:优先用每服务独立 yaml
        yaml_p = self._resolve_service_yaml(service)
        cmd = [*self._base_cmd(yaml_p), "up", "-d"]
        if wait_healthy:
            cmd.append("--wait")
            cmd += ["--wait-timeout", str(int(timeout))]
        cmd.append(service)

        rc, stdout, stderr = await self._run_capture(cmd, timeout=timeout + 30)
        if rc != 0:
            # 判断错误类型 — 用 stderr 文本特征
            err = (stderr or "").lower()
            if "no such service" in err or "service" in err and "not found" in err:
                code = LifecycleErrorCode.SERVICE_NOT_DEFINED
                hint = f"compose yaml 里没定义 service '{service}'"
            elif "port is already allocated" in err or "bind for" in err and "failed" in err:
                code = LifecycleErrorCode.CONTAINER_START_FAILED
                hint = (
                    f"host port 已被其他进程占用。已尝试自动重分配但失败 — "
                    f"请手动编辑 compose yaml 改 ports 字段"
                    f"(如 ports: ['38000:7997'])"
                )
            elif (
                "did not become healthy" in err
                or ("wait" in err and "timeout" in err)
                or "did not start" in err
                or "container exited" in err and wait_healthy
            ):
                code = LifecycleErrorCode.HEALTHCHECK_TIMEOUT
                hint = f"容器启动了但 {timeout}s 内 healthcheck 没通过 — 检查 logs"
            elif "pull access denied" in err or "manifest" in err:
                code = LifecycleErrorCode.IMAGE_PULL_FAILED
                hint = "镜像拉取失败 — 检查镜像名 / 网络 / 镜像源"
            elif "permission denied" in err:
                code = LifecycleErrorCode.DOCKER_DAEMON_DOWN
                hint = "docker daemon 权限问题 — 把当前用户加 docker 组,或用 sudo"
            elif "cannot connect to the docker daemon" in err:
                code = LifecycleErrorCode.DOCKER_DAEMON_DOWN
                hint = "docker daemon 未启动 — Linux: systemctl start docker;Mac/Win: 启动 Docker Desktop"
            else:
                code = LifecycleErrorCode.CONTAINER_START_FAILED
                hint = "查看完整 stderr 定位"
            raise LifecycleError(
                code,
                f"docker compose up -d {service} 失败 (rc={rc})",
                service=service,
                stderr=stderr,
                hint=hint,
            )

        # up 成功 → 拉一次 health 状态返回,并附上端口重分配信息(若有)
        h = await self.health(service)
        if reallocated_port is not None:
            h.port_reallocated_to = reallocated_port
            h.port_reallocated_from = original_port
        return h

    async def stop(
        self,
        service: str,
        *,
        timeout: float = 30.0,
    ) -> bool:
        """``docker compose -f <yaml> stop <service>``。返回是否成功。"""
        yaml_p = self._resolve_service_yaml(service)
        cmd = [*self._base_cmd(yaml_p), "stop", service]
        rc, _, _ = await self._run_capture(cmd, timeout=timeout)
        return rc == 0

    async def down(
        self,
        service: str,
        *,
        with_volumes: bool = False,
        timeout: float = 60.0,
    ) -> bool:
        """``docker compose down <service>``(实际 compose 不直接支持单 service down,
        改用 ``rm -f -s -v?`` 等价)。"""
        yaml_p = self._resolve_service_yaml(service)
        cmd = [*self._base_cmd(yaml_p), "rm", "-f", "-s", service]
        if with_volumes:
            cmd.append("-v")
        rc, _, _ = await self._run_capture(cmd, timeout=timeout)
        return rc == 0

    async def logs(
        self,
        service: str,
        *,
        follow: bool = False,
        tail: int = 200,
        timeout: Optional[float] = None,
    ) -> AsyncIterator[LogLine]:
        """``docker compose -f <yaml> logs [-f] --tail=N <service>``,流式 yield。

        ``follow=True`` 时无限流,调用方应有退出机制(asyncio.CancelledError 会终止)。
        """
        yaml_p = self._resolve_service_yaml(service)
        cmd = [*self._base_cmd(yaml_p), "logs", f"--tail={tail}"]
        if follow:
            cmd.append("-f")
        cmd.append(service)
        async for line in self._stream(cmd, timeout=timeout):
            yield line

    async def health(self, service: str) -> ContainerHealth:
        """`docker inspect` 解析容器健康状态。失败时返 ``MISSING``,不抛错。

        优先用 ``docker compose ps --format json`` 取容器 ID,然后 ``docker inspect``
        拿 ``.State.Health.Status``。
        """
        # 55 题:优先用每服务独立 yaml(否则聚合 yaml 兼容)
        yaml_p = self._resolve_service_yaml(service)
        # 1) 找容器 ID
        ps_cmd = [*self._base_cmd(yaml_p), "ps", "--format", "json", service]
        rc, stdout, _ = await self._run_capture(ps_cmd, timeout=10)
        if rc != 0 or not stdout.strip():
            return ContainerHealth(service=service, state=HealthState.MISSING)

        # docker compose ps --format json 每行一个对象(NDJSON)
        container_info: Optional[Dict[str, Any]] = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("Service") == service:
                container_info = obj
                break
        if container_info is None:
            return ContainerHealth(service=service, state=HealthState.MISSING)

        cid = container_info.get("ID") or ""
        cname = container_info.get("Name") or ""
        image = container_info.get("Image") or ""
        # ports 字段是 string,简单 split
        raw_ports = container_info.get("Publishers") or []
        ports = []
        if isinstance(raw_ports, list):
            for p in raw_ports:
                if isinstance(p, dict):
                    pubport = p.get("PublishedPort")
                    if pubport:
                        ports.append(str(pubport))

        # 2) docker inspect 拿 health
        if not cid:
            return ContainerHealth(
                service=service, state=HealthState.MISSING,
                container_name=cname, image=image, ports=ports,
            )
        rc, stdout, _ = await self._run_capture(
            ["docker", "inspect", "--format", "{{json .State}}", cid],
            timeout=5,
        )
        if rc != 0 or not stdout.strip():
            return ContainerHealth(
                service=service, state=HealthState.MISSING,
                container_id=cid, container_name=cname, image=image, ports=ports,
            )
        try:
            state_obj = json.loads(stdout)
        except json.JSONDecodeError:
            state_obj = {}

        running = bool(state_obj.get("Running"))
        raw_status = str(state_obj.get("Status") or "")
        health_obj = state_obj.get("Health") or {}
        health_status = (health_obj.get("Status") or "").lower()

        # 映射 docker 内部状态 → 我们的 HealthState
        if health_status == "healthy":
            state = HealthState.HEALTHY
        elif health_status == "unhealthy":
            state = HealthState.UNHEALTHY
        elif health_status == "starting":
            state = HealthState.STARTING
        elif running and not health_obj:
            state = HealthState.RUNNING_NO_CHECK
        elif raw_status == "exited":
            state = HealthState.EXITED
        elif raw_status == "created":
            state = HealthState.CREATED
        else:
            state = HealthState.MISSING

        # 健康日志尾部(失败时 UI 显示)
        log_tail: List[str] = []
        for entry in (health_obj.get("Log") or [])[-3:]:
            if isinstance(entry, dict):
                out = (entry.get("Output") or "").strip().splitlines()
                if out:
                    log_tail.append(out[-1][:200])

        return ContainerHealth(
            service=service,
            state=state,
            container_id=cid,
            container_name=cname,
            image=image,
            ports=ports,
            health_log_tail=log_tail,
            raw_status=raw_status,
        )

    async def health_many(self, services: List[str]) -> Dict[str, ContainerHealth]:
        """并发查多个 service 的健康状态(给 UI 卡片刷新用)。"""
        results = await asyncio.gather(
            *(self.health(s) for s in services), return_exceptions=True,
        )
        out: Dict[str, ContainerHealth] = {}
        for s, r in zip(services, results):
            if isinstance(r, BaseException):
                out[s] = ContainerHealth(service=s, state=HealthState.MISSING)
            else:
                out[s] = r
        return out

    async def inspect(self, service: str) -> Dict[str, Any]:
        """完整 ``docker inspect`` JSON,给"诊断详情"用。失败返 {}。"""
        h = await self.health(service)
        if not h.container_id:
            return {}
        rc, stdout, _ = await self._run_capture(
            ["docker", "inspect", h.container_id], timeout=10,
        )
        if rc != 0:
            return {}
        try:
            arr = json.loads(stdout)
            if isinstance(arr, list) and arr:
                return arr[0]
        except json.JSONDecodeError:
            pass
        return {}


# ============================================================================
# 单例 helper
# ============================================================================

_default_lifecycle: Optional[ContainerLifecycle] = None


def get_container_lifecycle() -> ContainerLifecycle:
    """单例。多处调用共享同一 compose_file 解析。"""
    global _default_lifecycle
    if _default_lifecycle is None:
        _default_lifecycle = ContainerLifecycle()
    return _default_lifecycle


__all__ = [
    "ContainerLifecycle",
    "ContainerHealth",
    "HealthState",
    "LogLine",
    "LifecycleError",
    "LifecycleErrorCode",
    "get_container_lifecycle",
]
