# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ["src/lada_agent/gui.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("src/lada_agent/templates", "lada_agent/templates"),
        ("src/lada_agent/static", "lada_agent/static"),
    ],
    hiddenimports=[
        "lada_agent",
        "lada_agent.lada_monitor",
        "lada_agent.lada_gui",
        "lada_agent.config_manager",
        "lada_agent.web_app",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LadaAgent",
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
    icon="NONE",
)
