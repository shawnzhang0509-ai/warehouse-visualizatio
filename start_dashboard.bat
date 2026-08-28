@echo off
setlocal

cd /d "%~dp0"

echo Starting instock-not-displayed dashboard (dashboard.py) ...

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

rem Make sure Flask is available; install quietly if missing.
%PYEXE% -c "import flask" >nul 2>nul
if not "%errorlevel%"=="0" (
    echo Flask not found, installing...
    %PYEXE% -m pip install flask
)

echo Opening http://localhost:5000 ...
start "" "http://localhost:5000"
%PYEXE% dashboard.py

set "EXIT_CODE=%errorlevel%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo dashboard.py exited with code %EXIT_CODE%.
    echo If module errors appear, install dependencies manually:
    echo   py -3 -m pip install flask
)
pause
exit /b %EXIT_CODE%
