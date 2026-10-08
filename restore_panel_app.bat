@echo off
setlocal
cd /d "%~dp0"

echo 正在从当前 Git 提交恢复 panel_app.py ...
where git >nul 2>nul
if errorlevel 1 (
    echo 未找到 git，请安装 Git for Windows 后重试。
    pause
    exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 (
    echo 当前目录不是 Git 仓库：%CD%
    pause
    exit /b 1
)

git fetch origin 2>nul
git checkout HEAD -- panel_app.py
if errorlevel 1 (
    echo checkout 失败。
    pause
    exit /b 1
)

findstr /C:"<<<<<<<" panel_app.py >nul 2>nul
if not errorlevel 1 (
    echo 仍有冲突标记，尝试从 origin/cursor/onhold-parts-mining-e23a 覆盖...
    git fetch origin cursor/onhold-parts-mining-e23a 2>nul
    git checkout origin/cursor/onhold-parts-mining-e23a -- panel_app.py
)

findstr /C:"<<<<<<<" panel_app.py >nul 2>nul
if not errorlevel 1 (
    echo 仍检测到 <<<<<<< ，请关闭 Cursor/记事本中对 panel_app.py 的打开窗口后重试。
    pause
    exit /b 1
)

echo 已恢复。第 38 行附近应为 APP_VERSION = "1.9.58"
powershell -NoProfile -Command "Get-Content -Path 'panel_app.py' -TotalCount 42 | Select-Object -Last 6"
echo.
echo 请重新双击 start_panel.bat
pause
