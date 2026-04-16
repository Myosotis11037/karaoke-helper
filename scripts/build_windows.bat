@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0\.."

set "PYTHON_BIN=python"
set "APP_NAME=Krok Helper"
set "DIST_PATH=dist\windows"
set "WORK_PATH=build\pyinstaller-windows"
set "SPEC_PATH=build\spec-windows"

echo Checking Python...
where %PYTHON_BIN% >nul 2>&1
if errorlevel 1 (
    echo Python not found. Please install Python 3.10+ first.
    pause
    exit /b 1
)

echo Checking PyInstaller...
%PYTHON_BIN% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found, installing...
    %PYTHON_BIN% -m pip install pyinstaller
    if errorlevel 1 (
        echo Failed to install PyInstaller.
        pause
        exit /b 1
    )
)

echo Checking tkinterdnd2...
%PYTHON_BIN% -c "import tkinterdnd2" >nul 2>&1
if errorlevel 1 (
    echo tkinterdnd2 not found, installing...
    %PYTHON_BIN% -m pip install tkinterdnd2
    if errorlevel 1 (
        echo Failed to install tkinterdnd2.
        pause
        exit /b 1
    )
)

if not exist "%DIST_PATH%" mkdir "%DIST_PATH%"
if not exist "%WORK_PATH%" mkdir "%WORK_PATH%"
if not exist "%SPEC_PATH%" mkdir "%SPEC_PATH%"

echo Building Windows package...
%PYTHON_BIN% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --onedir ^
    --name "%APP_NAME%" ^
    --distpath "%DIST_PATH%" ^
    --workpath "%WORK_PATH%" ^
    --specpath "%SPEC_PATH%" ^
    --collect-all tkinterdnd2 ^
    app.py

if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete:
echo %CD%\%DIST_PATH%\%APP_NAME%
pause
