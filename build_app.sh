#!/bin/bash
# 构建 Drawthings Studio.app（PyInstaller，仅 macOS）。
# 用法：./build_app.sh    （前置：source .venv/bin/activate 装过依赖）
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
APP_NAME="Drawthings Studio"

echo "==> 1/3 生成图标（icon_1024.png → .icns）"
"$PY" tools/make_icon.py
ICONSET="build/icon.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
sips -z 16 16     assets/icon_1024.png --out "$ICONSET/icon_16x16.png"      >/dev/null
sips -z 32 32     assets/icon_1024.png --out "$ICONSET/icon_16x16@2x.png"  >/dev/null
sips -z 32 32     assets/icon_1024.png --out "$ICONSET/icon_32x32.png"     >/dev/null
sips -z 64 64     assets/icon_1024.png --out "$ICONSET/icon_32x32@2x.png"  >/dev/null
sips -z 128 128   assets/icon_1024.png --out "$ICONSET/icon_128x128.png"   >/dev/null
sips -z 256 256   assets/icon_1024.png --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256   assets/icon_1024.png --out "$ICONSET/icon_256x256.png"   >/dev/null
sips -z 512 512   assets/icon_1024.png --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512   assets/icon_1024.png --out "$ICONSET/icon_512x512.png"   >/dev/null
sips -z 1024 1024 assets/icon_1024.png --out "$ICONSET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET" -o assets/Drawthings.icns

echo "==> 2/3 PyInstaller 打包（依据 \"$APP_NAME.spec\"，spec 为构建唯一依据，勿单独加参数）"
# 清空旧产物：Finder / Spotlight 可能并发重建 .DS_Store 导致一次性 rm 失败，重试几次
for _ in 1 2 3 4 5; do
  rm -rf dist build && break
  sleep 1
done
"$PY" -m PyInstaller --noconfirm "$APP_NAME.spec"

echo "==> 3/3 完成"
echo "✅ dist/$APP_NAME.app（双击运行，或拖到 应用程序 文件夹）"
echo "   数据目录：~/Library/Application Support/Drawthings Studio/data"
