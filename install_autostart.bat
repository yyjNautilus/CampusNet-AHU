@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================================
echo   校园网自动连接助手 - 安装开机自启
echo ============================================
if exist "CampusNet.exe" (
    CampusNet.exe --install
) else if exist "campus_net.py" (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [错误] 未找到 Python, 请安装 Python 3 或使用 CampusNet.exe
        pause
        exit /b 1
    )
    python campus_net.py --install
) else (
    echo [错误] 未找到 CampusNet.exe 或 campus_net.py
    pause
    exit /b 1
)
echo.
echo 提示: 如需修改配置, 运行 CampusNet.exe --setup 或直接编辑 config.ini
pause
