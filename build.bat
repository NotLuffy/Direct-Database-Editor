@echo off
echo ============================================
echo   CNC Direct Editor — Build Installer
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ from python.org
    pause
    exit /b 1
)

:: Install dependencies
echo [1/3] Installing dependencies...
pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

:: Kill running instance if any
taskkill /F /IM CNC_Direct_Editor.exe >nul 2>&1

:: Build exe
echo.
echo [2/3] Building executable...
pyinstaller --clean CNC_Direct_Editor.spec
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

:: Create installer package folder
echo.
echo [3/3] Packaging installer...
if exist "installer" rmdir /s /q "installer"
mkdir "installer"
copy "dist\CNC_Direct_Editor.exe" "installer\"
copy "install.bat" "installer\"

echo.
echo ============================================
echo   BUILD COMPLETE
echo   EXE: dist\CNC_Direct_Editor.exe
echo   Installer folder: installer\
echo ============================================
pause
