@echo off
setlocal
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
    echo Python 3 not found. Install Python 3 and retry.
    pause
    exit /b 1
)

%PYEXE% fix_panel_app.py
if not "%errorlevel%"=="0" (
    echo fix_panel_app.py failed.
    pause
    exit /b 1
)

%PYEXE% -c "import PIL" >nul 2>nul
if not "%errorlevel%"=="0" (
    echo Installing Pillow...
    %PYEXE% -m pip install pillow
)

echo Starting panel_app.py ...
%PYEXE% panel_app.py
set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo panel_app.py exited with code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
