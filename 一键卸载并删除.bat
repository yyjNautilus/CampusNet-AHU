@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
echo ============================================
echo   校园网自动连接助手 - 一键卸载并删除
echo ============================================
echo 将执行以下操作:
echo   1. 删除开机自启(任务计划 + 开机启动文件夹, 全部清除)
echo   2. 删除本程序所在文件夹(含程序/配置/日志, 全部移除)
echo.
set /p ans=确认删除请输入 YES 后回车(输入其他任意内容取消):
if /i not "%ans%"=="YES" (
    echo 已取消, 未做任何修改。
    pause
    exit /b 0
)
echo.
echo 正在删除开机自启...
if exist "%~dp0CampusNet.exe" (
    "%~dp0CampusNet.exe" --uninstall
) else (
    schtasks /Delete /TN "CampusNetAutoLogin" /F >nul 2>&1
)
rem 双保险: 直接删除开机启动文件夹里的两种可能文件
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\校园网自动连接.lnk" >nul 2>&1
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\校园网自动连接.vbs" >nul 2>&1
for %%i in ("%~dp0.") do set "TARGET=%%~fi"
echo 正在删除程序文件夹: %TARGET%
echo (删除完成后本窗口自动关闭, 若闪属正常现象)
start "" /min cmd /c "timeout /t 2 /nobreak >nul & rd /s /q \"%TARGET%\""
exit
