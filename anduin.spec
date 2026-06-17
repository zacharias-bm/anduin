# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Anduin.

Build:
  macOS:   pyinstaller anduin.spec
  Windows: pyinstaller anduin.spec

Output:
  dist/Anduin.app  (macOS)
  dist/Anduin/     (Windows)
"""
import os
import sys
from pathlib import Path
import sysconfig

block_cipher = None
project_root = Path(SPECPATH)
sitepackages = sysconfig.get_path("purelib")

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    [str(project_root / "anduin" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # Web assets (HTML/CSS/JS served by the built-in HTTP server)
        (str(project_root / "anduin" / "web"), "anduin/web"),
        # MLX native libraries (dylibs, Metal shaders)
        (os.path.join(sitepackages, "mlx", "lib"), "mlx/lib"),
        (os.path.join(sitepackages, "mlx", "core.cpython-312-darwin.so"), "mlx"),
        # mlx-whisper mel filter weights
        (os.path.join(sitepackages, "mlx_whisper", "assets"), "mlx_whisper/assets"),
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
        # ML — MLX native extensions need all submodules listed explicitly
        "mlx",
        "mlx._reprlib_fix",
        "mlx.core",
        "mlx.extension",
        "mlx.nn",
        "mlx.nn.init",
        "mlx.nn.layers",
        "mlx.nn.layers.activations",
        "mlx.nn.layers.base",
        "mlx.nn.layers.containers",
        "mlx.nn.layers.convolution",
        "mlx.nn.layers.convolution_transpose",
        "mlx.nn.layers.distributed",
        "mlx.nn.layers.dropout",
        "mlx.nn.layers.embedding",
        "mlx.nn.layers.linear",
        "mlx.nn.layers.normalization",
        "mlx.nn.layers.pooling",
        "mlx.nn.layers.positional_encoding",
        "mlx.nn.layers.quantized",
        "mlx.nn.layers.recurrent",
        "mlx.nn.layers.transformer",
        "mlx.nn.layers.upsample",
        "mlx.nn.losses",
        "mlx.nn.utils",
        "mlx.optimizers",
        "mlx.optimizers.optimizers",
        "mlx.optimizers.schedulers",
        "mlx.utils",
        "mlx_whisper",
        "mlx_whisper.audio",
        "mlx_whisper.decoding",
        "mlx_whisper.load_models",
        "mlx_whisper.timing",
        "mlx_whisper.tokenizer",
        "mlx_whisper.torch_whisper",
        "mlx_whisper.transcribe",
        "mlx_whisper.whisper",
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
            "CFBundleShortVersionString": "0.2.1",
            "CFBundleVersion": "0.2.1",
            "LSUIElement": True,  # Menu bar app — no Dock icon
            "NSMicrophoneUsageDescription": "Anduin needs microphone access to record meetings.",
            "NSScreenCaptureUsageDescription": "Anduin captures system audio for digital meeting recording.",
            "NSHighResolutionCapable": True,
        },
    )
