@echo off
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo Git not found. Run fix_panel_app.py only.
    goto :fixpy
)

git fetch origin 2>nul
git checkout HEAD -- panel_app.py 2>nul
git fetch origin cursor/onhold-parts-mining-e23a 2>nul
git checkout origin/cursor/onhold-parts-mining-e23a -- panel_app.py 2>nul

:fixpy
where py >nul 2>nul && set "PY=py -3" || set "PY=python"
%PY% fix_panel_app.py
%PY% -c "import pathlib; t=pathlib.Path('panel_app.py').read_text(encoding='utf-8'); assert '<<<<<<<' not in t; print('panel_app.py OK')"
if errorlevel 1 (
    echo Still broken. Close Cursor/Notepad if panel_app.py is open, then run this again.
    pause
    exit /b 1
)
echo Done. Double-click start_panel.bat
pause
