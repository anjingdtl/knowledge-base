# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
root = os.path.abspath('.')
qtawesome_datas = collect_data_files('qtawesome')

a = Analysis(
    ['main.py'],
    pathex=[root],
    binaries=[],
    datas=[
        ('config.yaml', '.'),
        ('src/gui/resources/style.qss', 'src/gui/resources'),
        ('src/gui/resources/style-dark.qss', 'src/gui/resources'),
    ] + qtawesome_datas,
    hiddenimports=[
        'src.version',
        'src.utils.config',
        'src.services.db',
        'src.services.file_parser',
        'src.services.text_splitter',
        'src.services.embedding',
        'src.services.vectorstore',
        'src.services.llm',
        'src.services.rag',
        'src.models.knowledge',
        'src.models.chat',
        'src.gui.main_window',
        'src.gui.knowledge_view',
        'src.gui.chat_view',
        'src.gui.import_dialog',
        'src.gui.settings_dialog',
        'src.gui.icons',
        'src.app',
        'src.plugins',
        'qtawesome',
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ShineHeKnowledge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
