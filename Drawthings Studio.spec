# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = [('static', 'static')]
binaries = []
hiddenimports = ['drawthings_py']
datas += copy_metadata('genai_prices')
datas += copy_metadata('pydantic_ai_slim')
datas += copy_metadata('pydantic_ai')
datas += copy_metadata('pydantic_graph')
datas += copy_metadata('pydantic_evals')
datas += copy_metadata('logfire_api')
tmp_ret = collect_all('pywebview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# OpenCV（抽帧 / 无 ffmpeg 时合成视频）：惰性导入，需显式声明让 PyInstaller 收集
hiddenimports += ['cv2', 'numpy']
# imageio-ffmpeg：自带静态 ffmpeg（合成含音轨视频、无需系统安装 ffmpeg）
if_ret = collect_all('imageio_ffmpeg')
datas += if_ret[0]; binaries += if_ret[1]; hiddenimports += if_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'logfire'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Drawthings Studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/Drawthings.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Drawthings Studio',
)
app = BUNDLE(
    coll,
    name='Drawthings Studio.app',
    icon='assets/Drawthings.icns',
    bundle_identifier='com.drawthings.studio',
)
