@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 仓库容积率 Web

python --version >nul 2>&1
if errorlevel 1 (
  echo 未找到 Python
  pause
  exit /b 1
)

python -m pip install flask pyodbc -q 2>nul
echo 启动 http://127.0.0.1:5001/volume
start "" "http://127.0.0.1:5001/volume"
python warehouse_volume_web.py
pause
