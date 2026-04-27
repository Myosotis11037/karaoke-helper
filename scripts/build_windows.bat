@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0\.."

set "PYTHON_BIN=python"
set "APP_NAME=Krok Helper"
set "DIST_PATH=dist\windows"
set "WORK_PATH=build\pyinstaller-windows"
set "SPEC_PATH=build\spec-windows"
set "APP_DIST=%DIST_PATH%\%APP_NAME%"

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

echo Checking PySide6...
%PYTHON_BIN% -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo PySide6 not found, installing...
    %PYTHON_BIN% -m pip install PySide6
    if errorlevel 1 (
        echo Failed to install PySide6.
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
    --add-data "%CD%\krok_helper\assets\logo;krok_helper\assets\logo" ^
    --exclude-module PySide6.Qt3DAnimation ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.Qt3DExtras ^
    --exclude-module PySide6.Qt3DInput ^
    --exclude-module PySide6.Qt3DLogic ^
    --exclude-module PySide6.Qt3DRender ^
    --exclude-module PySide6.QtCharts ^
    --exclude-module PySide6.QtDataVisualization ^
    --exclude-module PySide6.QtDesigner ^
    --exclude-module PySide6.QtGraphs ^
    --exclude-module PySide6.QtMultimedia ^
    --exclude-module PySide6.QtNetworkAuth ^
    --exclude-module PySide6.QtPdf ^
    --exclude-module PySide6.QtPdfWidgets ^
    --exclude-module PySide6.QtPositioning ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtQuick3D ^
    --exclude-module PySide6.QtQuickControls2 ^
    --exclude-module PySide6.QtQuickTest ^
    --exclude-module PySide6.QtQuickWidgets ^
    --exclude-module PySide6.QtRemoteObjects ^
    --exclude-module PySide6.QtScxml ^
    --exclude-module PySide6.QtSensors ^
    --exclude-module PySide6.QtSql ^
    --exclude-module PySide6.QtStateMachine ^
    --exclude-module PySide6.QtTest ^
    --exclude-module PySide6.QtTextToSpeech ^
    --exclude-module PySide6.QtWebChannel ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineQuick ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtWebSockets ^
    --exclude-module PySide6.QtWebView ^
    --exclude-module PySide6.QtXml ^
    --exclude-module PySide6.QtXmlPatterns ^
    app.py

if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

echo Trimming Windows package...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$py = Join-Path (Resolve-Path '%APP_DIST%') '_internal\PySide6';" ^
    "$translations = Join-Path $py 'translations';" ^
    "if (Test-Path $translations) { Get-ChildItem $translations -File | Where-Object { $_.Name -notin @('qtbase_zh_CN.qm','qtbase_zh_TW.qm','qtbase_ja.qm','qt_zh_CN.qm','qt_zh_TW.qm','qt_ja.qm') } | Remove-Item -Force };" ^
    "$plugins = Join-Path $py 'plugins';" ^
    "$removeFiles = @('platforms\qdirect2d.dll','platforms\qminimal.dll','platforms\qoffscreen.dll','imageformats\qwebp.dll','imageformats\qtiff.dll','imageformats\qicns.dll','imageformats\qgif.dll','imageformats\qpdf.dll','imageformats\qsvg.dll','imageformats\qtga.dll','imageformats\qwbmp.dll','iconengines\qsvgicon.dll','tls\qopensslbackend.dll','tls\qcertonlybackend.dll','generic\qtuiotouchplugin.dll','networkinformation\qnetworklistmanager.dll','platforminputcontexts\qtvirtualkeyboardplugin.dll');" ^
    "foreach ($rel in $removeFiles) { $path = Join-Path $plugins $rel; if (Test-Path $path) { Remove-Item -LiteralPath $path -Force } };" ^
    "$removeDirs = @('generic','iconengines','networkinformation','platforminputcontexts');" ^
    "foreach ($rel in $removeDirs) { $path = Join-Path $plugins $rel; if ((Test-Path $path -PathType Container) -and -not (Get-ChildItem $path -Force)) { Remove-Item -LiteralPath $path -Force } };" ^
    "$dlls = @('Qt6Pdf.dll','Qt6Svg.dll','Qt6VirtualKeyboard.dll');" ^
    "foreach ($name in $dlls) { $path = Join-Path $py $name; if (Test-Path $path) { Remove-Item -LiteralPath $path -Force } }"
if errorlevel 1 (
    echo.
    echo Package trimming failed.
    pause
    exit /b 1
)

echo.
echo Build complete:
echo %CD%\%APP_DIST%
pause
