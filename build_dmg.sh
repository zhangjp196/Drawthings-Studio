#!/bin/bash
# 构建可发布的 DMG（仅 macOS）：先打包 .app，再制作压缩磁盘映像。
# DMG 内含「Drawthings Studio.app」与「应用程序」替身 —— 挂载后拖入即安装。
#
# 用法：
#   ./build_dmg.sh                        # 打包 .app + 生成 dist/Drawthings Studio.dmg
#   SKIP_BUILD=1 ./build_dmg.sh           # 复用现有 dist/*.app，仅重新制作 DMG
#   DMG_LAYOUT=1 ./build_dmg.sh           # 额外用 Finder 摆好图标位置（首次需授予「自动化」权限）
#
# 发布到其他 Mac（可选，强烈建议）：
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" ./build_dmg.sh
#   NOTARY_PROFILE=myprofile ./build_dmg.sh   # 需先 xcrun notarytool store-credentials myprofile
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="Drawthings Studio"
APP="dist/${APP_NAME}.app"
DMG="dist/${APP_NAME}.dmg"
STAGE="build/dmg"
RW_DMG="build/rw.dmg"
MOUNT_POINT="/Volumes/${APP_NAME}"

# ---- 1/5 打包 .app ----
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "==> 1/5 打包 .app（build_app.sh）"
  ./build_app.sh
else
  echo "==> 1/5 跳过打包（SKIP_BUILD=1）"
fi
[[ -d "$APP" ]] || { echo "❌ 未找到 ${APP}，请先运行 ./build_app.sh"; exit 1; }

# ---- 2/5 可选：Developer ID 签名（分发到其他机器必做，否则 Gatekeeper 拦截）----
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
  echo "==> 2/5 代码签名：${CODESIGN_IDENTITY}"
  codesign --deep --force --options runtime --timestamp \
    --sign "$CODESIGN_IDENTITY" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
else
  echo "==> 2/5 跳过签名（未设 CODESIGN_IDENTITY）"
fi

# ---- 3/5 准备 DMG 内容（.app + /Applications 替身）----
echo "==> 3/5 准备内容"
rm -rf "$STAGE" "$RW_DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
xattr -cr "$STAGE" 2>/dev/null || true  # 去掉扩展属性，避免带进 DMG

# ---- 4/5 生成 DMG ----
if [[ "${DMG_LAYOUT:-0}" == "1" ]]; then
  # 4a. 可写临时 DMG → Finder 摆位 → 压缩转换
  echo "==> 4/5 生成 DMG（含 Finder 布局）"
  hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDRW -fs HFS+ "$RW_DMG" >/dev/null
  [[ -d "$MOUNT_POINT" ]] && hdiutil detach "$MOUNT_POINT" -force >/dev/null 2>&1 || true
  hdiutil attach "$RW_DMG" -readwrite -noverify -noautoopen >/dev/null
  osascript <<APPLESCRIPT || echo "   ⚠️ Finder 布局失败（可能未授予自动化权限），已跳过"
tell application "Finder"
  tell disk "$APP_NAME"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 120, 820, 520}
    set opts to the icon view options of container window
    set arrangement of opts to not arranged
    set icon size of opts to 120
    set position of item "${APP_NAME}.app" of container window to {160, 190}
    set position of item "Applications" of container window to {470, 190}
    close
    open
    update without registering applications
    delay 1
  end tell
end tell
APPLESCRIPT
  sync
  hdiutil detach "$MOUNT_POINT" -force >/dev/null 2>&1 || true
  rm -f "$DMG"
  hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -o "$DMG" >/dev/null
  rm -f "$RW_DMG"
else
  echo "==> 4/5 生成 DMG（压缩）"
  rm -f "$DMG"
  hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" \
    -ov -format UDZO -imagekey zlib-level=9 "$DMG" >/dev/null
fi

# ---- 5/5 校验 + 可选公证 ----
echo "==> 5/5 校验 + 公证"
hdiutil verify "$DMG" >/dev/null && echo "   ✅ DMG 校验通过"

if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  echo "   公证（notarytool submit --wait）..."
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
fi

SIZE=$(du -h "$DMG" | cut -f1)
echo ""
echo "✅ ${DMG}（${SIZE}）"
echo "   挂载后把「${APP_NAME}」拖到「应用程序」即可安装。"
if [[ -z "${CODESIGN_IDENTITY:-}" ]]; then
  echo "   ⚠️ 未签名/未公证：其他 Mac 首次打开需「右键 → 打开」，"
  echo "      或执行：xattr -dr com.apple.quarantine \"/Applications/${APP_NAME}.app\""
fi
