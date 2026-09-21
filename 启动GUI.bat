@echo off
chcp 65001 >nul
title 爱快路由器管理工具

REM ============================================================
REM 爱快路由器管理工具 —— GUI 启动器
REM
REM 说明：GUI 需要 tkinter，而 WorkBuddy 自带的便携版 Python
REM       不含 tkinter，因此使用基于系统 Python 3.12 创建的
REM       专用虚拟环境。
REM
REM 路径用 %~dp0 定位，项目整体挪动后仍然可用。
REM ============================================================

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "PYTHON=C:\Users\VIC-AMD\.workbuddy\binaries\python\envs\ikuai_gui\Scripts\pythonw.exe"
set "PYTHON_CONSOLE=C:\Users\VIC-AMD\.workbuddy\binaries\python\envs\ikuai_gui\Scripts\python.exe"
set "SCRIPT=%SCRIPT_DIR%\ikuai_gui.py"

if not exist "%PYTHON%" (
    echo.
    echo [错误] 找不到 GUI 运行环境
    echo   %PYTHON%
    echo.
    echo 请先执行 scripts\setup_gui_env.bat 创建环境
    echo.
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo.
    echo [错误] 找不到主程序
    echo   %SCRIPT%
    echo.
    pause
    exit /b 1
)

REM 用 pythonw.exe 启动，不显示黑色控制台窗口。
REM 若启动失败想排查，把下面的 pythonw.exe 改成 python.exe 即可看到报错。
start "ikuai-gui" "%PYTHON%" "%SCRIPT%"
exit /b 0
