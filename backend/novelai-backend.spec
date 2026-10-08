# PyInstaller spec for the FastAPI sidecar — a standalone `novelai-backend` binary the packaged Electron
# app spawns instead of `python -m app`. Built per-OS in CI (PyInstaller does not cross-compile).
# Run from backend/: `pyinstaller novelai-backend.spec`.
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

# The T5 + Qwen tokenizer files must ship alongside the binary; tokenizer.py resolves them from
# sys._MEIPASS under this same relative path when frozen.
datas = [("app/novelai/assets/t5_spiece.model", "app/novelai/assets"),
         ("app/novelai/assets/qwen35_tokenizer.def", "app/novelai/assets")]
# The built frontend is served single-origin at /app/; main.py resolves it from sys._MEIPASS/frontend/dist
# when frozen, so bundle it here (CI runs `npm run build` before PyInstaller). The trailing dir must exist.
datas += [("../frontend/dist", "frontend/dist")]
# keyring finds its OS backend via importlib.metadata entry points — bundle the dist metadata too.
datas += copy_metadata("keyring")

# uvicorn imports its loop/protocol workers dynamically; keyring's OS backend is imported by name.
# collect_submodules is platform-aware (only what exists on the build runner), so the same spec works
# on both Windows and macOS runners.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("keyring.backends")
    + ["app", "sentencepiece", "regex"]
)

a = Analysis(
    ["pyi_entry.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],  # not used; drops a chunk of weight
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="novelai-backend",
    console=False,         # windowless: a GUI-subsystem binary so the packaged Electron app doesn't pop a
                           # console window when it spawns the sidecar. Logs still flow over inherited stdio
                           # handles (visible when the shell itself is launched from a terminal, e.g. dev).
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,      # the runner's arch (macOS matrix builds x64 + arm64 separately)
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="novelai-backend",  # -> dist/novelai-backend/  (onedir: exe + libs, bundled as an Electron resource)
)
