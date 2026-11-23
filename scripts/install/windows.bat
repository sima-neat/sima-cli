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
    set "IS_ADMIN=1"
    set "REG_KEY=HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    set "SETX_FLAG=/M"
) else (
    echo Standard user detected. Will modify User PATH.
    set "IS_ADMIN=0"
    set "REG_KEY=HKCU\Environment"
    set "SETX_FLAG="
)

rem The Microsoft Store alias, or any python that cannot run, counts as no Python.
set "PYTHON=python"
python -c "import sys" >nul 2>nul
if errorlevel 1 (
    set "PYTHON_URL=https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.exe"
    set "PYTHON_INSTALLER=%TEMP%\python-3.14.10-amd64.exe"
    if "%IS_ADMIN%"=="1" (
        set "PYTHON_ALL_USERS=1"
        set "PYTHON=%ProgramFiles%\Python314\python.exe"
    ) else (
        set "PYTHON_ALL_USERS=0"
        set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    )
    echo No working Python found. Installing 64-bit Python 3.14.10...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri $env:PYTHON_URL -OutFile $env:PYTHON_INSTALLER"
    if errorlevel 1 (
        echo Failed to download !PYTHON_URL!
        exit /b 1
    )
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = Get-AuthenticodeSignature -LiteralPath $env:PYTHON_INSTALLER; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.GetNameInfo('SimpleName', $false) -ne 'Python Software Foundation') { exit 1 }"
    if errorlevel 1 (
        echo The downloaded Python installer is not validly signed by the Python Software Foundation. It was not run.
        del "!PYTHON_INSTALLER!"
        exit /b 1
    )
    start "" /wait "!PYTHON_INSTALLER!" /quiet InstallAllUsers=!PYTHON_ALL_USERS! InstallLauncherAllUsers=!PYTHON_ALL_USERS! PrependPath=1 Include_test=0
    del "!PYTHON_INSTALLER!"
    "!PYTHON!" -c "import sys" >nul 2>nul
    if errorlevel 1 (
        echo Python installation failed: !PYTHON! does not run.
        exit /b 1
    )
)
"%PYTHON%" -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 14) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 echo Note: the Windows PCIe host package needs 64-bit Python 3.14 from https://www.python.org/downloads/windows/

set "INSTALL_DIR=%USERPROFILE%\.sima-cli-env"
if not exist "%INSTALL_DIR%" (
    echo Creating virtual environment...
    "!PYTHON!" -m venv "%INSTALL_DIR%"
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
