@echo off
setlocal enabledelayedexpansion
title Sima-CLI Installer

set "WHEEL_PATH=%~1"
if "%WHEEL_PATH%"=="" (
    set "INSTALL_FROM_PYPI=1"
) else (
    set "INSTALL_FROM_PYPI=0"
)
if "%INSTALL_FROM_PYPI%"=="0" if not exist "%WHEEL_PATH%" (
    echo Wheel not found: %WHEEL_PATH%
    echo Usage: %~nx0 [path\to\sima_cli.whl]
    exit /b 2
)

net session >nul 2>&1
if %errorlevel% equ 0 (
    echo Administrator privileges detected. Will modify System PATH.
    set "REG_KEY=HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    set "SETX_FLAG=/M"
) else (
    echo Standard user detected. Will modify User PATH.
    set "REG_KEY=HKCU\Environment"
    set "SETX_FLAG="
)

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python not found. Installing Python...
    set "PYTHON_INSTALLER=%TEMP%\python-installer.exe"
    set "PYTHON_URL=https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_INSTALLER%'"
    "%PYTHON_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    timeout /t 15 >nul
    del "%PYTHON_INSTALLER%"
)

set "INSTALL_DIR=%USERPROFILE%\.sima-cli-env"
if not exist "%INSTALL_DIR%" (
    echo Creating virtual environment...
    python -m venv "%INSTALL_DIR%"
)

if "%INSTALL_FROM_PYPI%"=="1" (
    echo Installing/Upgrading official sima-cli release from PyPI...
) else (
    echo Installing/Upgrading sima-cli from %WHEEL_PATH%...
)
call "%INSTALL_DIR%\Scripts\activate.bat"
python -m pip install --upgrade pip
if "%INSTALL_FROM_PYPI%"=="1" (
    python -m pip install --force-reinstall --index-url https://pypi.org/simple sima-cli
) else (
    python -m pip install --force-reinstall "%WHEEL_PATH%"
)

set "VENV_PATH=%INSTALL_DIR%\Scripts"
echo %PATH% | findstr /I /C:"%VENV_PATH%" >nul
if errorlevel 1 (
    echo Adding %VENV_PATH% to current session PATH...
    set "PATH=%PATH%;%VENV_PATH%"
)

set "TARGET_PATH="
for /f "tokens=2*" %%A in ('reg query "!REG_KEY!" /v PATH 2^>nul') do set "TARGET_PATH=%%B"

echo !TARGET_PATH! | findstr /I /C:"%VENV_PATH%" >nul
if errorlevel 1 (
    echo Persisting %VENV_PATH% to PATH...
    if "!TARGET_PATH!"=="" (
        setx PATH "%VENV_PATH%" !SETX_FLAG! >nul
    ) else (
        setx PATH "!TARGET_PATH!;%VENV_PATH%" !SETX_FLAG! >nul
    )
) else (
    echo Already in PATH.
)

echo.
echo Done! Open a new Command Prompt or PowerShell and type:
echo     sima-cli
echo to start using the tool.
endlocal
