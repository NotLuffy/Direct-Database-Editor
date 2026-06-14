@echo off
echo ============================================
echo   CNC Direct Editor — Installer
echo ============================================
echo.

set "INSTALL_DIR=%LOCALAPPDATA%\CNC Direct Editor"
set "EXE_NAME=CNC_Direct_Editor.exe"
set "SHORTCUT_NAME=CNC Direct Editor"

:: Create install directory
echo Installing to: %INSTALL_DIR%
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

:: Copy exe
copy /Y "%~dp0%EXE_NAME%" "%INSTALL_DIR%\%EXE_NAME%"
if errorlevel 1 (
    echo ERROR: Failed to copy executable. Is the program running?
    echo Close CNC Direct Editor and try again.
    pause
    exit /b 1
)

:: Create Desktop shortcut
echo Creating desktop shortcut...
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $sc = $ws.CreateShortcut([IO.Path]::Combine($ws.SpecialFolders('Desktop'), '%SHORTCUT_NAME%.lnk')); ^
   $sc.TargetPath = '%INSTALL_DIR%\%EXE_NAME%'; ^
   $sc.WorkingDirectory = '%INSTALL_DIR%'; ^
   $sc.Description = 'CNC Direct Editor'; ^
   $sc.Save()"

:: Create Start Menu shortcut
echo Creating Start Menu shortcut...
set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $sc = $ws.CreateShortcut([IO.Path]::Combine('%START_MENU%', '%SHORTCUT_NAME%.lnk')); ^
   $sc.TargetPath = '%INSTALL_DIR%\%EXE_NAME%'; ^
   $sc.WorkingDirectory = '%INSTALL_DIR%'; ^
   $sc.Description = 'CNC Direct Editor'; ^
   $sc.Save()"

echo.
echo ============================================
echo   INSTALLATION COMPLETE
echo.
echo   Location:  %INSTALL_DIR%
echo   Desktop shortcut created
echo   Start Menu shortcut created
echo.
echo   To uninstall, delete:
echo     - %INSTALL_DIR%
echo     - Desktop shortcut
echo     - Start Menu shortcut
echo ============================================
echo.
pause
