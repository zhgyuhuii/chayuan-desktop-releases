// 防止 Windows release 弹出额外控制台
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    chayuan_desktop_lib::run();
}
