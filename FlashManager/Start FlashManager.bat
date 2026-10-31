@echo off
setlocal
cd /d "%~dp0"
title FlashManager
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 "%~dp0flashmanager.py"
  goto done
)
where python >nul 2>&1
if %errorlevel%==0 (
  python "%~dp0flashmanager.py"
  goto done
)
echo.
echo FlashManager needs Python 3.
echo Install it from https://www.python.org/downloads/
echo On the installer, check "Add python.exe to PATH".
echo.
pause
exit /b 1
:done
if not %errorlevel%==0 pause
