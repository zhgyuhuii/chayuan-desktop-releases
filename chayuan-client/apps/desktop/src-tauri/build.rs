use std::fs;
use std::path::Path;

/// 在 Tauri build 前确保 bundle.resources glob 至少能匹配一个文件,否则
/// `tauri_build::build()` 内部 glob 展开报 "path not found or didn't match any files"
/// 直接 abort。
///
/// 背景:`tauri.conf.json` 的 `bundle.resources` 写了 `bundled_models/**/*`
/// 和 `services/**/*`,但 dev 模式下这俩目录是空的(集成版打包时由
/// chayuan-server/packaging/pyinstaller/build.py sync_* 填,dev 走不到那条路径)。
/// 这里在 build.rs 顶部塞个空 sentinel 文件,glob 至少能匹配到一个。
fn ensure_glob_sentinel(dir: &str) {
    let p = Path::new(dir);
    let _ = fs::create_dir_all(p);
    let sentinel = p.join(".placeholder");
    if !sentinel.exists() {
        let _ = fs::write(&sentinel, b"# tauri bundle.resources glob sentinel\n");
    }
    println!("cargo:rerun-if-changed={}", sentinel.display());
}

fn main() {
    ensure_glob_sentinel("bundled_models");
    ensure_glob_sentinel("services");
    tauri_build::build()
}
