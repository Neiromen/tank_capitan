# Сборка: pyinstaller build.spec
# Требует: pip install pyinstaller
# Результат: dist/TankVoice.exe + папки model и kronos_models рядом с exe (при onefile — в _MEIPASS)

# -*- mode: python ; coding: utf-8 -*-
import os
import sys

block_cipher = None
base = os.path.abspath('.')

# Vosk требует папку пакета с DLL в bundle (libvosk.dll и др.)
try:
    import vosk
    vosk_dir = os.path.dirname(vosk.__file__)
except Exception:
    vosk_dir = os.path.join(base, '.venv', 'Lib', 'site-packages', 'vosk')
if not os.path.isdir(vosk_dir):
    vosk_dir = None

datas_list = [
    ('model', 'model'),
    ('kronos_models', 'kronos_models'),
]
if vosk_dir:
    datas_list.append((vosk_dir, 'vosk'))

a = Analysis(
    ['src.py'],
    pathex=[],
    binaries=[],
    datas=datas_list,
    hiddenimports=[
        'vosk', 'sounddevice', 'pyautogui', 'numpy',
        'mouse', 'win32api', 'win32con', 'dxcam', 'cv2',
        'ultralytics', 'winsound', 'json', 'queue', 'threading',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# target_arch: None = текущий Python. Собирайте на 64-битном Python для работы на 64-битной Windows.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TankVoice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX отключён: на других ПК exe может определяться как 16-бит и не запускаться
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
