@echo off
setlocal

cd /d "%~dp0"

echo Starting app.py ...
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 app.py
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python app.py
    ) else (
        echo Python 3 not found in PATH.
        echo Please install Python and try again.
        pause
        exit /b 1
    )
)

set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo app.py exited with code %EXIT_CODE%.
    echo If module errors appear, install dependencies manually:
    echo   py -3 -m pip install pyodbc pillow
)
pause
exit /b %EXIT_CODE%
