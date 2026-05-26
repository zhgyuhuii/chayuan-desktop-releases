"""install_task_manager 单测.

策略:
* 不真启动 ollama 安装(会修改本机环境);用 ``echo`` / ``sleep`` 命令
  模拟"短任务"和"被取消的任务"
* 验证:
  - start 返回 task,attach 拿到同一个
  - 重复 start 同 framework 返回已有 task(去重)
  - 终态后 attach 仍能拿到结果
  - cancel 让进程进入 cancelled 状态
  - 日志 ring buffer 上限
"""
from __future__ import annotations

import time

import pytest

from chayuan.server.config_panel.install_task_manager import (
    InstallRecipe,
    InstallTaskManager,
    get_install_manager,
)


def _wait_terminal(manager: InstallTaskManager, task_id: str, *, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = manager.get(task_id)
        if t and t.is_terminal():
            return
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not reach terminal state in {timeout}s")


def test_start_simple_echo_task_completes_done():
    mgr = InstallTaskManager()
    recipe = InstallRecipe(label="echo", cmd=["echo", "hello"])
    task = mgr.start(framework="_test_echo", recipe=recipe)
    assert task.task_id
    assert task.framework == "_test_echo"
    _wait_terminal(mgr, task.task_id)
    final = mgr.get(task.task_id)
    assert final.state == "done"
    assert final.return_code == 0
    # 日志至少包含 echo 的输出
    assert any("hello" in line for line in final.log)


def test_start_failing_command_marks_failed():
    mgr = InstallTaskManager()
    recipe = InstallRecipe(label="bad", cmd=["bash", "-c", "exit 7"])
    task = mgr.start(framework="_test_fail", recipe=recipe)
    _wait_terminal(mgr, task.task_id)
    final = mgr.get(task.task_id)
    assert final.state == "failed"
    assert final.return_code == 7


def test_start_nonexistent_command_marks_failed_without_crash():
    mgr = InstallTaskManager()
    recipe = InstallRecipe(label="nope", cmd=["__definitely_not_a_real_binary__"])
    task = mgr.start(framework="_test_nx", recipe=recipe)
    _wait_terminal(mgr, task.task_id, timeout=2.0)
    final = mgr.get(task.task_id)
    assert final.state == "failed"
    assert "命令不存在" in final.error or final.error


def test_start_dedupes_when_active_task_exists():
    mgr = InstallTaskManager()
    # 长任务: sleep 30s,确保第二次 start 时 t1 还活着 + cancel 留时间
    recipe = InstallRecipe(label="long", cmd=["sleep", "30"])
    t1 = mgr.start(framework="_test_dedup", recipe=recipe)
    # 让 popen 启起来再 dedup
    time.sleep(0.2)
    # 立刻再 start 同 framework — 应当返回 t1, 不开新 task
    t2 = mgr.start(framework="_test_dedup", recipe=recipe)
    assert t1.task_id == t2.task_id
    # 终止以释放;cancel 全流程最长 ~6s (SIGTERM + 3s wait + SIGKILL + 3s wait)
    mgr.cancel(t1.task_id)
    _wait_terminal(mgr, t1.task_id, timeout=10.0)


def test_attach_returns_active_task():
    mgr = InstallTaskManager()
    recipe = InstallRecipe(label="long", cmd=["sleep", "3"])
    task = mgr.start(framework="_test_attach", recipe=recipe)
    attached = mgr.attach("_test_attach")
    assert attached is not None
    assert attached.task_id == task.task_id
    mgr.cancel(task.task_id)
    _wait_terminal(mgr, task.task_id, timeout=4.0)


def test_attach_returns_terminal_task_for_30min():
    """终态 task 仍保留给 attach (用户重开弹窗看到结果)。"""
    mgr = InstallTaskManager()
    recipe = InstallRecipe(label="echo", cmd=["echo", "x"])
    task = mgr.start(framework="_test_terminal_attach", recipe=recipe)
    _wait_terminal(mgr, task.task_id)
    again = mgr.attach("_test_terminal_attach")
    assert again is not None
    assert again.task_id == task.task_id
    assert again.state == "done"


def test_cancel_marks_task_cancelled():
    mgr = InstallTaskManager()
    recipe = InstallRecipe(label="long", cmd=["sleep", "10"])
    task = mgr.start(framework="_test_cancel", recipe=recipe)
    # 等几十 ms 让 popen 起来
    time.sleep(0.2)
    ok = mgr.cancel(task.task_id)
    assert ok
    _wait_terminal(mgr, task.task_id, timeout=8.0)
    final = mgr.get(task.task_id)
    assert final.state == "cancelled"


def test_cancel_unknown_task_returns_false():
    mgr = InstallTaskManager()
    assert mgr.cancel("doesnt-exist") is False


def test_cancel_terminal_task_returns_false():
    mgr = InstallTaskManager()
    recipe = InstallRecipe(label="echo", cmd=["echo", "y"])
    task = mgr.start(framework="_test_cancel_term", recipe=recipe)
    _wait_terminal(mgr, task.task_id)
    assert mgr.cancel(task.task_id) is False


def test_log_ring_buffer_caps():
    """生成 600 行输出, 期望 log 不超过 LOG_CAP (500)。"""
    mgr = InstallTaskManager()
    # 用 seq 命令 / printf 循环生成
    recipe = InstallRecipe(
        label="bulk", cmd=["bash", "-c",
                           "for i in $(seq 1 600); do echo line-$i; done"],
    )
    task = mgr.start(framework="_test_cap", recipe=recipe)
    _wait_terminal(mgr, task.task_id, timeout=8.0)
    final = mgr.get(task.task_id)
    assert final.state == "done"
    # 留有 [recipe] / [cmd] / [end] 标记 + 实际内容,总数 <= LOG_CAP
    assert len(final.log) <= mgr._LOG_CAP


def test_singleton_returns_same_instance():
    a = get_install_manager()
    b = get_install_manager()
    assert a is b


def test_list_active_returns_only_running():
    mgr = InstallTaskManager()
    recipe_quick = InstallRecipe(label="q", cmd=["echo", "1"])
    recipe_slow = InstallRecipe(label="s", cmd=["sleep", "5"])
    t_done = mgr.start(framework="_t_active_done", recipe=recipe_quick)
    _wait_terminal(mgr, t_done.task_id)
    t_running = mgr.start(framework="_t_active_running", recipe=recipe_slow)
    actives = mgr.list_active()
    active_ids = {t.task_id for t in actives}
    assert t_running.task_id in active_ids
    assert t_done.task_id not in active_ids
    mgr.cancel(t_running.task_id)


# ---------------------------------------------------------------------------
# start_service (新加的 daemon 启动入口, 给"已安装·未启动"卡片用)
# ---------------------------------------------------------------------------

def test_start_service_returns_none_for_unknown_framework():
    mgr = InstallTaskManager()
    assert mgr.start_service(framework="not-a-framework") is None


def test_start_service_known_framework_creates_task():
    """ollama 在 _START_RECIPES 里有 nohup 配方;start_service 应返回 task。

    nohup 命令立即返回 0(后台 fork),所以等 task terminal 不会久等。
    本测试不验证 ollama 真起来(那需要装 ollama),只验证 task lifecycle。
    """
    from chayuan.server.config_panel.install_task_manager import _START_RECIPES
    assert "ollama" in _START_RECIPES
    mgr = InstallTaskManager()
    task = mgr.start_service(framework="ollama")
    assert task is not None
    assert task.framework == "start-ollama"
    # nohup 立即返回; 但 ollama 二进制可能不在 PATH (失败也行,只要 task 终止)
    _wait_terminal(mgr, task.task_id, timeout=4.0)
    final = mgr.get(task.task_id)
    assert final.is_terminal()


def test_subprocess_output_with_non_gbk_bytes_does_not_crash():
    """回归:子进程输出含非 GBK 字节时不应崩 runner。

    中文 Windows 默认编码是 GBK(cp936)。pip install 的输出(wheel 名 /
    进度条 / UTF-8 字符)含 0x9e 之类在 GBK 下非法的字节,若 Popen 不显式
    指定 encoding,text=True 会用平台默认编码解码 → UnicodeDecodeError 崩。
    本测试让子进程直接吐出 0x9e 等原始字节,验证改后能以 UTF-8 + replace
    容错解码,task 正常终止而不是 crash。
    """
    import sys

    # Python 子进程往 stdout 写含 0x9e / UTF-8 多字节的原始 bytes;
    # 用 sys.executable 保证跨平台(Windows 上没有 bash)。
    code = (
        "import sys; "
        "sys.stdout.buffer.write(b'before \\x9e middle \\xe4\\xb8\\xad after\\n'); "
        "sys.stdout.buffer.flush()"
    )
    mgr = InstallTaskManager()
    recipe = InstallRecipe(label="nongbk", cmd=[sys.executable, "-c", code])
    task = mgr.start(framework="_test_nongbk", recipe=recipe)
    _wait_terminal(mgr, task.task_id, timeout=8.0)
    final = mgr.get(task.task_id)
    # 关键:没有崩成 failed-with-crash,return_code 正常为 0
    assert final.state == "done", f"state={final.state} error={final.error!r}"
    assert final.return_code == 0
    # 没有任何 [end] crash: UnicodeDecodeError 行
    assert not any("crash" in line and "codec" in line for line in final.log)
    # 输出仍被捕获(0x9e 被 replace 成 U+FFFD,可读部分保留)
    assert any("before" in line and "after" in line for line in final.log)


def test_start_recipes_table_covers_main_runtimes():
    """主流框架都得有启动配方。"""
    from chayuan.server.config_panel.install_task_manager import _START_RECIPES
    for fw in ("ollama", "vllm", "infinity", "comfyui", "llamacpp", "onlyoffice"):
        assert fw in _START_RECIPES, f"{fw} 缺启动配方"


def test_start_service_recipes_are_runnable():
    """每条启动 recipe 都该可运行 — 要么 requires 字段非空(用 PATH 查),
    要么 cmd[0] 是绝对路径(如 sys.executable)或跨平台 helper(bash/cmd)。
    """
    import os
    from chayuan.server.config_panel.install_task_manager import _START_RECIPES
    for fw, recipe in _START_RECIPES.items():
        if recipe.requires:
            continue  # 显式声明 PATH 命令依赖,OK
        # 否则 cmd[0] 必须是绝对路径或跨平台 launcher
        first = recipe.cmd[0] if recipe.cmd else ""
        ok = (
            os.path.isabs(first)        # /usr/bin/python 等
            or first in ("bash", "cmd", "powershell")  # 跨平台 launcher
            or first == "docker"        # docker 二进制几乎都在 PATH
        )
        assert ok, f"{fw}: 既无 requires 也非 abs path / 跨平台 helper: {first!r}"
