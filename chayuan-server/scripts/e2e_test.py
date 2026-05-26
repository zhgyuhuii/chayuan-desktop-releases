#!/usr/bin/env python3
"""察元端到端浏览器自动化（Playwright + Chromium）。

覆盖的界面（按运行成本由低到高）：
  1. Swagger UI       /docs              — 路由注册契约
  2. OpenAPI JSON     /openapi.json      — 所有 endpoint 元数据
  3. Langfuse UI      :3000              — 观测服务可达
  4. MinIO Console    :9001              — 对象存储可达
  5. 用户旅程 (journey) — 注册 / 登录 / 建 KB / 搜索

用法：
    python scripts/e2e_test.py                          # 默认全跑
    python scripts/e2e_test.py --headed                 # 显示浏览器
    python scripts/e2e_test.py --only swagger,langfuse  # 精确选
    python scripts/e2e_test.py --junit e2e.xml          # CI 消费

设计：单文件无 pytest 依赖；失败写 screenshot 到 ``./e2e_artifacts/``。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from playwright.sync_api import (  # type: ignore
        Browser, BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright,
    )
except ImportError:
    sys.stderr.write(
        "缺少 playwright：\n"
        "  pip install playwright\n"
        "  python -m playwright install chromium\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# 结果 / 工具
# ---------------------------------------------------------------------------

@dataclass
class CaseResult:
    group: str
    name: str
    status: str   # pass / fail / skip
    elapsed_ms: float = 0.0
    message: str = ""
    screenshot: str = ""


class E2ERunner:
    def __init__(
        self, *,
        api_base: str, langfuse_base: str, minio_base: str,
        artifacts_dir: Path, headed: bool = False, timeout_ms: int = 15000,
    ):
        self.api_base = api_base.rstrip("/")
        self.langfuse_base = langfuse_base.rstrip("/")
        self.minio_base = minio_base.rstrip("/")
        self.artifacts_dir = artifacts_dir
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.headed = headed
        self.timeout_ms = timeout_ms
        self.results: List[CaseResult] = []

    # ---- 小工具 ----

    def _screenshot(self, page: Page, name: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        out = self.artifacts_dir / f"{int(time.time())}_{safe}.png"
        try:
            page.screenshot(path=str(out), full_page=True)
        except Exception:
            pass
        return str(out)

    def _case(
        self, group: str, name: str, fn: Callable[[BrowserContext], None],
        ctx: BrowserContext,
    ) -> CaseResult:
        t0 = time.time()
        try:
            fn(ctx)
            elapsed = (time.time() - t0) * 1000
            r = CaseResult(group=group, name=name, status="pass", elapsed_ms=elapsed)
        except PWTimeout as e:
            elapsed = (time.time() - t0) * 1000
            r = CaseResult(
                group=group, name=name, status="fail", elapsed_ms=elapsed,
                message=f"timeout: {e}",
            )
        except Exception as e:  # noqa: BLE001
            elapsed = (time.time() - t0) * 1000
            r = CaseResult(
                group=group, name=name, status="fail", elapsed_ms=elapsed,
                message=f"{type(e).__name__}: {e}",
            )
        self.results.append(r)
        tag = "[OK]  " if r.status == "pass" else "[FAIL]"
        print(f"  {tag} [{group}] {name}  ({r.elapsed_ms:.0f}ms)"
              f"{'  ' + r.message if r.message else ''}")
        return r

    def _skip(self, group: str, name: str, reason: str) -> None:
        r = CaseResult(group=group, name=name, status="skip", message=reason)
        self.results.append(r)
        print(f"  [SKIP] [{group}] {name}  {reason}")

    # ---- 组 1：Swagger / OpenAPI ----

    def run_swagger(self, ctx: BrowserContext) -> None:
        group = "1-swagger"

        def openapi_json(_ctx):
            resp = _ctx.request.get(f"{self.api_base}/openapi.json",
                                     timeout=self.timeout_ms)
            assert resp.status == 200, f"openapi.json http={resp.status}"
            doc = resp.json()
            assert doc.get("openapi"), "openapi version missing"
            paths = list((doc.get("paths") or {}).keys())
            # 注意：/healthz / /readyz 用 include_in_schema=False 故意不上 openapi；
            # 改为 direct probe 它们；其它业务路由必须出现。
            required_in_schema = [
                "/knowledge_base/list_knowledge_bases",
                "/knowledge_source/dialects",
                "/governance/policy",
                "/storage/status",
                "/image_models/",
                "/chat/v2/chat",
            ]
            missing = [p for p in required_in_schema if p not in paths]
            assert not missing, f"openapi 缺路由: {missing}"

        def health_endpoints_live(_ctx):
            for ep in ("/healthz", "/readyz"):
                r = _ctx.request.get(f"{self.api_base}{ep}", timeout=self.timeout_ms)
                assert r.status in (200, 503), f"{ep} http={r.status}"

        self._case(group, "GET /openapi.json", openapi_json, ctx)
        self._case(group, "GET /healthz /readyz", health_endpoints_live, ctx)

        def swagger_ui(_ctx):
            page = _ctx.new_page()
            page.goto(f"{self.api_base}/docs", timeout=self.timeout_ms,
                        wait_until="domcontentloaded")
            # Swagger UI 有个 id=swagger-ui 的根元素
            page.wait_for_selector("#swagger-ui", timeout=self.timeout_ms)
            title = page.title()
            assert "察元" in title or "Swagger" in title or "FastAPI" in title, \
                f"标题异常: {title!r}"
            page.close()

        self._case(group, "Swagger UI renders", swagger_ui, ctx)

    # ---- 组 2：Langfuse ----

    def run_langfuse(self, ctx: BrowserContext) -> None:
        group = "2-langfuse"
        def _fn(_ctx):
            # 先用 API 探活（NextAuth 的 /api/auth/providers 永远 200）
            r = _ctx.request.get(f"{self.langfuse_base}/api/auth/providers",
                                   timeout=self.timeout_ms)
            assert r.status == 200, f"auth/providers http={r.status}"
            providers = r.json()
            assert isinstance(providers, dict), "providers 响应非 dict"
            assert "credentials" in providers or providers, \
                f"无 credentials provider: {providers}"

            # 再 open page 做 SPA 渲染验证
            page = _ctx.new_page()
            page.goto(f"{self.langfuse_base}/auth/sign-in",
                        timeout=self.timeout_ms, wait_until="networkidle")
            # Langfuse 登录页有 email input
            page.wait_for_selector(
                'input[type="email"], input[name="email"]',
                timeout=self.timeout_ms,
            )
            page.close()

        self._case(group, "Langfuse login page", _fn, ctx)

    # ---- 组 3：MinIO Console ----

    def run_minio(self, ctx: BrowserContext) -> None:
        group = "3-minio"
        def _fn(_ctx):
            page = _ctx.new_page()
            page.goto(self.minio_base, timeout=self.timeout_ms,
                        wait_until="networkidle")
            # MinIO console 的标题必含 "MinIO"
            title = page.title()
            if "MinIO" not in title:
                self._screenshot(page, "minio_title_unexpected")
                raise AssertionError(f"MinIO title 不含预期字样: {title!r}")
            # 等登录表单渲染（username 输入框）
            page.wait_for_selector(
                'input[name="accessKey"], input[id="accessKey"], '
                'input[type="text"], input[name="username"]',
                timeout=self.timeout_ms,
            )
            page.close()

        self._case(group, "MinIO Console loads", _fn, ctx)

    # ---- 组 4：用户旅程（API 层）----

    def run_api_journey(self, ctx: BrowserContext) -> None:
        """浏览器内用 request API 走一条 user journey。
        好处：复用 browser 的 cookie jar + 与真实用户走的网络栈一致。"""
        group = "4-journey"

        state: Dict[str, Any] = {}

        def register_then_login(_ctx):
            import secrets
            name = f"e2e_{int(time.time())}_{secrets.token_hex(3)}"
            password = "e2e-passwd-123"
            # 1) 注册（察元 register 返回 201 user 对象，无 token）
            r = _ctx.request.post(
                f"{self.api_base}/auth/register",
                data={
                    "username": name,
                    "email": f"{name}@chayuan.local",
                    "password": password,
                },
                timeout=self.timeout_ms,
            )
            assert r.status in (200, 201), \
                f"register failed http={r.status}: {r.text()[:200]}"
            # 2) 登录拿 token
            r2 = _ctx.request.post(
                f"{self.api_base}/auth/login",
                data={"username": name, "password": password},
                timeout=self.timeout_ms,
            )
            assert r2.status == 200, f"login failed http={r2.status}"
            body = r2.json() or {}
            token = body.get("access_token") or (body.get("data") or {}).get("access_token")
            assert token, f"login 无 token: {body}"
            state["token"] = token
            state["user"] = name

        self._case(group, "注册 + 登录", register_then_login, ctx)

        if not state.get("token"):
            self._skip(group, "创建 KB", "无 token")
            self._skip(group, "搜索 KB", "无 token")
            self._skip(group, "删除 KB", "无 token")
            return

        auth_headers = {"Authorization": f"Bearer {state['token']}"}

        def create_kb(_ctx):
            kb_name = f"e2e_kb_{int(time.time())}"
            state["kb_name"] = kb_name
            # 不传 vector_store_type，让后端用 DEFAULT_VS_TYPE（与用户配置一致）
            r = _ctx.request.post(
                f"{self.api_base}/knowledge_base/create_knowledge_base",
                headers=auth_headers,
                data={"knowledge_base_name": kb_name, "kb_info": "e2e"},
                timeout=self.timeout_ms * 3,
            )
            assert r.status == 200, f"create_kb http={r.status}: {r.text()[:200]}"
            # 关键：察元返回 200 + code=500 代表业务失败；必须校验 body.code
            body = r.json() or {}
            code = body.get("code")
            assert code in (0, 200), f"create_kb code={code} msg={body.get('msg')}"

        self._case(group, "创建 KB", create_kb, ctx)

        def search_empty(_ctx):
            r = _ctx.request.post(
                f"{self.api_base}/knowledge_base/search_docs",
                headers=auth_headers,
                data={
                    "query": "nothing",
                    "knowledge_base_name": state.get("kb_name") or "",
                    "top_k": 3, "score_threshold": 1.0,
                    "file_name": "", "metadata": {},
                },
                timeout=self.timeout_ms * 2,
            )
            # 空 KB 应 200（返回空数组或 BaseResponse）
            assert r.status == 200, f"search_docs http={r.status}"

        self._case(group, "搜索 KB（空）", search_empty, ctx)

        # 按 KB 存储后端：查看 + 切换 + 再查看（验证持久化）
        def kb_storage_backend(_ctx):
            # 1) 读当前
            r = _ctx.request.get(
                f"{self.api_base}/knowledge_base/storage_backend",
                headers=auth_headers,
                params={"knowledge_base_name": state.get("kb_name") or ""},
                timeout=self.timeout_ms,
            )
            assert r.status == 200, f"GET storage_backend http={r.status}"
            body = r.json() or {}
            assert body.get("code") == 0, f"code={body.get('code')}"
            data = body.get("data") or {}
            assert "global" in data and "available" in data, \
                f"响应结构异常: {data}"
            assert data.get("override") in ("", None), \
                f"新建 KB 初始 override 应为空；收到 {data.get('override')!r}"
            # 2) 切到 local（一定能做，不需 MinIO）
            r2 = _ctx.request.post(
                f"{self.api_base}/knowledge_base/update_storage_backend",
                headers=auth_headers,
                data={
                    "knowledge_base_name": state.get("kb_name") or "",
                    "storage_backend": "local",
                    "migrate": False, "dry_run": False,
                },
                timeout=self.timeout_ms * 2,
            )
            assert r2.status == 200, f"update http={r2.status}"
            b2 = r2.json() or {}
            assert b2.get("code") in (0, 200), f"update code={b2.get('code')}"
            # 3) 再读，确认 override=local 已持久
            r3 = _ctx.request.get(
                f"{self.api_base}/knowledge_base/storage_backend",
                headers=auth_headers,
                params={"knowledge_base_name": state.get("kb_name") or ""},
                timeout=self.timeout_ms,
            )
            d3 = (r3.json() or {}).get("data") or {}
            assert d3.get("override") == "local", \
                f"override 未持久化：{d3.get('override')!r}"

        self._case(group, "查询/切换 KB 存储后端", kb_storage_backend, ctx)

        def delete_kb(_ctx):
            r = _ctx.request.post(
                f"{self.api_base}/knowledge_base/delete_knowledge_base",
                headers=auth_headers,
                data={"knowledge_base_name": state.get("kb_name") or ""},
                timeout=self.timeout_ms,
            )
            assert r.status == 200, f"delete_kb http={r.status}: {r.text()[:200]}"
            body = r.json() or {}
            code = body.get("code")
            assert code in (0, 200), f"delete_kb code={code} msg={body.get('msg')}"

        # ---------- 新建"图像 / SQL"数据源（验证 4 类 KB 的新入口）----------

        def create_image_source(_ctx):
            """kind=image 走 /knowledge_source/；验证白名单已开通。"""
            import secrets
            name = f"e2e_img_{int(time.time())}_{secrets.token_hex(2)}"
            state["img_src_name"] = name
            r = _ctx.request.post(
                f"{self.api_base}/knowledge_source/",
                headers=auth_headers,
                data={
                    "name": name, "kind": "image",
                    "display_name": name, "description": "e2e",
                    "dialect": "image", "host": "", "port": 0,
                    "database": name, "username": "", "password": "",
                    "options": {
                        "embedder_model": "google/siglip2-base-patch16-224",
                        "source_name": name,
                    },
                    "allowed": {}, "visibility": "private",
                },
                timeout=self.timeout_ms * 2,
            )
            assert r.status == 200, f"create image src http={r.status}: {r.text()[:200]}"
            body = r.json() or {}
            assert body.get("code") == 0, f"code={body.get('code')}"
            assert (body.get("data") or {}).get("id"), "image src id missing"
            state["img_src_id"] = body["data"]["id"]

        self._case(group, "新建图像数据源", create_image_source, ctx)

        def create_sql_source_sqlite(_ctx):
            """SQLite 数据源（无需真实 DB 服务）——验证 SQL 路径端到端。"""
            import os, secrets, tempfile, sqlite3
            # 建一个可测试的 sqlite 文件
            dbpath = os.path.join(
                tempfile.gettempdir(),
                f"e2e_{int(time.time())}_{secrets.token_hex(3)}.db",
            )
            cx = sqlite3.connect(dbpath)
            cx.execute("CREATE TABLE orders (id INT, amount REAL)")
            cx.execute("INSERT INTO orders VALUES (1, 9.9), (2, 19.9)")
            cx.commit(); cx.close()

            name = f"e2e_sql_{int(time.time())}_{secrets.token_hex(2)}"
            state["sql_src_name"] = name
            r = _ctx.request.post(
                f"{self.api_base}/knowledge_source/",
                headers=auth_headers,
                data={
                    "name": name, "kind": "sql",
                    "display_name": name, "description": "e2e",
                    "dialect": "sqlite", "host": "", "port": 0,
                    "database": dbpath, "username": "", "password": "",
                    "options": {}, "allowed": {}, "visibility": "private",
                },
                timeout=self.timeout_ms * 2,
            )
            assert r.status == 200, f"create sql src http={r.status}: {r.text()[:200]}"
            body = r.json() or {}
            assert body.get("code") == 0, f"code={body.get('code')}"

        self._case(group, "新建 SQL 数据源（sqlite）", create_sql_source_sqlite, ctx)

        def reject_unknown_kind(_ctx):
            """未知 kind 必须返回 400。防回归：代码错改成白名单过宽。"""
            r = _ctx.request.post(
                f"{self.api_base}/knowledge_source/",
                headers=auth_headers,
                data={
                    "name": f"e2e_bad_{int(time.time())}",
                    "kind": "gdrive",  # 伪装的未支持类型
                    "dialect": "", "host": "", "port": 0,
                    "database": "", "username": "", "password": "",
                    "options": {}, "allowed": {}, "visibility": "private",
                },
                timeout=self.timeout_ms,
            )
            assert r.status == 400, f"应返回 400；实际 http={r.status}"

        self._case(group, "拒绝未知 kind", reject_unknown_kind, ctx)

        self._case(group, "删除 KB", delete_kb, ctx)

    # ---- 总控 ----

    def run(self, groups: Optional[List[str]] = None) -> int:
        with sync_playwright() as p:
            # 启动 Chromium；带 --no-proxy-server 防止 Windows 系统代理把 127.0.0.1 也劫走
            launch_args = []
            if os.environ.get("DISABLE_PROXY", "1") == "1":
                launch_args = [
                    "--no-proxy-server",
                    "--proxy-bypass-list=*",
                ]
            browser = p.chromium.launch(headless=not self.headed, args=launch_args)
            ctx = browser.new_context(ignore_https_errors=True)
            ctx.set_default_timeout(self.timeout_ms)
            try:
                def should(name: str) -> bool:
                    return groups is None or name in groups
                if should("swagger"):
                    print("\n>>> 1. Swagger / OpenAPI")
                    self.run_swagger(ctx)
                if should("langfuse"):
                    print("\n>>> 2. Langfuse UI")
                    self.run_langfuse(ctx)
                if should("minio"):
                    print("\n>>> 3. MinIO Console")
                    self.run_minio(ctx)
                if should("journey"):
                    print("\n>>> 4. 用户旅程 (API)")
                    self.run_api_journey(ctx)
            finally:
                ctx.close()
                browser.close()
        return self._summarize()

    def _summarize(self) -> int:
        print("\n" + "=" * 64)
        by_group: Dict[str, Dict[str, int]] = {}
        for r in self.results:
            g = by_group.setdefault(r.group,
                                      {"pass": 0, "skip": 0, "fail": 0})
            g[r.status] += 1
        exit_code = 0
        for g, stats in by_group.items():
            tag = "[OK]  " if stats.get("fail", 0) == 0 else "[FAIL]"
            print(f"  {tag} {g:<15}  pass={stats.get('pass', 0):>2}  "
                  f"skip={stats.get('skip', 0):>2}  fail={stats.get('fail', 0):>2}")
            if stats.get("fail", 0) > 0:
                exit_code = 1
        total = len(self.results)
        p = sum(1 for r in self.results if r.status == "pass")
        s = sum(1 for r in self.results if r.status == "skip")
        f = sum(1 for r in self.results if r.status == "fail")
        print(f"  TOTAL: {total}  pass={p}  skip={s}  fail={f}")
        if f > 0:
            print("\nFailures:")
            for r in self.results:
                if r.status == "fail":
                    print(f"  - [{r.group}] {r.name}  {r.message}")
        return exit_code

    def emit_junit(self, path: str) -> None:
        from xml.etree import ElementTree as ET
        root = ET.Element("testsuites")
        grouped: Dict[str, List[CaseResult]] = {}
        for r in self.results:
            grouped.setdefault(r.group, []).append(r)
        for gname, cases in grouped.items():
            ts = ET.SubElement(root, "testsuite", {
                "name": gname, "tests": str(len(cases)),
                "failures": str(sum(1 for c in cases if c.status == "fail")),
                "skipped": str(sum(1 for c in cases if c.status == "skip")),
            })
            for c in cases:
                tc = ET.SubElement(ts, "testcase", {
                    "classname": gname, "name": c.name,
                    "time": f"{c.elapsed_ms / 1000:.3f}",
                })
                if c.status == "fail":
                    failure = ET.SubElement(tc, "failure", {"message": c.message})
                    failure.text = c.message
                elif c.status == "skip":
                    ET.SubElement(tc, "skipped", {"message": c.message})
        ET.ElementTree(root).write(path, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api-base", default="http://127.0.0.1:62581")
    p.add_argument("--langfuse-base", default="http://127.0.0.1:3000")
    p.add_argument("--minio-base", default="http://127.0.0.1:9001")
    p.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    p.add_argument("--timeout-ms", type=int, default=15000)
    p.add_argument("--artifacts-dir", default="./e2e_artifacts")
    p.add_argument("--only", default="",
                    help="只跑指定组；逗号分隔：swagger,langfuse,minio,journey")
    p.add_argument("--junit", default="",
                    help="输出 JUnit XML")
    args = p.parse_args()

    if args.only:
        groups = [g.strip() for g in args.only.split(",") if g.strip()]
    else:
        groups = ["swagger", "langfuse", "minio", "journey"]

    print(f"[e2e] api={args.api_base}")
    print(f"[e2e] langfuse={args.langfuse_base}  minio={args.minio_base}")
    print(f"[e2e] groups={groups}  headed={args.headed}")

    runner = E2ERunner(
        api_base=args.api_base,
        langfuse_base=args.langfuse_base, minio_base=args.minio_base,
        artifacts_dir=Path(args.artifacts_dir),
        headed=args.headed, timeout_ms=args.timeout_ms,
    )
    code = runner.run(groups=groups)
    if args.junit:
        runner.emit_junit(args.junit)
        print(f"\n[e2e] JUnit XML → {args.junit}")
    return code


if __name__ == "__main__":
    sys.exit(main())
