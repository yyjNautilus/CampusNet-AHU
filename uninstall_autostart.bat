@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   校园网自动连接助手 - 卸载开机自启
echo ============================================
if exist "CampusNet.exe" (
    CampusNet.exe --uninstall
) else if exist "campus_net.py" (
    python campus_net.py --uninstall
) else (
    echo [错误] 未找到 CampusNet.exe 或 campus_net.py
    pause
    exit /b 1
)
pause
