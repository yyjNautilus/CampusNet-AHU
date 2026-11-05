@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  校园网自动连接助手 (CampusNet) 一键构建
REM    1) PyInstaller 打包目录版 exe -> dist_onedir\CampusNet\CampusNet.exe
REM    2) NSIS 编译安装包              -> release\..._安装包.exe
REM  依赖: 带 tkinter 的 Python + pyinstaller
REM        NSIS 便携版固定在 D:\my own special applications\PythonTools\nsis-3.10
REM ============================================================

cd /d "%~dp0"

set "NSIS_EXE=D:\my own special applications\PythonTools\nsis-3.10\makensis.exe"

echo [1/3] 检查 PyInstaller ...
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo   未检测到 pyinstaller, 正在安装 ...
    python -m pip install --upgrade pyinstaller || goto :fail
)

echo [2/3] 打包 exe ...
python -m PyInstaller --noconfirm --onedir --noconsole ^
    --icon "%CD%\app_icon.ico" ^
    --version-file "%CD%\version_info.txt" ^
    --name CampusNet --distpath "%CD%\dist_onedir" --workpath "%CD%\build" ^
    --specpath "%CD%\build" "%CD%\campus_net.py" || goto :fail
echo   -> dist_onedir\CampusNet\CampusNet.exe 完成

echo [3/3] 编译安装包 ...
if not exist "%NSIS_EXE%" (
    echo   跳过: 未找到 %NSIS_EXE%
    echo   便携版 NSIS 应放在 D:\my own special applications\PythonTools\nsis-3.10
    goto :done
)
if not exist "release" mkdir release
"%NSIS_EXE%" /INPUTCHARSET UTF8 installer.nsi || goto :fail
echo   -> release\ 下的安装包完成

:done
echo.
echo 构建完成。
pause
exit /b 0

:fail
echo.
echo 构建失败, 请看上面的错误信息。
pause
exit /b 1
