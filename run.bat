@echo off
chcp 65001 >nul
title 爱快路由器自动化

REM ============================================================
REM 爱快路由器 AP 自动化 —— 命令行一键运行
REM 路径用 %~dp0 定位，项目挪动后仍然可用。
REM ============================================================

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "PYTHON=C:\Users\VIC-AMD\.workbuddy\binaries\python\envs\ikuai\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo [错误] 找不到命令行运行环境
    echo   %PYTHON%
    echo.
    pause
    exit /b 1
)

:MENU
echo.
echo ============================================
echo   爱快路由器自动化
echo ============================================
echo.
echo   [1] 重启 AP  - 演练（不会真重启，安全）
echo   [2] 重启 AP  - 正式执行（会断开无线网）
echo   [3] 重启 AP  - 演练并显示浏览器窗口
echo   [4] SSH 环境探测（只读）
echo   [5] 退出
echo.
set /p choice=请输入选项数字:

if "%choice%"=="1" goto DRILL
if "%choice%"=="2" goto REAL
if "%choice%"=="3" goto SHOW
if "%choice%"=="4" goto PROBE
if "%choice%"=="5" goto END
echo 无效选项，请重新选择
goto MENU

:DRILL
echo.
echo [演练模式] 只走到确认弹窗，不会真正重启...
echo.
"%PYTHON%" "%SCRIPT_DIR%\restart_ap.py"
goto DONE

:REAL
echo.
echo ============================================
echo   警告：即将真正重启 AP
echo   无线网络会中断约 1-2 分钟
echo ============================================
echo.
set /p confirm=确认执行吗？输入 YES 继续:
if /i not "%confirm%"=="YES" (
    echo 已取消
    goto DONE
)
echo.
"%PYTHON%" "%SCRIPT_DIR%\restart_ap.py" --confirm
goto DONE

:SHOW
echo.
echo [演练模式 - 显示窗口] 可以看着浏览器操作...
echo.
"%PYTHON%" "%SCRIPT_DIR%\restart_ap.py" --show
goto DONE

:PROBE
echo.
echo [SSH探测] 只执行查询命令...
echo.
"%PYTHON%" "%SCRIPT_DIR%\probe_ssh.py"
goto DONE

:DONE
if %ERRORLEVEL% EQU 0 (
    echo.
    echo [完成] 执行成功
) else (
    echo.
    echo [注意] 错误码: %ERRORLEVEL%
    echo 详情请查看 output 目录下的日志文件
)
echo.
pause
goto MENU

:END
exit /b 0
