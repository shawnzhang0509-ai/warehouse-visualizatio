@echo off
setlocal

cd /d "%~dp0"

echo Starting 有货未展示看板 (panel_app.py) ...
echo 默认分支: git pull origin main  （本仓库没有 master）
echo [提示] 需要 Output-NZ\stock.csv 与 display.csv（AU 用 Output-AU）。
echo [提示] clone 后若缺文件：从旧目录拷贝，或先运行 start_app.bat / python app.py 导出 SQL。

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
