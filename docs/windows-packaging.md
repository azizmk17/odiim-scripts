# Build StackLab 1D for Windows

Use a supported modern Python on Windows. Build in a clean virtual environment so
the executable contains only the application dependencies.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\scripts\build_windows.ps1
```

The build script installs `requirements.txt`, runs `StackLab1D.spec`, and creates
`dist\StackLab1D\StackLab1D.exe`. The five `.stack1d` reference projects are
bundled under `dist\StackLab1D\_internal\examples` by PyInstaller. Distribute
the **entire** `dist\StackLab1D` directory; the EXE is a one-folder build and
needs its neighboring runtime files.

For a different Python interpreter, pass `-Python` with a path relative to the
repository root:

```powershell
.\scripts\build_windows.ps1 -Python ".venv\Scripts\python.exe"
```

The program entry point is `main.py`. After building, launch the executable and
open `examples\b-floating-block.stack1d` to verify the sketch, analysis, save,
and report workflows. A headless engineering check can also use the source
entry point:

```powershell
.\.venv\Scripts\python.exe main.py examples\a-fixed-chain.stack1d --analyze --methods worst_case
```

The PyInstaller build should be performed on Windows for Windows delivery.
Builds on another operating system target that operating system. Neither
PyInstaller nor this configuration signs the executable; add organization code
signing to the distribution process when needed.

If a Conda CAD environment is active, use `run.bat` for source launches. That
launcher isolates Qt from the environment's DLL path. The PyInstaller spec also
excludes Conda's incompatible version-suffixed ICU runtime; PySide6 uses the
Windows system ICU DLL.
