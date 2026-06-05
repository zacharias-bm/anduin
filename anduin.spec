# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Anduin.

Build:
  macOS:   pyinstaller anduin.spec
  Windows: pyinstaller anduin.spec

Output:
  dist/Anduin.app  (macOS)
  dist/Anduin/     (Windows)
"""
import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH)

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    [str(project_root / "anduin" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # Web assets (HTML/CSS/JS served by the built-in HTTP server)
        (str(project_root / "anduin" / "web"), "anduin/web"),
    ],
    hiddenimports=[
        # PyObjC frameworks used at runtime
        "AppKit",
        "WebKit",
        "rumps",
        "keyring.backends.macOS",
        # Audio
        "sounddevice",
        "soundfile",
        "_sounddevice_data",
        # ML
        "faster_whisper",
        "ctranslate2",
        # Torch — PyInstaller often misses these
        "torch",
        "torch.nn",
        "torch.nn.functional",
        "torchaudio",
        # Hugging Face
        "huggingface_hub",
        # pyannote / lightning / torchmetrics chain
        "scipy",
        "scipy.signal",
        "scipy.spatial",
        "scipy.fft",
        "scipy.linalg",
        "lightning",
        "lightning.pytorch",
        "pytorch_lightning",
        "torchmetrics",
        "pyannote",
        "pyannote.audio",
        # ScreenCaptureKit
        "ScreenCaptureKit",
        "CoreMedia",
        # Misc
        "psutil",
        "requests",
        "imageio_ffmpeg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Reduce bundle size — exclude unused heavy modules
        "tkinter",
        "_tkinter",
        "matplotlib",
        "IPython",
        "notebook",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ── PYZ ───────────────────────────────────────────────────────────────────────

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ───────────────────────────────────────────────────────────────────────

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # --onedir mode
    name="Anduin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX breaks some native libs on macOS
    console=False,  # No terminal window
    icon=str(project_root / "anduin" / "web" / "logo.svg") if sys.platform != "darwin" else None,
)

# ── COLLECT ───────────────────────────────────────────────────────────────────

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Anduin",
)

# ── macOS .app bundle ─────────────────────────────────────────────────────────

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Anduin.app",
        icon=str(project_root / "resources" / "Anduin.icns"),
        bundle_identifier="com.backingminds.anduin",
        info_plist={
            "CFBundleShortVersionString": "0.1.3",
            "CFBundleVersion": "0.1.3",
            "LSUIElement": True,  # Menu bar app — no Dock icon
            "NSMicrophoneUsageDescription": "Anduin needs microphone access to record meetings.",
            "NSScreenCaptureUsageDescription": "Anduin captures system audio for digital meeting recording.",
            "NSHighResolutionCapable": True,
        },
    )
