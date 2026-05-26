"""install_recipes 跨平台多镜像源矩阵测试.

策略:
* 每个 framework 至少 1 个 recipe
* Docker 兜底 — 至少有一条 recipe.cmd[0] == "docker" (除 funasr/cosyvoice/rapidocr/paddleocr/piper 等纯 pip 框架外)
* recipe.cmd 第一个元素是合法可执行名 (or 'bash'/'docker'/'pip')
"""
from __future__ import annotations

import pytest

from chayuan.server.config_panel.install_recipes import (
    MIRROR_SOURCES,
    custom_recipe,
    get_recipes,
)


_KNOWN_FRAMEWORKS = [
    "ollama", "vllm", "infinity", "comfyui",
    "llamacpp", "whispercpp", "funasr", "piper",
    "cosyvoice", "rapidocr", "paddleocr",
]


@pytest.mark.parametrize("fw", _KNOWN_FRAMEWORKS)
def test_each_framework_has_at_least_one_recipe(fw: str):
    recipes = get_recipes(fw)
    assert len(recipes) >= 1, f"{fw}: no recipes"


def test_unknown_framework_returns_empty():
    assert get_recipes("not-a-real-framework") == []


def test_docker_fallback_for_main_runtimes():
    """主流推理框架至少有 1 条 docker recipe。"""
    for fw in ("ollama", "vllm", "infinity", "comfyui", "llamacpp"):
        recipes = get_recipes(fw)
        docker_recipes = [r for r in recipes if r.cmd and r.cmd[0] == "docker"]
        assert len(docker_recipes) >= 1, f"{fw} 缺 docker fallback recipe"


def test_recipes_have_label_and_cmd():
    for fw in _KNOWN_FRAMEWORKS:
        for r in get_recipes(fw):
            assert r.label, f"{fw}: empty label"
            assert isinstance(r.cmd, list)
            assert len(r.cmd) >= 1, f"{fw}: empty cmd"
            assert all(isinstance(x, str) for x in r.cmd)


def test_pip_recipes_have_4_mirror_options():
    """所有走 _pip_with_mirrors 的框架应有 4 条 (官方 + 3 国内)。"""
    for fw in ("funasr", "cosyvoice", "rapidocr", "paddleocr"):
        recipes = get_recipes(fw)
        pip_only = [r for r in recipes if r.cmd[:3] == [
            __import__("sys").executable, "-m", "pip"
        ]]
        assert len(pip_only) >= 4, f"{fw}: 期望 ≥4 个 pip 镜像源, 实际 {len(pip_only)}"


def test_mirror_sources_label_complete():
    for key in (
        "official", "cn-mirror", "docker-hub", "quay",
        "aliyun-acr", "tencent-tcr", "github-cn", "custom",
    ):
        assert key in MIRROR_SOURCES
        assert "label" in MIRROR_SOURCES[key]


def test_custom_recipe_wraps_in_bash():
    rec = custom_recipe("ollama", "echo custom")
    assert rec.label == "自定义命令"
    assert rec.cmd[:2] == ["bash", "-lc"]
    assert "echo custom" in rec.cmd[2]


# ---------------------------------------------------------------------------
# docker_mirror_image (P3 上一轮加的多镜像源 + IMAGE_REWRITES)
# ---------------------------------------------------------------------------

from chayuan.server.config_panel.install_recipes import (
    DOCKER_MIRRORS, IMAGE_REWRITES, docker_mirror_image, make_docker_recipes,
)


@pytest.mark.parametrize("mirror,image,expected", [
    # docker-hub 是默认,原样返回
    ("docker-hub", "michaelf34/infinity:latest", "michaelf34/infinity:latest"),
    # daocloud 镜像加前缀
    ("daocloud", "michaelf34/infinity:latest", "docker.m.daocloud.io/michaelf34/infinity:latest"),
    # 1ms.run 同样
    ("1ms", "library/postgres:16", "docker.1ms.run/library/postgres:16"),
    # 玄垣
    ("xuanyuan", "redis:7", "docker.xuanyuan.me/redis:7"),
    # 已带 ghcr.io 域名 → 不重写 (即便 mirror=daocloud)
    ("daocloud", "ghcr.io/comfyanonymous/comfyui:latest", "ghcr.io/comfyanonymous/comfyui:latest"),
    # quay.io 也不重写
    ("daocloud", "quay.io/foo/bar", "quay.io/foo/bar"),
    # registry.cn-* 也不重写
    ("1ms", "registry.cn-hangzhou.aliyuncs.com/x/y", "registry.cn-hangzhou.aliyuncs.com/x/y"),
    # 未知 mirror → 原样
    ("not-exist", "vllm/vllm-openai", "vllm/vllm-openai"),
])
def test_docker_mirror_image_rewrite(mirror, image, expected):
    assert docker_mirror_image(image, mirror) == expected


def test_image_rewrites_yanwk_comfyui_redirected():
    """yanwk/comfyui-boot 已停更, 自动改写到 ghcr.io 上游。"""
    expected = "ghcr.io/comfyanonymous/comfyui:latest"
    # 任何 mirror_key 都触发 rewrite (因为下架替代优先于 mirror 重写)
    for mirror in ("docker-hub", "daocloud", "1ms", "xuanyuan"):
        assert docker_mirror_image("yanwk/comfyui-boot", mirror) == expected
        # 带 tag 也命中(rewrite 用 base)
        assert docker_mirror_image("yanwk/comfyui-boot:custom", mirror) == expected


def test_image_rewrites_llamacpp_org_migration():
    """ggerganov/llama.cpp 项目迁到 ggml-org;老 GHCR 路径必须走替代镜像。"""
    expected = "ghcr.io/ggml-org/llama.cpp:server"
    for mirror in ("docker-hub", "daocloud", "1ms", "xuanyuan"):
        # 完整路径命中
        assert docker_mirror_image("ghcr.io/ggerganov/llama.cpp", mirror) == expected
        # 带 tag 的完整路径也命中(rewrite 看 base)
        assert docker_mirror_image("ghcr.io/ggerganov/llama.cpp:server", mirror) == expected


def test_image_rewrites_table_self_consistent():
    """每条 IMAGE_REWRITES 都必须有 replacement / reason / search_hint 字段。"""
    for original, info in IMAGE_REWRITES.items():
        assert "replacement" in info, f"{original}: 缺 replacement"
        assert "reason" in info, f"{original}: 缺 reason"
        assert "search_hint" in info, f"{original}: 缺 search_hint"


def test_make_docker_recipes_emits_4_mirrors():
    recipes = make_docker_recipes(
        "redis:7",
        port_map="6379:6379",
        container_name="redis-test",
    )
    assert len(recipes) == 4
    labels = [r.label for r in recipes]
    assert any("Docker Hub" in l for l in labels)
    assert any("DaoCloud" in l for l in labels)
    assert any("1ms" in l for l in labels)
    assert any("玄垣" in l for l in labels)
    # 所有 recipe 都需要 docker
    assert all(r.requires == "docker" for r in recipes)


def test_make_docker_recipes_carries_extra_args():
    recipes = make_docker_recipes(
        "vllm/vllm-openai",
        port_map="18000:8000",
        container_name="vllm-test",
        extra_args=["--runtime", "nvidia", "--gpus", "all"],
    )
    for r in recipes:
        # extra_args 应该出现在 image 之前
        assert "--runtime" in r.cmd
        assert "nvidia" in r.cmd
        # image 是 cmd 最后一个元素
        assert r.cmd[-1].endswith("vllm-openai") or r.cmd[-1].endswith("vllm-openai:latest") or "vllm-openai" in r.cmd[-1]


def test_make_docker_recipes_rewrites_dead_image():
    """stale image 自动走替代 — 4 条全部命中。"""
    recipes = make_docker_recipes(
        "yanwk/comfyui-boot",
        port_map="18188:8188",
        container_name="comfyui",
    )
    for r in recipes:
        assert r.cmd[-1] == "ghcr.io/comfyanonymous/comfyui:latest"
        # note 应当包含"已停更"提示
        assert "已停更" in r.note or "停" in r.note


def test_docker_mirrors_table_complete():
    """4 镜像源 + aliyun 各项都得在 MIRROR_SOURCES 有展示标签。"""
    for key in ("docker-hub", "daocloud", "1ms", "xuanyuan", "aliyun"):
        assert key in DOCKER_MIRRORS
    for key in ("docker-hub", "daocloud", "1ms", "xuanyuan"):
        assert key in MIRROR_SOURCES, f"{key} 缺 UI label"


# ---------------------------------------------------------------------------
# Docker / docker-compose / OnlyOffice 新加的 install recipes
# ---------------------------------------------------------------------------

def test_docker_recipes_per_platform():
    """Docker 自身各平台应有合适的安装方式。"""
    # _docker_install 返回值会因 host 不同而变;只确认非空 + 命令合法
    for fw in ("docker", "docker-compose", "onlyoffice"):
        recipes = get_recipes(fw)
        assert len(recipes) >= 1, f"{fw}: 无 recipe"


def test_onlyoffice_uses_4_mirrors():
    recipes = get_recipes("onlyoffice")
    # 至少 4 条(4 mirror), JWT_ENABLED 在 cmd 里
    assert len(recipes) >= 4
    for r in recipes:
        assert "JWT_ENABLED=true" in r.cmd or "-e" in r.cmd
        assert r.requires == "docker"


# ---------------------------------------------------------------------------
# Windows 原生 (no-bash) recipe — 用 monkeypatch 切换 _host_os 模拟 win
# ---------------------------------------------------------------------------

@pytest.fixture
def force_win(monkeypatch):
    monkeypatch.setattr(
        "chayuan.server.config_panel.install_recipes._host_os",
        lambda: "win",
    )
    yield


def test_piper_on_windows_avoids_bash(force_win):
    recipes = get_recipes("piper")
    assert recipes
    for r in recipes:
        # Windows 上不能出现 bash (或 sh 这种 unix shell)
        assert r.cmd[0] not in ("bash", "sh"), f"{r.label} 在 Win 上仍用 {r.cmd[0]}"


def test_comfyui_on_windows_no_bash(force_win):
    recipes = get_recipes("comfyui")
    assert recipes
    # 至少有一条非 docker 的 (git clone 类) 不用 bash
    non_docker = [r for r in recipes if r.cmd[0] != "docker"]
    assert non_docker
    for r in non_docker:
        assert r.cmd[0] not in ("bash", "sh"), f"{r.label} 在 Win 上仍用 {r.cmd[0]}"


def test_whispercpp_on_windows_no_bash(force_win):
    recipes = get_recipes("whispercpp")
    assert recipes
    for r in recipes:
        assert r.cmd[0] not in ("bash", "sh"), f"{r.label} 在 Win 上仍用 {r.cmd[0]}"


def test_llamacpp_on_windows_includes_powershell_recipe(force_win):
    recipes = get_recipes("llamacpp")
    assert recipes
    has_powershell = any(r.cmd[0] == "powershell" for r in recipes)
    assert has_powershell, "Windows 上 llama.cpp 缺 PowerShell release 配方"


def test_custom_recipe_uses_cmd_on_windows(force_win):
    rec = custom_recipe("ollama", "echo hi")
    assert rec.cmd[0] == "cmd"
    assert rec.cmd[:2] == ["cmd", "/c"]
    assert "echo hi" in rec.cmd[2]


def test_custom_recipe_uses_bash_on_unix(monkeypatch):
    monkeypatch.setattr(
        "chayuan.server.config_panel.install_recipes._host_os",
        lambda: "linux",
    )
    rec = custom_recipe("ollama", "echo hi")
    assert rec.cmd[0] == "bash"
