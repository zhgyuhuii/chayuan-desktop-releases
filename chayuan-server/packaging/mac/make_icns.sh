#!/bin/bash
# 把一张方形 PNG 转换为 macOS .icns。
# 用法：make_icns.sh <src.png> <dst.icns>
#
# 依赖 macOS 自带的 sips + iconutil，无需 brew。

set -euo pipefail

SRC="${1:?source png required}"
DST="${2:?destination icns required}"

if [ ! -f "${SRC}" ]; then
    echo "[make_icns] 源文件不存在：${SRC}" >&2
    exit 1
fi

TMP_DIR="$(mktemp -d -t chayuan-icns)"
ICONSET="${TMP_DIR}/AppIcon.iconset"
mkdir -p "${ICONSET}"

# Apple 建议的各 DPI / 点数组合
sizes=(
    "16 icon_16x16.png"
    "32 icon_16x16@2x.png"
    "32 icon_32x32.png"
    "64 icon_32x32@2x.png"
    "128 icon_128x128.png"
    "256 icon_128x128@2x.png"
    "256 icon_256x256.png"
    "512 icon_256x256@2x.png"
    "512 icon_512x512.png"
    "1024 icon_512x512@2x.png"
)

for entry in "${sizes[@]}"; do
    size="${entry%% *}"
    name="${entry#* }"
    /usr/bin/sips -z "${size}" "${size}" "${SRC}" --out "${ICONSET}/${name}" >/dev/null
done

/usr/bin/iconutil -c icns "${ICONSET}" -o "${DST}"

rm -rf "${TMP_DIR}"
echo "[make_icns] 写入：${DST}"
