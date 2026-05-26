"""本机可用服务探测器。

定位
----
服务管理页的"服务列表"原本只展示**已在 yaml 里配置过**的服务（Redis / DB /
Milvus / MinIO）。本模块补齐另一半视角：在本机扫描**已安装且正在监听**的
外部服务（如系统自带的 Postgres、brew 起的 Redis、docker 跑的 Milvus），
让用户一眼看到"哪些现成的后端可以直接接进项目里用"。

与 ``service_checks.py`` 的区别
------------------------------
- ``service_checks.py`` 面向 **已配置** 的服务：读 yaml / 探连接 / 返回状态。
- ``local_service_detector.py`` 面向 **本机已安装** 的服务：扫监听端口 / 协议
  握手识别类型 / 拿版本。两者正交，拼起来形成完整的"外部依赖视图"。

与 ``process_utils.find_pids_listening_on`` 的区别
------------------------------------------------
- 后者按 **端口查 pid**，用于生命周期管理（杀进程）。
- 本模块按 **服务类型** 枚举候选端口 + 协议验证 + 版本抽取；是业务诊断视角。
  两者共用 ``psutil.net_connections`` 但返回结构完全不同。

设计原则
--------
1. **纯探测 / 只读**：不写任何配置；也不触发任何项目侧的连接池重建。
2. **快**：总扫描时间控制在 ~1s 内；协议探针统一 0.4s 超时；失败即认为
   "不是该类型"，不阻塞。
3. **宽松识别**：端口匹配即纳入候选；能协议握手的 ``verified=True``，
   握手失败但端口占用的仍然展示（标 ``verified=False``，灰色），给用户一个
   "这里有个怪东西占着"的提示。
4. **零依赖外部 CLI**：版本信息只从协议握手里取（PG / MySQL 的 handshake
   自带 server_version；Redis ``INFO server``；Milvus/ES HTTP；MinIO HTTP）。
   不调用 ``redis-cli`` / ``psql`` / 等——容器镜像里经常没装。
"""
from __future__ import annotations

import logging
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("chayuan.config_panel.local_service_detector")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class DetectedService:
    """本机扫描到的一个外部服务。

    由于探测视角恒为客户端 → 127.0.0.1，``host`` 字段固定 ``127.0.0.1``；
    真正的绑定目标由调用方（catalog / ui）决定是写 ``127.0.0.1``、``localhost``
    还是 ``host.docker.internal``。
    """

    kind: str                         # "redis"/"postgresql"/"mysql"/"milvus"/"minio"/"elasticsearch"
    label: str                        # "Redis"
    host: str                         # 探测视角，固定 127.0.0.1
    port: int
    pids: List[int] = field(default_factory=list)
    process_name: str = ""
    version: str = ""                 # 从协议/HTTP 握手拿到的 server 版本
    verified: bool = False            # 协议握手是否成功
    detail: str = ""                  # 一行人类可读描述


# ---------------------------------------------------------------------------
# 候选端口表 + 进程名提示
# ---------------------------------------------------------------------------

# 每个服务类型的"候选端口"。之所以给列表：同一类型在不同部署下可能监听不同端口
# （如 postgres 自定义到 15432、minio console 9001）。扫描只对 LISTEN 端口感
# 兴趣，名单只用于"把扫到的端口映射回服务类型"的反查。
_DEFAULT_PORTS: Dict[str, List[int]] = {
    # 同时覆盖：上游标准端口 + 察元 vendor 偏好端口（chayuan/server/runtime/vendor_loader.py
    # 中 KNOWN_SERVICES 的 default_port），让"用 dev-stack-alt-ports 起的服务"也
    # 能被探到并展示在配置面板"本机可用服务"卡里。
    "redis": [6379, 6380, 16379, 36379],
    "postgresql": [5432, 15432, 25432, 35432],
    "mysql": [3306, 3307, 33060],       # 33060 是 MySQL X Protocol，也归入 mysql 类别
    "milvus": [19530, 19121, 39530],     # 19530 = gRPC；19121 = 旧版 REST；39530 = alt-ports 偏好
    "minio": [9000, 39000],              # 9001 是 console，不算"数据端口"
    "elasticsearch": [9200, 39200],
}

# psutil 拿到的进程名 → 服务类型（辅助识别，主要看协议探针）
_PROCESS_NAME_HINTS: Dict[str, str] = {
    "redis-server": "redis",
    "redis": "redis",
    "postgres": "postgresql",
    "postmaster": "postgresql",
    "mysqld": "mysql",
    "mariadbd": "mysql",
    "mariadb": "mysql",
    "milvus": "milvus",
    "minio": "minio",
    "java": "elasticsearch",            # ES 跑在 JVM 里，需要靠端口 + HTTP 探针确认
}


_SERVICE_LABELS: Dict[str, str] = {
    "redis": "Redis",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL / MariaDB",
    "milvus": "Milvus",
    "minio": "MinIO / S3",
    "elasticsearch": "Elasticsearch",
}


# ---------------------------------------------------------------------------
# 基础端口扫描
# ---------------------------------------------------------------------------

def _list_local_listens() -> List[Tuple[int, List[int], str]]:
    """列出本机 TCP LISTEN 端口 → ``(port, [pids], process_name)``。

    优先走 ``psutil``（跨平台，一次系统调用全拿齐）；拿不到时退化为
    逐端口 ``socket.connect`` 探活（只对候选端口表里的端口），版本信息更少
    但能保证基本能力。
    """
    # --- 走 psutil（首选）---
    try:
        import psutil  # type: ignore
        port_map: Dict[int, Tuple[List[int], str]] = {}
        for c in psutil.net_connections(kind="inet"):
            try:
                if str(c.status or "").upper() != "LISTEN":
                    continue
                if not c.laddr:
                    continue
                # 只看本机（IPv4/IPv6 都收；127.0.0.1 / 0.0.0.0 / :: 都算）
                port = int(c.laddr.port)
                pid = int(c.pid) if c.pid else 0
                pids, pname = port_map.get(port, ([], ""))
                if pid and pid not in pids:
                    pids.append(pid)
                if not pname and pid:
                    try:
                        pname = psutil.Process(pid).name() or ""
                    except Exception:  # noqa: BLE001
                        pname = ""
                port_map[port] = (pids, pname)
            except Exception:  # noqa: BLE001
                continue
        return [(p, v[0], v[1]) for p, v in port_map.items()]
    except Exception as e:  # noqa: BLE001
        logger.debug("psutil scan failed, falling back to socket probe: %s", e)

    # --- 退化：只扫候选端口，无 pid/进程名 ---
    found: List[Tuple[int, List[int], str]] = []
    for ports in _DEFAULT_PORTS.values():
        for p in ports:
            if _probe_tcp("127.0.0.1", p, timeout=0.3):
                found.append((p, [], ""))
    return found


def _probe_tcp(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 协议探针 —— 每个协议一个，返回 (kind_or_None, version, detail)
# ---------------------------------------------------------------------------

def _probe_redis(host: str, port: int, timeout: float = 0.4) -> Tuple[bool, str, str]:
    """Redis 探针：发 ``PING\\r\\n``；回 ``+PONG``/``-NOAUTH`` 即确认是 Redis。

    对带密码但无 AUTH 的实例：服务会回 ``-NOAUTH Authentication required``
    这也属于"可确认是 Redis"。
    版本靠 ``INFO server`` 拿（免密的场景下），拿不到就空着。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"*1\r\n$4\r\nPING\r\n")
            data = s.recv(64)
            if not data:
                return False, "", ""
            first = data[:1]
            if first not in (b"+", b"-"):
                return False, "", ""
            # 成功路径尝试拿版本（不影响确认）
            version = ""
            if first == b"+":
                try:
                    s.sendall(b"*2\r\n$4\r\nINFO\r\n$6\r\nserver\r\n")
                    info = s.recv(4096).decode("utf-8", errors="ignore")
                    for line in info.splitlines():
                        if line.startswith("redis_version:"):
                            version = line.split(":", 1)[1].strip()
                            break
                except Exception:  # noqa: BLE001
                    pass
            note = "PING OK" if first == b"+" else data.decode("utf-8", errors="ignore").strip()
            return True, version, note
    except OSError:
        return False, "", ""


def _probe_postgresql(host: str, port: int, timeout: float = 0.6) -> Tuple[bool, str, str]:
    """PostgreSQL startup 包探针。

    发一个最小 StartupMessage（protocol=3.0, user=chayuan_probe），
    任何 PG 都会立刻回 ``R``(Authentication) 或 ``E``(Error)：
    - ``R``：拿到认证请求 → 明确是 PG
    - ``E``：错误（如"role xxx does not exist"）→ 同样证明是 PG
    ``S``（SSLResponse）情况见：若 server 强制 SSL，会先回 ``S`` 要求握手，
    这里也算确认。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            # StartupMessage: length(4) + protocol(4) + "user\0chayuan_probe\0\0"
            body = b"user\x00chayuan_probe\x00\x00"
            proto = struct.pack(">I", 196608)          # 3.0
            msg_len = struct.pack(">I", 4 + len(proto) + len(body))
            s.sendall(msg_len + proto + body)
            head = s.recv(5)
            if len(head) < 5:
                return False, "", ""
            mtype = head[:1]
            if mtype in (b"R", b"E", b"S", b"N"):
                # 尝试从 Error 里抠 server_version（有些版本不带）
                version = ""
                note = {
                    b"R": "握手成功（PG 请求认证）",
                    b"E": "握手成功（PG 返回 Error，意料之中）",
                    b"S": "PG + 强制 SSL",
                    b"N": "PG 拒绝 SSL",
                }[mtype]
                return True, version, note
            return False, "", ""
    except OSError:
        return False, "", ""


def _probe_mysql(host: str, port: int, timeout: float = 0.6) -> Tuple[bool, str, str]:
    """MySQL handshake 探针。MySQL/MariaDB 连上来第一件事就是 server → client
    发一个 Initial Handshake Packet：

        [packet_len 3][packet_num 1][protocol 1][server_version \\0 ...]

    任何 MySQL 兼容实现都是这个协议，这里只取 server_version 字符串即可确认。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            head = s.recv(4)
            if len(head) < 4:
                return False, "", ""
            # packet_len: 3 字节小端
            pkt_len = head[0] | (head[1] << 8) | (head[2] << 16)
            if pkt_len <= 0 or pkt_len > 1024:
                return False, "", ""
            body = _recv_exact(s, min(pkt_len, 256))
            if not body:
                return False, "", ""
            protocol_ver = body[0]
            if protocol_ver not in (9, 10):
                return False, "", ""
            # server_version 以 \0 结束
            try:
                ver_end = body.index(b"\x00", 1)
            except ValueError:
                return False, "", ""
            version = body[1:ver_end].decode("utf-8", errors="ignore")
            return True, version, f"handshake v{protocol_ver}"
    except OSError:
        return False, "", ""


def _probe_http_root(host: str, port: int, timeout: float = 0.6) -> Tuple[int, str, str]:
    """发一个 ``GET / HTTP/1.0``；返回 ``(status_code, server_header, body_prefix)``。

    很多服务（MinIO / ES / 大多数 HTTP 后端）在 / 上会回带 ``Server:`` 头的响应
    —— 用来区分是谁最直接。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            buf = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = s.recv(2048)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                if len(buf) >= 4096:
                    break
            text = buf.decode("utf-8", errors="ignore")
            if not text.startswith("HTTP/"):
                return -1, "", ""
            head, _, body = text.partition("\r\n\r\n")
            lines = head.splitlines()
            status = -1
            try:
                status = int(lines[0].split()[1])
            except Exception:  # noqa: BLE001
                pass
            server = ""
            for ln in lines[1:]:
                if ln.lower().startswith("server:"):
                    server = ln.split(":", 1)[1].strip()
                    break
            return status, server, body[:256]
    except OSError:
        return -1, "", ""


def _probe_milvus(host: str, port: int, timeout: float = 0.4) -> Tuple[bool, str, str]:
    """Milvus 2.x 默认是 gRPC（HTTP/2）；用裸 TCP 连通 + 发一个 HTTP/2 前导帧
    测试比较复杂。折中方案：

    1. 端口是 19530 → TCP 通即视为 Milvus（几乎没有别的服务抢这个端口）
    2. 端口是 19121（旧 REST）→ HTTP 探一下 ``/``；路径里通常含 milvus 字样
    """
    if port == 19530:
        ok = _probe_tcp(host, port, timeout=timeout)
        return ok, "", "TCP 可达（默认 Milvus gRPC 端口）" if ok else ""
    if port == 19121:
        status, server, body = _probe_http_root(host, port, timeout=timeout)
        if 200 <= status < 500 and ("milvus" in server.lower() or "milvus" in body.lower()):
            return True, "", f"HTTP {status} · {server or 'milvus-rest'}"
        return False, "", ""
    return False, "", ""


def _probe_minio(host: str, port: int, timeout: float = 0.4) -> Tuple[bool, str, str]:
    """MinIO / S3 兼容后端探针。发 ``GET /``：
    - MinIO 会回 403 或 400 XML（表明是 S3 协议）
    - ``Server: MinIO`` 头是最硬的证据
    """
    status, server, body = _probe_http_root(host, port, timeout=timeout)
    if status < 0:
        return False, "", ""
    srv = server.lower()
    if "minio" in srv:
        # MinIO 的 Server 头通常就是固定字符串 "MinIO"（不带版本），
        # 所以 version 留空，只把它作为 note 展示即可。
        return True, "", f"HTTP {status} · {server}"
    # 其它 S3 兼容后端：看 body 里是否含 <Error> 和 AWS-style XML
    if status in (400, 403) and "<Code>" in body and "<Error>" in body:
        return True, "", f"HTTP {status} · {server or 'S3-compatible'}"
    return False, "", ""


def _probe_elasticsearch(host: str, port: int, timeout: float = 0.5) -> Tuple[bool, str, str]:
    """ES 的 ``GET /`` 会回带 ``version.number`` 的 JSON（免密时）；
    8.x 默认开启安全时回 401，但依然有 ``You Know, for Search`` 或 ES 特有 header
    —— 这里两种情况都认可。
    """
    status, server, body = _probe_http_root(host, port, timeout=timeout)
    if status < 0:
        return False, "", ""
    # 免密 ES：body 里含 "number": "x.y.z"
    if status == 200 and ("\"cluster_name\"" in body or "You Know, for Search" in body):
        version = ""
        # 快速抓版本号："number" : "8.12.1"
        try:
            import re
            m = re.search(r'"number"\s*:\s*"([^"]+)"', body)
            if m:
                version = m.group(1)
        except Exception:  # noqa: BLE001
            pass
        return True, version, f"HTTP 200 · ES{' v' + version if version else ''}"
    # 开启安全的 ES：401 + body 里有 security_exception 或 ES 的 header
    if status == 401 and ("security_exception" in body or "missing authentication credentials" in body):
        return True, "", "HTTP 401 · ES 开启安全（需凭据）"
    return False, "", ""


def _recv_exact(s: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        try:
            chunk = s.recv(n - len(out))
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


# ---------------------------------------------------------------------------
# 探针调度表
# ---------------------------------------------------------------------------

# 每个 (kind, probe_fn) 按"候选端口"跑探针。
_PROBES: Dict[str, Callable[[str, int], Tuple[bool, str, str]]] = {
    "redis": _probe_redis,
    "postgresql": _probe_postgresql,
    "mysql": _probe_mysql,
    "milvus": _probe_milvus,
    "minio": _probe_minio,
    "elasticsearch": _probe_elasticsearch,
}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def detect_local_services(
    *,
    host: str = "127.0.0.1",
    extra_ports: Optional[Dict[str, List[int]]] = None,
) -> List[DetectedService]:
    """扫描本机，返回所有识别到的外部服务。

    流程：
    1. ``_list_local_listens()`` 拿本机所有 LISTEN 端口 + pid/进程名；
    2. 对每个端口，根据候选端口表/进程名提示筛出可能的服务类型集合；
    3. 对每个可能类型跑协议探针，命中 → 作为 ``DetectedService`` 返回；
    4. 对于**没扫到但候选端口被占用**的情况（例如端口号不在候选表但进程名匹配），
       仍然作为 ``verified=False`` 的 entry 加进结果。

    ``extra_ports`` 让调用方（如从 kb_settings 里读到用户之前配过的自定义端口）
    补进候选表，提高识别率。
    """
    t0 = time.monotonic()
    listens = _list_local_listens()

    # 端口 → 候选类型列表
    port_to_kinds: Dict[int, List[str]] = {}
    for kind, ports in _DEFAULT_PORTS.items():
        for p in ports:
            port_to_kinds.setdefault(p, []).append(kind)
    if extra_ports:
        for kind, ports in extra_ports.items():
            for p in ports:
                port_to_kinds.setdefault(p, [])
                if kind not in port_to_kinds[p]:
                    port_to_kinds[p].append(kind)

    results: List[DetectedService] = []
    seen_keys = set()

    for port, pids, pname in listens:
        # 1. 按端口找候选类型
        candidates = list(port_to_kinds.get(port, []))
        # 2. 按进程名提示扩充候选（比如某用户把 PG 跑在 15432）
        hint = _PROCESS_NAME_HINTS.get((pname or "").lower())
        if hint and hint not in candidates:
            candidates.append(hint)

        for kind in candidates:
            probe = _PROBES.get(kind)
            if probe is None:
                continue
            try:
                ok, version, note = probe(host, port)
            except Exception as e:  # noqa: BLE001
                logger.debug("probe %s on %s:%d crashed: %s", kind, host, port, e)
                ok, version, note = False, "", f"probe crashed: {e}"
            if not ok:
                # 该端口不能以 `kind` 协议握手——跳过，让其它候选继续试
                continue
            key = (kind, port)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(DetectedService(
                kind=kind,
                label=_SERVICE_LABELS.get(kind, kind),
                host=host,
                port=port,
                pids=list(pids),
                process_name=pname,
                version=version,
                verified=True,
                detail=note + (f" · v{version}" if version else "")
                       + (f" · pid={pids[0]}" if pids else ""),
            ))
            break  # 同一端口上第一个确认即止（多个协议互斥）

    # 对于候选端口被占用但没有协议探针命中的，也给个未验证的条目
    # （帮助用户发现"这个端口被占了但不知道是啥"）
    for port, pids, pname in listens:
        if port not in port_to_kinds:
            # 进程名命中但不是候选端口的情况
            hint = _PROCESS_NAME_HINTS.get((pname or "").lower())
            if not hint:
                continue
            if any(r.port == port for r in results):
                continue
            results.append(DetectedService(
                kind=hint,
                label=_SERVICE_LABELS.get(hint, hint),
                host=host,
                port=port,
                pids=list(pids),
                process_name=pname,
                version="",
                verified=False,
                detail=f"进程名匹配 {pname}，但协议探针未通过（非默认端口）",
            ))
            continue
        # 候选端口但没匹配：可能是装了 ES 但关了 HTTP 等
        if any(r.port == port for r in results):
            continue
        kinds = port_to_kinds.get(port, [])
        if not kinds:
            continue
        results.append(DetectedService(
            kind=kinds[0],
            label=_SERVICE_LABELS.get(kinds[0], kinds[0]),
            host=host,
            port=port,
            pids=list(pids),
            process_name=pname,
            version="",
            verified=False,
            detail=(
                f"端口 {port} 在候选表里（{'/'.join(kinds)}），但协议探针未通过 —— "
                "可能是被其它进程占用、或服务正在启动中"
            ),
        ))

    # 按 (kind, port) 排序，让同类服务相邻
    results.sort(key=lambda r: (r.kind, r.port))

    elapsed = int((time.monotonic() - t0) * 1000)
    logger.debug(
        "detect_local_services: scanned %d LISTEN ports, found %d services in %d ms",
        len(listens), len(results), elapsed,
    )
    return results


# ---------------------------------------------------------------------------
# URL 辅助：把用户在 yaml 里配过的地址抽出 ports，喂给 detect(extra_ports=...)
# ---------------------------------------------------------------------------

def guess_extra_ports_from_settings() -> Dict[str, List[int]]:
    """尝试从当前 Settings 里抽出用户已经配的非默认端口，作为扫描补充。

    比如用户把 Postgres 跑在 15433，yaml 里 SQLALCHEMY_DATABASE_URI 也指向
    15433，这里就能帮扫描器一并把 15433 加进 postgresql 候选里。
    """
    extra: Dict[str, List[int]] = {}

    def _add(kind: str, port: Optional[int]) -> None:
        if not port or port <= 0:
            return
        lst = extra.setdefault(kind, [])
        if port not in lst:
            lst.append(port)

    try:
        from chayuan.settings import Settings
        bs = Settings.basic_settings
        kb = Settings.kb_settings

        # Redis
        url = str(getattr(bs, "REDIS_URL", "") or "")
        if url:
            try:
                _add("redis", urlparse(url).port or 0)
            except Exception:  # noqa: BLE001
                pass

        # 主业务库
        uri = str(getattr(bs, "SQLALCHEMY_DATABASE_URI", "") or "")
        if uri and not uri.startswith("sqlite"):
            try:
                u = urlparse(uri)
                backend = (u.scheme or "").split("+", 1)[0]
                if backend in ("postgresql", "postgres"):
                    _add("postgresql", u.port or 0)
                elif backend in ("mysql", "mariadb"):
                    _add("mysql", u.port or 0)
            except Exception:  # noqa: BLE001
                pass

        # MinIO
        ep = str(getattr(bs, "MINIO_ENDPOINT", "") or "")
        if ep:
            try:
                tmp = ep
                if tmp.startswith(("http://", "https://")):
                    tmp = tmp.split("://", 1)[1]
                tmp = tmp.strip("/")
                host, _, ps = tmp.partition(":")
                if ps:
                    _add("minio", int(ps))
            except Exception:  # noqa: BLE001
                pass

        # kb_settings 里 pg / milvus / es 的端口
        kbs_config = dict(getattr(kb, "kbs_config", {}) or {})
        mv = kbs_config.get("milvus") or {}
        try:
            _add("milvus", int(mv.get("port") or 0))
        except Exception:  # noqa: BLE001
            pass
        pg = kbs_config.get("pg") or {}
        pg_uri = str(pg.get("connection_uri") or "")
        if pg_uri:
            try:
                _add("postgresql", urlparse(pg_uri).port or 0)
            except Exception:  # noqa: BLE001
                pass
        es = kbs_config.get("es") or {}
        try:
            _add("elasticsearch", int(es.get("port") or 0))
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        logger.debug("guess_extra_ports_from_settings failed: %s", e)

    return extra


__all__ = [
    "DetectedService",
    "detect_local_services",
    "guess_extra_ports_from_settings",
]
