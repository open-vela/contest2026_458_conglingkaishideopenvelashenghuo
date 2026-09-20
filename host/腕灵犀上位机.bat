@echo off
rem 腕灵犀 WristClaw 上位机 —— 双击即可运行
rem 用同目录 .venv 里的 Python（系统 Python 建的环境，带 Tkinter / pyserial / vosk）
cd /d %~dp0

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    echo [!] 缺少 .venv，正在用系统 Python 重建运行环境...
    "C:\Users\yilives\AppData\Local\Programs\Python\Python312\python.exe" -m venv .venv
    .venv\Scripts\python.exe -m pip install --quiet pyserial vosk numpy sounddevice
    set PY=.venv\Scripts\python.exe
)

echo 启动腕灵犀上位机...
%PY% app.py
if errorlevel 1 pause
