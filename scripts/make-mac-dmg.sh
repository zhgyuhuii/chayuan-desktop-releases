#!/usr/bin/env bash
# 把 mac .app 打成 .dmg —— 标准"双击 DMG → 拖到 Applications"安装体验
#
# 为啥单独写:Tauri 2 自带的 bundle_dmg.sh 在 macOS 14/15 上经常因 osascript
# Privacy/Automation 权限或 hdiutil 兼容性挂掉,真错被 Tauri 吞;brew create-dmg
# 也常有版本差异。这里只用 macOS 自带的 hdiutil + 一条 ln -s,稳定可重复。
#
# 输出:UDZO 压缩 DMG,挂载后用户看到 .app + Applications 软链,拖过去即装。
# 不做 AppleScript 摆窗口位置 / 加背景图(那才是 bundle_dmg 卡住的主要源头),
# 想要花哨布局后续单独治。
#
# 用法:
#   bash scripts/make-mac-dmg.sh <input.app> <output.dmg> [volname]
#
# 例:
#   bash scripts/make-mac-dmg.sh \
#     chayuan-client/apps/desktop/src-tauri/target/release/bundle/macos/Chayuan.app \
#     dist-full/Chayuan-macos-arm64.dmg \
#     Chayuan
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
    echo "[make-mac-dmg] 只在 macOS 上运行(uname=$(uname -s))" >&2
    exit 1
fi

if [ $# -lt 2 ]; then
    echo "用法: $0 <input.app> <output.dmg> [volname]" >&2
    exit 64
fi

APP="$1"
DMG="$2"
VOLNAME="${3:-Chayuan}"

if [ ! -d "$APP" ] || [[ "$APP" != *.app ]]; then
    echo "[make-mac-dmg] 输入不是 .app 目录: $APP" >&2
    exit 1
fi

# 临时 staging:.app + Applications 软链
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/chayuan-dmg-stage.XXXXXX")"
cleanup() {
    rm -rf "$STAGE" 2>/dev/null || true
    # 如果挂过临时卷,卸掉(防 mount 残留卡死后续运行)
    if mount | grep -q "/Volumes/$VOLNAME"; then
        hdiutil detach "/Volumes/$VOLNAME" -force >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

# DMG 内的 .app 名做"去 flavor"规整 —— 用户拖到 /Applications 后应该看到
# 标准的 Chayuan.app,而不是 Chayuan.lite.app / Chayuan.full.app。flavor
# 后缀只用于分发文件名(DMG 自身),不该带进应用名:
#   Chayuan.lite.app  → DMG 内 Chayuan.app
#   Chayuan.full.app  → DMG 内 Chayuan.app
#   Chayuan.app       → DMG 内 Chayuan.app(no-op)
# 实现:basename 取第一个 `.` 前的部分(${var%%.*})作为应用名。我们的
# app 名是 Chayuan,中间不含点,这个规则够用;若以后改成 "Chayuan AI.app"
# 这种带空格 / 多点的名字需要再加显式 --inner-name 参数。
APP_BASE="$(basename "$APP")"
INNER_STEM="${APP_BASE%%.*}"
INNER_NAME="${INNER_STEM}.app"

echo "[make-mac-dmg] staging: $STAGE"
echo "[make-mac-dmg] inner app name: $APP_BASE → $INNER_NAME"
cp -R "$APP" "$STAGE/$INNER_NAME"
ln -s /Applications "$STAGE/Applications"

# 先卸残留 + 删旧 DMG —— 上一次跑挂在中间会留卷
if mount | grep -q "/Volumes/$VOLNAME"; then
    echo "[make-mac-dmg] 卸载残留卷 /Volumes/$VOLNAME"
    hdiutil detach "/Volumes/$VOLNAME" -force >/dev/null 2>&1 || true
fi
rm -f "$DMG"
mkdir -p "$(dirname "$DMG")"

# 算 staging 大小 → DMG 容量(+ 50 MB buffer 防 hdiutil 紧凑算失败)
size_kb=$(du -sk "$STAGE" | awk '{print $1}')
size_mb=$(( (size_kb + 1023) / 1024 + 50 ))
echo "[make-mac-dmg] staging size ≈ ${size_mb} MB"

# 直接生成 UDZO 压缩 DMG。绕开 read-write → convert 两步走,简单 + 稳。
# -ov 强制覆盖,-format UDZO 是默认压缩格式(zlib),Finder 双击能直接挂。
echo "[make-mac-dmg] hdiutil create"
hdiutil create \
    -volname "$VOLNAME" \
    -srcfolder "$STAGE" \
    -ov \
    -format UDZO \
    "$DMG" >/dev/null

# 验证:能挂上、能 ls 到 .app、卸下来不卡
echo "[make-mac-dmg] 验证 mount / unmount"
MOUNT_POINT="$(hdiutil attach "$DMG" -nobrowse -noverify -noautoopen \
    | awk '/\/Volumes\//{print $NF; exit}')"
if [ -z "$MOUNT_POINT" ] || [ ! -d "$MOUNT_POINT" ]; then
    echo "[make-mac-dmg] DMG mount 失败" >&2
    exit 1
fi
if [ ! -d "$MOUNT_POINT/$INNER_NAME" ]; then
    echo "[make-mac-dmg] mount 后 $INNER_NAME 不在(挂载点 $MOUNT_POINT)" >&2
    ls "$MOUNT_POINT" >&2
    hdiutil detach "$MOUNT_POINT" -force >/dev/null 2>&1 || true
    exit 1
fi
hdiutil detach "$MOUNT_POINT" -force >/dev/null 2>&1 || true

ls -lh "$DMG"
echo "[make-mac-dmg] 完成 → $DMG"
