@echo off
REM Build Anduin for Windows using PyInstaller, then package as .zip
REM
REM Usage:  scripts\build_windows.bat
REM Output: dist\Anduin\, dist\Anduin-VERSION-windows.zip
setlocal

cd /d "%~dp0\.."

for /f "tokens=*" %%v in ('python -c "from anduin import __version__; print(__version__)"') do set VERSION=%%v
echo Building Anduin v%VERSION% for Windows...

REM ── 1. Clean ───────────────────────────────────────────────────────────────
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM ── 2. PyInstaller ─────────────────────────────────────────────────────────
echo Running PyInstaller...
pyinstaller anduin.spec --noconfirm

if not exist "dist\Anduin\Anduin.exe" (
    echo ERROR: dist\Anduin\Anduin.exe not found
    exit /b 1
)
echo Built dist\Anduin\

REM ── 3. Create .zip for distribution and updates ────────────────────────────
echo Creating archive...
cd dist
powershell Compress-Archive -Path Anduin -DestinationPath "Anduin-%VERSION%-windows.zip" -Force
cd ..
echo Created dist\Anduin-%VERSION%-windows.zip

REM ── 4. Checksums ───────────────────────────────────────────────────────────
echo Checksums:
for /f "tokens=*" %%h in ('certutil -hashfile "dist\Anduin-%VERSION%-windows.zip" SHA256 ^| findstr /v "hash"') do (
    echo   Anduin-%VERSION%-windows.zip: %%h
    set WIN_SHA=%%h
)

echo Done! Upload dist\Anduin-%VERSION%-windows.zip to a GitHub Release.
