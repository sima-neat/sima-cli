@echo off
setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION

:: ------------------------------------------------------------
:: Check for Administrator privileges
:: ------------------------------------------------------------
net session >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ============================================================
    echo  ERROR: ADMINISTRATOR PRIVILEGES REQUIRED
    echo ============================================================
    echo.
    echo This script must be run from an elevated Command Prompt.
    echo.
    echo Please:
    echo   1. Right-click this .bat file
    echo   2. Select "Run as administrator"
    echo.
    echo No changes have been made to your system.
    echo.
    pause
    exit /B 1
)

:: ------------------------------------------------------------
:: Modalix / MLSoC PCIe Driver Installer (simaai_mla_drv, Test Signing Mode)
:: ------------------------------------------------------------

echo.
echo ============================================================
echo  WARNING: UNSIGNED DRIVER - TESTING PURPOSE ONLY
echo ============================================================
echo.
echo This PCIe driver is NOT digitally signed.
echo It is intended for DEVELOPMENT AND TESTING ONLY.
echo.
echo To install this driver, Windows must be placed into
echo **TEST SIGNING MODE**.
echo.
echo Enabling test signing mode may reduce system security.
echo DO NOT proceed on production systems.
echo.

choice /C YN /N /M "Do you want to continue and enable Test Signing Mode? [Y/N]: "
if errorlevel 2 goto USER_ABORT
if errorlevel 1 goto CONTINUE_INSTALL

:USER_ABORT
echo.
echo Installation aborted by user.
echo No changes were made to the system.
echo.
exit /B 0

:CONTINUE_INSTALL
echo.
echo Enabling Windows Test Signing Mode...
echo.

bcdedit /set testsigning on
if errorlevel 1 (
    echo.
    echo ERROR: Failed to enable test signing mode.
    echo.
    pause
    exit /B 1
)

echo.
echo Test Signing Mode enabled successfully.
echo.

:: ------------------------------------------------------------
:: Run driver installer
:: ------------------------------------------------------------
set DRIVER_EXE=pcie_master_10082025_2.00.081025.1_B11.exe

if not exist "%DRIVER_EXE%" (
    echo.
    echo ERROR: Driver installer not found:
    echo %DRIVER_EXE%
    echo.
    pause
    exit /B 1
)

echo Starting driver installer:
echo %DRIVER_EXE%
echo.

"%DRIVER_EXE%"
set INSTALL_EXIT_CODE=%ERRORLEVEL%

echo.
echo Driver installer exited with code: %INSTALL_EXIT_CODE%
echo.

:: ------------------------------------------------------------
:: Reboot prompt
:: ------------------------------------------------------------
echo ============================================================
echo A SYSTEM REBOOT IS REQUIRED
echo ============================================================
echo.
echo Test Signing Mode will not take effect until reboot.
echo The driver may not function correctly until the system restarts.
echo.

choice /C YN /N /M "Reboot the system now? [Y/N]: "
if errorlevel 2 goto NO_REBOOT
if errorlevel 1 goto DO_REBOOT

:NO_REBOOT
echo.
echo Reboot skipped.
echo Please remember to reboot the system manually later.
echo.
exit /B 0

:DO_REBOOT
echo.
echo Rebooting system now...
echo.
shutdown /r /t 5
exit /B 0
