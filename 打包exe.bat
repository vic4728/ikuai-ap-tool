@echo off
chcp 65001 >nul
rem ============================================================
rem  打包单文件 exe（爱快路由-AP终端工具 PySide6 版）
rem  产物: dist\爱快路由-AP终端工具.exe
rem ============================================================

set PY=C://Users//VIC-AMD//.workbuddy//binaries//python//envs//ikuai_gui//Scripts//python.exe
if not exist "%PY%" set PY=%~dp0..\\.venv-gui\Scripts\python.exe

echo [1/3] 检查 pyinstaller ...
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo       首次使用，安装 pyinstaller ...
    "%PY%" -m pip install pyinstaller
)

echo [2/3] 清理旧构建 ...
if exist "%~dp0..\build" rmdir /s /q "%~dp0..\build"
if exist "%~dp0..\dist" rmdir /s /q "%~dp0..\dist"

echo [3/3] 打包（单文件 / 无控制台 / 带图标 / PySide6）...
cd /d "%~dp0.."
"%PY%" -m PyInstaller --noconfirm --onefile --windowed ^
    --name "爱快路由-AP终端工具" ^
    --icon=ikuai-R-logo.ico ^
    --add-data "ikuai-R-logo.ico;." ^
    --exclude-module tkinter ^
    ikuai_gui_qt.py

if errorlevel 1 (
    echo.
    echo [失败] 打包出错，请检查上方输出。
    pause
    exit /b 1
)

echo.
echo [成功] 产物: dist\爱快路由-AP终端工具.exe
pause
