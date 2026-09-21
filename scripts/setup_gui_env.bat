@echo off
chcp 65001 >nul
title 创建 GUI 运行环境

REM ============================================================
REM 创建 GUI 专用 Python 环境
REM
REM 为什么要单独建：
REM   WorkBuddy 自带的便携版 Python 不含 tkinter（Tcl/Tk 库），
REM   而 GUI 界面依赖 tkinter，因此必须用系统安装的 Python 来建。
REM ============================================================

set "SYS_PY=C:\Program Files\Python312\python.exe"
set "ENV_DIR=C:\Users\VIC-AMD\.workbuddy\binaries\python\envs\ikuai_gui"

echo.
echo ============================================
echo   创建 GUI 运行环境
echo ============================================
echo.

if not exist "%SYS_PY%" (
    echo [错误] 找不到系统 Python：%SYS_PY%
    echo.
    echo 请先安装 Python 3.12（安装时勾选 tcl/tk 组件）
    echo 下载：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo [1/3] 检查 tkinter 支持...
"%SYS_PY%" -c "import tkinter; print('  tkinter 版本:', tkinter.TkVersion)"
if errorlevel 1 (
    echo.
    echo [错误] 系统 Python 不含 tkinter，请重新安装并勾选 tcl/tk 组件
    pause
    exit /b 1
)

echo.
echo [2/3] 创建虚拟环境...
if exist "%ENV_DIR%" (
    echo   环境已存在，跳过
) else (
    "%SYS_PY%" -m venv "%ENV_DIR%"
    if errorlevel 1 (
        echo   创建失败
        pause
        exit /b 1
    )
    echo   创建成功
)

echo.
echo [3/3] 安装依赖（playwright / pyyaml）...
"%ENV_DIR%\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%ENV_DIR%\Scripts\python.exe" -m pip install --quiet playwright pyyaml
if errorlevel 1 (
    echo   依赖安装失败
    pause
    exit /b 1
)
echo   依赖安装完成

echo.
echo 安装 Chromium 内核（约 150MB，若已装过会跳过）...
"%ENV_DIR%\Scripts\python.exe" -m playwright install chromium

echo.
echo ============================================
echo   完成！
echo   现在可以双击「启动GUI.bat」运行
echo ============================================
echo.
pause
exit /b 0
