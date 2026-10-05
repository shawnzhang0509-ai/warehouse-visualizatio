@echo off
setlocal

cd /d "%~dp0"

echo Starting 有货未展示看板 (panel_app.py) ...

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python 3 not found in PATH.
    echo Please install Python and try again.
    pause
    exit /b 1
)

rem 图片显示需要 Pillow；缺失时自动安装（仅首次）。
%PYEXE% -c "import PIL" >nul 2>nul
if not "%errorlevel%"=="0" (
    echo Installing Pillow for image thumbnails...
    %PYEXE% -m pip install pillow
)

%PYEXE% panel_app.py

set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo panel_app.py exited with code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
