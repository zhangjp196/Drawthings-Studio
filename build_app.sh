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

echo "==> 2/3 PyInstaller 打包"
rm -rf dist build "$APP_NAME.spec"
"$PY" -m PyInstaller --noconfirm \
  --name "$APP_NAME" \
  --onedir \
  --windowed \
  --icon assets/Drawthings.icns \
  --osx-bundle-identifier "com.drawthings.studio" \
  --add-data "static:static" \
  --collect-all pywebview \
  --hidden-import drawthings_py \
  --exclude-module tkinter \
  --exclude-module logfire \
  --copy-metadata genai_prices \
  --copy-metadata pydantic_ai_slim \
  --copy-metadata pydantic_ai \
  --copy-metadata pydantic_graph \
  --copy-metadata pydantic_evals \
  --copy-metadata logfire_api \
  app.py

echo "==> 3/3 完成"
echo "✅ dist/$APP_NAME.app（双击运行，或拖到 应用程序 文件夹）"
echo "   数据目录：~/Library/Application Support/Drawthings Studio/data"
