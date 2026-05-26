"""``sync_services`` 行为测试。

集成版打包时,按 target triple 把对应 platform 子目录拷到 src-tauri/services/。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def build_module(tmp_path, monkeypatch):
    """动态导入 build.py,把 ROOT/DESKTOP_SRC_TAURI 替换成 tmp_path"""
    build_py = Path(__file__).resolve().parent.parent / "packaging" / "pyinstaller" / "build.py"
    spec = importlib.util.spec_from_file_location("build_mod", build_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 重写 ROOT/DESKTOP_SRC_TAURI 指向 tmp_path 镜像
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "DESKTOP_SRC_TAURI", tmp_path / "desktop_src_tauri")
    monkeypatch.setattr(mod, "SERVICES_SRC", tmp_path / "vendor" / "services")
    monkeypatch.setattr(mod, "DESKTOP_SERVICES_DIR", tmp_path / "desktop_src_tauri" / "services")
    # 清掉可能干扰的 env
    monkeypatch.delenv("CHAYUAN_VENDOR_PLATFORM", raising=False)
    return mod


def test_triple_to_vendor_subdir_basic(build_module):
    assert build_module.triple_to_vendor_subdir("x86_64-pc-windows-msvc") == "win-x64"
    assert build_module.triple_to_vendor_subdir("aarch64-pc-windows-msvc") == "win-arm64"
    assert build_module.triple_to_vendor_subdir("aarch64-apple-darwin") == "macos-arm64"
    assert build_module.triple_to_vendor_subdir("x86_64-apple-darwin") == "macos-x64"
    assert build_module.triple_to_vendor_subdir("x86_64-unknown-linux-gnu") == "linux-x64"
    assert build_module.triple_to_vendor_subdir("aarch64-unknown-linux-gnu") == "linux-arm64"


def test_triple_to_vendor_subdir_env_override(build_module, monkeypatch):
    monkeypatch.setenv("CHAYUAN_VENDOR_PLATFORM", "win-x64-noavx")
    assert build_module.triple_to_vendor_subdir("x86_64-pc-windows-msvc") == "win-x64-noavx"


def test_triple_to_vendor_subdir_unknown_returns_empty(build_module):
    assert build_module.triple_to_vendor_subdir("riscv64gc-unknown-linux-gnu") == ""


def test_sync_services_integrated_picks_target_subdir(build_module, tmp_path):
    """target=win-x64 时,只拷 vendor/services/llama-server/win-x64/*,扁平到目标。"""
    base = tmp_path / "vendor" / "services" / "llama-server"
    (base / "win-x64").mkdir(parents=True)
    (base / "win-x64" / "llama-server.exe").write_bytes(b"M" + b"Z" + b"x" * 1024)
    (base / "win-x64" / "ggml-cpu.dll").write_bytes(b"y" * 512)
    (base / "win-x64" / "VERSION").write_text("b4404\n2026-05-15\n")
    # 其它平台也存在,但不应被拷
    (base / "linux-x64").mkdir(parents=True)
    (base / "linux-x64" / "llama-server").write_bytes(b"\x7fELF" + b"x" * 1024)

    build_module.sync_services(lite=False, target_triple="x86_64-pc-windows-msvc")

    dst = tmp_path / "desktop_src_tauri" / "services" / "llama-server"
    assert (dst / "llama-server.exe").is_file()
    assert (dst / "ggml-cpu.dll").is_file()
    assert (dst / "VERSION").read_text().startswith("b4404")
    # platform 子目录不应在目标里出现(已扁平化)
    assert not (dst / "win-x64").exists()
    assert not (dst / "linux-x64").exists()
    # Linux 文件不应混入 Win 包
    assert not (dst / "llama-server").exists()


def test_sync_services_skips_missing_platform_subdir(build_module, tmp_path, capsys):
    """target=macos-x64 时 llama 没 macos-x64/ 子目录 → 跳过,不抛错。"""
    base = tmp_path / "vendor" / "services" / "llama-server"
    (base / "linux-x64").mkdir(parents=True)
    (base / "linux-x64" / "llama-server").write_bytes(b"\x7fELF" + b"x" * 1024)

    build_module.sync_services(lite=False, target_triple="x86_64-apple-darwin")

    dst_engine = tmp_path / "desktop_src_tauri" / "services" / "llama-server"
    # engine 目录不应被创建(因为没东西可拷)
    assert not dst_engine.exists()
    captured = capsys.readouterr()
    assert "没 macos-x64/" in captured.out or "没 macos-x64" in captured.out


def test_sync_services_skips_empty_gitkeep_placeholder(build_module, tmp_path, capsys):
    """target=linux-x64 时 whisper-server/linux-x64/ 只有 .gitkeep → 跳过,不抛错。"""
    base = tmp_path / "vendor" / "services" / "whisper-server"
    (base / "linux-x64").mkdir(parents=True)
    (base / "linux-x64" / ".gitkeep").touch()

    build_module.sync_services(lite=False, target_triple="x86_64-unknown-linux-gnu")

    dst_engine = tmp_path / "desktop_src_tauri" / "services" / "whisper-server"
    assert not dst_engine.exists()
    captured = capsys.readouterr()
    assert "空占位" in captured.out


def test_sync_services_lite_also_syncs_binaries(build_module, tmp_path):
    """lite 也应把 llama-server / whisper-server 二进制同步进去 —— 否则
    bundle 了 embedding/rerank/asr 模型权重但 launcher binary 缺,
    start() 直接 state=failed "llama-server.exe 不在 vendor/services/..."。

    历史 lite 早返回逻辑已删,见 build.py sync_services 注释。
    """
    base = tmp_path / "vendor" / "services" / "llama-server"
    (base / "linux-x64").mkdir(parents=True)
    (base / "linux-x64" / "llama-server").write_bytes(b"\x7fELF" + b"x" * 1024)

    whisper_base = tmp_path / "vendor" / "services" / "whisper-server"
    (whisper_base / "linux-x64").mkdir(parents=True)
    (whisper_base / "linux-x64" / "whisper-server").write_bytes(b"\x7fELF" + b"y" * 1024)

    build_module.sync_services(lite=True, target_triple="x86_64-unknown-linux-gnu")

    dst = tmp_path / "desktop_src_tauri" / "services"
    assert dst.is_dir()
    # lite 必须打 llama-server(给 embedding/rerank 用)
    assert (dst / "llama-server" / "llama-server").is_file(), \
        "lite 必须同步 llama-server,否则 embedding/rerank sidecar 起不来"
    # lite 必须打 whisper-server(给 asr 用)
    assert (dst / "whisper-server" / "whisper-server").is_file(), \
        "lite 必须同步 whisper-server,否则 asr sidecar 起不来"
    # 不应被 .gitkeep 占空(老行为)
    assert not (dst / ".gitkeep").exists()


def test_sync_services_missing_src_does_not_crash(build_module, tmp_path):
    """vendor/services/ 不存在时应 graceful 退化"""
    build_module.sync_services(lite=False, target_triple="x86_64-unknown-linux-gnu")

    dst = tmp_path / "desktop_src_tauri" / "services"
    assert dst.is_dir()
    assert (dst / ".gitkeep").is_file()


def test_sync_services_size_guard_rejects_2gb(build_module, tmp_path):
    """单文件 ≥ 2 GB 应 abort (Windows installer 硬限制)"""
    base = tmp_path / "vendor" / "services" / "huge" / "linux-x64"
    base.mkdir(parents=True)
    huge = base / "huge.bin"
    with open(huge, "wb") as f:
        f.truncate(build_module._WIN_INSTALLER_FILE_LIMIT + 1)

    with pytest.raises(SystemExit) as exc:
        build_module.sync_services(lite=False, target_triple="x86_64-unknown-linux-gnu")
    assert exc.value.code == 2


def test_verify_service_binaries_passes_when_present_and_runnable(build_module, tmp_path):
    """所有 engine 都有合法 binary 时,verify 通过。"""
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "llama-server"
    eng.mkdir(parents=True)
    # 用 ELF magic + 足量字节,模拟一个 Linux x64 binary
    bin_path = eng / "llama-server"
    bin_path.write_bytes(b"\x7fELF" + b"\x00" * (200 * 1024))

    # cross-build:host != target,跳过 --help 实测
    build_module.verify_service_binaries(target_subdir="linux-x64", host_subdir="win-x64")


def test_verify_service_binaries_fails_on_missing_binary(build_module, tmp_path):
    """engine 目录里没主 binary → SystemExit(2)。"""
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "llama-server"
    eng.mkdir(parents=True)
    # 只有 dll 没 exe
    (eng / "ggml-cpu.dll").write_bytes(b"x" * (200 * 1024))

    with pytest.raises(SystemExit) as exc:
        build_module.verify_service_binaries(target_subdir="win-x64", host_subdir="win-x64")
    assert exc.value.code == 2


def test_verify_service_binaries_fails_on_too_small(build_module, tmp_path):
    """binary 异常小(< 100 KB)→ 疑似占位,SystemExit。"""
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "llama-server"
    eng.mkdir(parents=True)
    (eng / "llama-server").write_bytes(b"\x7fELF" + b"x" * 100)  # 100 字节

    with pytest.raises(SystemExit) as exc:
        build_module.verify_service_binaries(target_subdir="linux-x64", host_subdir="win-x64")
    assert exc.value.code == 2


def test_verify_service_binaries_fails_on_magic_mismatch(build_module, tmp_path):
    """target=win-x64 但 binary 是 ELF(把 Linux exe 错打进 Win 包)→ SystemExit。"""
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "llama-server"
    eng.mkdir(parents=True)
    # Win 期望 MZ,这里给 ELF
    (eng / "llama-server.exe").write_bytes(b"\x7fELF" + b"x" * (200 * 1024))

    with pytest.raises(SystemExit) as exc:
        build_module.verify_service_binaries(target_subdir="win-x64", host_subdir="macos-arm64")
    assert exc.value.code == 2


def test_verify_service_binaries_empty_services_dir(build_module, tmp_path):
    """services/ 空时不抛错(轻量版场景)。"""
    (tmp_path / "desktop_src_tauri" / "services").mkdir(parents=True)
    build_module.verify_service_binaries(target_subdir="linux-x64", host_subdir="linux-x64")


def test_verify_service_binaries_linker_error_is_soft_warning(build_module, tmp_path, monkeypatch, capsys):
    """host 的 GLIBC 比 binary 要求的旧 → --help 命中 linker 错误 → 软警告不 fail。"""
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "llama-server"
    eng.mkdir(parents=True)
    bin_path = eng / "llama-server"
    bin_path.write_bytes(b"\x7fELF" + b"x" * (200 * 1024))

    class FakeResult:
        returncode = 0
        stdout = b""
        stderr = b"./llama-server: /lib64/libstdc++.so.6: version `GLIBCXX_3.4.32' not found\n"

    monkeypatch.setattr(build_module.subprocess, "run",
                        lambda *a, **kw: FakeResult())

    # 不应抛 SystemExit
    build_module.verify_service_binaries(target_subdir="linux-x64", host_subdir="linux-x64")
    captured = capsys.readouterr()
    assert "runtime-linker 错误" in captured.out
    assert "glibc" in captured.out.lower()


def test_verify_service_binaries_shared_library_error_is_soft_warning(
    build_module, tmp_path, monkeypatch, capsys
):
    """build host 缺某个系统级 .so → --help 命中 "shared libraries" 错 → 软警告不 fail。

    whisper-server 是动态链接的,捆绑的 .so 已通过 LD_LIBRARY_PATH 解决;万一
    host 还缺某个系统级 .so,这也是 host 环境问题,不代表二进制对目标平台坏。
    """
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "whisper-server"
    eng.mkdir(parents=True)
    bin_path = eng / "whisper-server"
    bin_path.write_bytes(b"\x7fELF" + b"x" * (200 * 1024))

    class FakeResult:
        returncode = 127
        stdout = b""
        stderr = (
            b"whisper-server: error while loading shared libraries: "
            b"libwhisper.so.1: cannot open shared object file: "
            b"No such file or directory\n"
        )

    monkeypatch.setattr(build_module.subprocess, "run",
                        lambda *a, **kw: FakeResult())

    # 不应抛 SystemExit
    build_module.verify_service_binaries(target_subdir="linux-x64", host_subdir="linux-x64")
    captured = capsys.readouterr()
    assert "runtime-linker 错误" in captured.out


def test_verify_service_binaries_sets_library_path_env(
    build_module, tmp_path, monkeypatch
):
    """--help 子进程的 env 必须把 binary 自身目录加进 LD_LIBRARY_PATH。"""
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "whisper-server"
    eng.mkdir(parents=True)
    bin_path = eng / "whisper-server"
    bin_path.write_bytes(b"\x7fELF" + b"x" * (200 * 1024))

    captured_env = {}

    class FakeResult:
        returncode = 0
        stdout = b"usage: whisper-server\n"
        stderr = b""

    def fake_run(*a, **kw):
        captured_env["env"] = kw.get("env")
        return FakeResult()

    monkeypatch.setattr(build_module.subprocess, "run", fake_run)

    build_module.verify_service_binaries(target_subdir="linux-x64", host_subdir="linux-x64")

    env = captured_env["env"]
    assert env is not None
    lib_var = "DYLD_LIBRARY_PATH" if build_module.sys.platform == "darwin" else "LD_LIBRARY_PATH"
    assert str(eng) in env[lib_var].split(build_module.os.pathsep)


def test_verify_service_binaries_fails_on_segfault(build_module, tmp_path, monkeypatch):
    """--help 非常规退出码(SIGSEGV 等)→ fail。"""
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "llama-server"
    eng.mkdir(parents=True)
    bin_path = eng / "llama-server"
    bin_path.write_bytes(b"\x7fELF" + b"x" * (200 * 1024))

    class FakeResult:
        returncode = -11  # SIGSEGV
        stdout = b""
        stderr = b""

    monkeypatch.setattr(build_module.subprocess, "run",
                        lambda *a, **kw: FakeResult())

    with pytest.raises(SystemExit) as exc:
        build_module.verify_service_binaries(target_subdir="linux-x64", host_subdir="linux-x64")
    assert exc.value.code == 2


def test_verify_service_binaries_fails_on_exec_format_error(build_module, tmp_path, monkeypatch):
    """OSError(把 Win exe 错放进 Linux 包,exec format error)→ fail。"""
    services = tmp_path / "desktop_src_tauri" / "services"
    eng = services / "llama-server"
    eng.mkdir(parents=True)
    bin_path = eng / "llama-server"
    bin_path.write_bytes(b"\x7fELF" + b"x" * (200 * 1024))

    def raise_oserror(*a, **kw):
        raise OSError("Exec format error")
    monkeypatch.setattr(build_module.subprocess, "run", raise_oserror)

    with pytest.raises(SystemExit) as exc:
        build_module.verify_service_binaries(target_subdir="linux-x64", host_subdir="linux-x64")
    assert exc.value.code == 2
