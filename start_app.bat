@echo off
setlocal

cd /d "%~dp0"

echo ==========================================
echo  Warehouse Visualization / Dashboard Start
echo ==========================================
echo.

echo [1/3] Detecting Python runtime...
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=python"
    ) else (
        echo ERROR: Python was not found in PATH.
        echo Please install Python 3 and retry.
        pause
        exit /b 1
    )
)

echo [2/3] Installing required dependency (Flask)...
%PY_CMD% -m pip install flask
if not %errorlevel%==0 (
    echo ERROR: Failed to install Flask.
    pause
    exit /b 1
)

echo [3/3] Starting app.py...
echo Open http://127.0.0.1:5000 in your browser.
echo Press Ctrl+C to stop the server.
echo.
%PY_CMD% app.py

set "EXIT_CODE=%errorlevel%"
echo.
echo app.py exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
