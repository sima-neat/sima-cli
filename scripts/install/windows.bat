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
    echo Administrator privileges detected.
    set "IS_ADMIN=1"
) else (
    echo Standard user detected.
    set "IS_ADMIN=0"
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

rem The environment is per-user, so persist it to the user PATH only, even when elevated.
rem Edit the raw registry value so entries that reference variables stay unexpanded.
set "PS_PATH=$dir = $env:VENV_PATH.TrimEnd('\'); function Same($entry) { $entry.Trim().TrimEnd('\') -ieq $dir }"
set "PS_PATH=!PS_PATH!; $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment'); $raw = [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames')"
set "PS_PATH=!PS_PATH!; if ($raw.Split(';') | Where-Object { Same ([Environment]::ExpandEnvironmentVariables($_)) }) { Write-Host 'Already in the user PATH.' } else { $kind = 'ExpandString'; if ($null -ne $key.GetValue('Path')) { $kind = $key.GetValueKind('Path') }; Write-Host ('Persisting ' + $env:VENV_PATH + ' to the user PATH...'); $key.SetValue('Path', ($raw.TrimEnd(';') + ';' + $env:VENV_PATH).TrimStart(';'), $kind) }"
rem Earlier installers added this exact per-user path to the system PATH when elevated; remove only that.
set "PS_PATH=!PS_PATH!; if ($env:IS_ADMIN -eq '1') { $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('SYSTEM\CurrentControlSet\Control\Session Manager\Environment', $true); $raw = [string]$key.GetValue('Path', '', 'DoNotExpandEnvironmentNames'); $kept = @($raw.Split(';') | Where-Object { -not (Same $_) }); if ($kept.Count -lt $raw.Split(';').Count) { Write-Host ('Removing ' + $env:VENV_PATH + ' from the system PATH...'); $key.SetValue('Path', ($kept -join ';'), $key.GetValueKind('Path')) } }"
rem Setting an unused user variable to null only broadcasts the change, so new windows see the new PATH.
set "PS_PATH=!PS_PATH!; [Environment]::SetEnvironmentVariable('SIMA_CLI_PATH_REFRESH', $null, 'User')"
powershell -NoProfile -ExecutionPolicy Bypass -Command "!PS_PATH!"
if errorlevel 1 echo Could not update the user PATH. Add %VENV_PATH% to it manually.

echo.
echo Done. Open a new Command Prompt or PowerShell and type:
echo     sima-cli
echo to start using the tool.
endlocal
