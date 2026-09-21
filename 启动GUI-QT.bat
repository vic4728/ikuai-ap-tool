@echo off
chcp 65001 >nul
rem 爱快路由-AP终端工具 PySide6 版启动器
set PY=C://Users//VIC-AMD//.workbuddy//binaries//python//envs//ikuai_gui//Scripts//pythonw.exe
if not exist "%PY%" set PY=%~dp0.venv-gui\Scripts\pythonw.exe
start "" "%PY%" "%~dp0ikuai_gui_qt.py"
