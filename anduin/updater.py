from __future__ import annotations
"""DIY auto-updater for Anduin.

Checks GitHub Releases for a newer version, downloads the update
payload, verifies its checksum, stages it, and relaunches the app.

Update manifest (latest.json) hosted at the GitHub Release:
{
    "version": "0.2.0",
    "macos": {
        "url": "https://github.com/.../releases/download/v0.2.0/Anduin-0.2.0-macos.tar.gz",
        "sha256": "abc123..."
    },
    "windows": {
        "url": "https://github.com/.../releases/download/v0.2.0/Anduin-0.2.0-windows.zip",
        "sha256": "def456..."
    }
}
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import requests

from anduin import __version__

# ── Configuration ─────────────────────────────────────────────────────────────

MANIFEST_URL = os.environ.get(
    "ANDUIN_UPDATE_URL",
    "https://api.github.com/repos/zacharias-bm/anduin/releases/latest",
)

ProgressCallback = Callable[[int, int], None]  # (bytes_downloaded, total_bytes)


# ── Public API ────────────────────────────────────────────────────────────────

def current_version() -> str:
    return __version__


def check_for_update() -> dict | None:
    """Check if a newer version is available.

    Returns a dict with {version, url, sha256} if an update exists,
    or None if we're up to date (or on error).
    """
    try:
        manifest = _fetch_manifest()
        if manifest is None:
            raise ConnectionError("Could not reach update server")

        latest_version = manifest.get("version", "")
        if not latest_version or not _is_newer(latest_version, __version__):
            return None

        plat = "macos" if sys.platform == "darwin" else "windows"
        asset = manifest.get(plat)
        if not asset:
            print(f"[updater] no asset for platform {plat}", flush=True)
            return None

        url = asset["url"]
        if not url.startswith("http"):
            url = f"https://github.com/zacharias-bm/anduin/releases/download/v{latest_version}/{url}"

        return {
            "version": latest_version,
            "url": url,
            "sha256": asset.get("sha256", ""),
        }
    except Exception as e:
        print(f"[updater] check failed: {e}", flush=True)
        return None


def download_and_apply(
    update_info: dict,
    progress: ProgressCallback | None = None,
) -> bool:
    """Download update, verify checksum, stage it, and relaunch.

    Returns True if the update was staged successfully (app will relaunch).
    Returns False on any error.
    """
    try:
        url = update_info["url"]
        expected_sha = update_info.get("sha256", "")
        version = update_info["version"]

        print(f"[updater] downloading {version} from {url}", flush=True)

        # Download to temp file
        tmp_dir = Path(tempfile.mkdtemp(prefix="anduin_update_"))
        archive_name = url.split("/")[-1]
        archive_path = tmp_dir / archive_name

        _download_file(url, archive_path, progress)

        # Verify checksum
        if expected_sha:
            actual_sha = _sha256(archive_path)
            if actual_sha != expected_sha:
                print(f"[updater] checksum mismatch: expected {expected_sha}, got {actual_sha}", flush=True)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return False
            print("[updater] checksum verified", flush=True)

        # Extract
        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        _extract(archive_path, extract_dir)

        # Stage the update
        if sys.platform == "darwin":
            _stage_macos(extract_dir)
        else:
            _stage_windows(extract_dir)

        # Clean up temp
        shutil.rmtree(tmp_dir, ignore_errors=True)

        print(f"[updater] update to {version} staged, relaunching...", flush=True)
        _relaunch()
        return True

    except Exception as e:
        print(f"[updater] update failed: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return False


# ── Internal helpers ──────────────────────────────────────────────────────────

def _fetch_manifest() -> dict | None:
    """Fetch the latest release manifest from GitHub."""
    headers = {"Accept": "application/vnd.github.v3+json"}

    try:
        resp = requests.get(MANIFEST_URL, headers=headers, timeout=10)
        if resp.status_code == 404:
            print("[updater] no releases found", flush=True)
            return None
        resp.raise_for_status()
    except requests.ConnectionError:
        print("[updater] cannot reach GitHub", flush=True)
        return None

    release = resp.json()
    tag = release.get("tag_name", "")
    version = tag.lstrip("v")

    # Look for a latest.json asset in the release
    for asset in release.get("assets", []):
        if asset["name"] == "latest.json":
            r = requests.get(asset["browser_download_url"], timeout=10)
            r.raise_for_status()
            return r.json()

    # Fallback: construct manifest from release assets
    manifest = {"version": version}
    for asset in release.get("assets", []):
        name = asset["name"].lower()
        dl_url = asset["browser_download_url"]
        if "macos" in name and (name.endswith(".tar.gz") or name.endswith(".dmg")):
            manifest["macos"] = {"url": dl_url, "sha256": ""}
        elif "windows" in name and (name.endswith(".zip") or name.endswith(".exe")):
            manifest["windows"] = {"url": dl_url, "sha256": ""}

    return manifest if ("macos" in manifest or "windows" in manifest) else None


def _is_newer(latest: str, current: str) -> bool:
    """Compare semver-style version strings."""
    def parse(v: str) -> tuple:
        parts = v.lstrip("v").split(".")
        return tuple(int(p) for p in parts if p.isdigit())
    try:
        return parse(latest) > parse(current)
    except (ValueError, TypeError):
        return False


def _download_file(url: str, dest: Path, progress: ProgressCallback | None):
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
            if progress:
                progress(downloaded, total)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(128 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _extract(archive: Path, dest: Path):
    name = archive.name.lower()
    dest = dest.resolve()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                target = (dest / member.name).resolve()
                if not str(target).startswith(str(dest)):
                    raise ValueError(f"Path traversal detected: {member.name}")
            tf.extractall(dest)
    elif name.endswith(".zip"):
        with zipfile.ZipFile(archive, "r") as zf:
            for info in zf.infolist():
                target = (dest / info.filename).resolve()
                if not str(target).startswith(str(dest)):
                    raise ValueError(f"Path traversal detected: {info.filename}")
            zf.extractall(dest)
    else:
        raise ValueError(f"Unknown archive format: {archive.name}")


def _find_app_bundle() -> Path:
    """Find the root of the running app bundle.

    For a PyInstaller --onedir build:
      macOS: Anduin.app/Contents/MacOS/Anduin -> Anduin.app
      Windows: Anduin/Anduin.exe -> Anduin/
    For dev mode (running from source): returns the project root.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller frozen app
        exe = Path(sys.executable)
        if sys.platform == "darwin":
            # Walk up from Contents/MacOS/Anduin to Anduin.app
            for parent in exe.parents:
                if parent.suffix == ".app":
                    return parent
            return exe.parent
        else:
            return exe.parent
    else:
        # Dev mode — project root
        return Path(__file__).resolve().parent.parent


def _stage_macos(extract_dir: Path):
    """Replace the current .app bundle with the new one."""
    current_app = _find_app_bundle()

    # Find the .app in the extracted directory
    apps = list(extract_dir.rglob("*.app"))
    if not apps:
        raise FileNotFoundError("No .app bundle found in update archive")
    new_app = apps[0]

    if current_app.suffix == ".app":
        # Replace the bundle
        backup = current_app.with_suffix(".app.bak")
        if backup.exists():
            shutil.rmtree(backup)
        current_app.rename(backup)
        shutil.copytree(new_app, current_app, symlinks=True)
        shutil.rmtree(backup, ignore_errors=True)
        print(f"[updater] replaced {current_app}", flush=True)
    else:
        # Dev mode — just log
        print(f"[updater] dev mode: would replace {current_app} with {new_app}", flush=True)


def _stage_windows(extract_dir: Path):
    """Replace the current app directory with the new one."""
    current_dir = _find_app_bundle()

    # Find the directory containing the exe in the extracted archive
    exes = list(extract_dir.rglob("*.exe"))
    if not exes:
        raise FileNotFoundError("No .exe found in update archive")
    new_dir = exes[0].parent

    if getattr(sys, "frozen", False):
        # Write a batch script that waits for us to exit, then replaces files
        bat = current_dir / "_update.bat"
        bat.write_text(
            f'@echo off\n'
            f'timeout /t 2 /nobreak >nul\n'
            f'xcopy /E /Y /I "{new_dir}" "{current_dir}"\n'
            f'start "" "{current_dir / exes[0].name}"\n'
            f'del "%~f0"\n',
            encoding="utf-8",
        )
        subprocess.Popen(
            ["cmd", "/c", str(bat)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        )
    else:
        print(f"[updater] dev mode: would replace {current_dir} with {new_dir}", flush=True)


def _relaunch():
    """Relaunch the app after an update."""
    if sys.platform == "darwin":
        app_bundle = _find_app_bundle()
        if app_bundle.suffix == ".app":
            exe = app_bundle / "Contents" / "MacOS" / "Anduin"
            os.execv(str(exe), [str(exe)])
        else:
            # Dev mode
            os.execv(sys.executable, [sys.executable] + sys.argv)
    else:
        # Windows — the batch script handles relaunch
        sys.exit(0)
