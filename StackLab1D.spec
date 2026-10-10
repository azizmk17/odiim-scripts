# Windows one-folder build: python -m PyInstaller --noconfirm StackLab1D.spec
from pathlib import Path

project_root = Path(SPECPATH)
example_files = [(str(path), "examples") for path in (project_root / "examples").glob("*.stack1d")]

analysis = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=example_files,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "stackup"],
    noarchive=False,
)
# PyInstaller can pick up Conda's version-suffixed ICU DLL while scanning
# Qt6Core. The PySide6 wheel imports unversioned ICU symbols and uses the
# Windows system ICU; bundling Conda's DLL makes QtWidgets fail to import.
analysis.binaries = [
    item for item in analysis.binaries
    if Path(item[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}
]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="StackLab1D",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="StackLab1D",
)
