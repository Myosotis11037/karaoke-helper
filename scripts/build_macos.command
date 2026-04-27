#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_NAME="卡拉OK工具箱"
DIST_PATH="$PROJECT_ROOT/dist/macos"
WORK_PATH="$PROJECT_ROOT/build/pyinstaller-macos"
SPEC_PATH="$PROJECT_ROOT/build/spec-macos"
APP_DIST="$DIST_PATH/$APP_NAME.app"

EXCLUDED_MODULES=(
  PySide6.Qt3DAnimation
  PySide6.Qt3DCore
  PySide6.Qt3DExtras
  PySide6.Qt3DInput
  PySide6.Qt3DLogic
  PySide6.Qt3DRender
  PySide6.QtCharts
  PySide6.QtDataVisualization
  PySide6.QtDesigner
  PySide6.QtGraphs
  PySide6.QtMultimedia
  PySide6.QtNetworkAuth
  PySide6.QtPdf
  PySide6.QtPdfWidgets
  PySide6.QtPositioning
  PySide6.QtQml
  PySide6.QtQuick
  PySide6.QtQuick3D
  PySide6.QtQuickControls2
  PySide6.QtQuickTest
  PySide6.QtQuickWidgets
  PySide6.QtRemoteObjects
  PySide6.QtScxml
  PySide6.QtSensors
  PySide6.QtSql
  PySide6.QtStateMachine
  PySide6.QtTest
  PySide6.QtTextToSpeech
  PySide6.QtWebChannel
  PySide6.QtWebEngineCore
  PySide6.QtWebEngineQuick
  PySide6.QtWebEngineWidgets
  PySide6.QtWebSockets
  PySide6.QtWebView
  PySide6.QtXml
  PySide6.QtXmlPatterns
)

KEEP_TRANSLATIONS=(
  qtbase_zh_CN.qm
  qtbase_zh_TW.qm
  qtbase_ja.qm
  qt_zh_CN.qm
  qt_zh_TW.qm
  qt_ja.qm
)

REMOVE_PLUGIN_FILES=(
  "platforms/libqminimal.dylib"
  "platforms/libqoffscreen.dylib"
  "imageformats/libqgif.dylib"
  "imageformats/libqicns.dylib"
  "imageformats/libqpdf.dylib"
  "imageformats/libqsvg.dylib"
  "imageformats/libqtga.dylib"
  "imageformats/libqtiff.dylib"
  "imageformats/libqwbmp.dylib"
  "imageformats/libqwebp.dylib"
  "iconengines/libqsvgicon.dylib"
  "tls/libqcertonlybackend.dylib"
  "tls/libqopensslbackend.dylib"
  "generic/libqtuiotouchplugin.dylib"
  "networkinformation/libqnetworklistmanager.dylib"
  "platforminputcontexts/libqtvirtualkeyboardplugin.dylib"
)

REMOVE_PLUGIN_DIRS=(
  generic
  iconengines
  networkinformation
  platforminputcontexts
)

REMOVE_QT_LIBS=(
  "QtPdf.framework"
  "QtSvg.framework"
  "QtVirtualKeyboard.framework"
)

echo "Checking Python..."
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 not found. Please install Python 3.10+ first."
  if [ -z "${CI:-}" ]; then
    read -r -p "Press Enter to close..."
  fi
  exit 1
fi

echo "Checking PyInstaller..."
if ! "$PYTHON_BIN" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller not found, installing..."
  if ! "$PYTHON_BIN" -m pip install pyinstaller; then
    echo "Failed to install PyInstaller."
    if [ -z "${CI:-}" ]; then
      read -r -p "Press Enter to close..."
    fi
    exit 1
  fi
fi

echo "Checking PySide6..."
if ! "$PYTHON_BIN" -c "import PySide6" >/dev/null 2>&1; then
  echo "PySide6 not found, installing..."
  if ! "$PYTHON_BIN" -m pip install PySide6; then
    echo "Failed to install PySide6."
    if [ -z "${CI:-}" ]; then
      read -r -p "Press Enter to close..."
    fi
    exit 1
  fi
fi

mkdir -p "$DIST_PATH" "$WORK_PATH" "$SPEC_PATH"

PYINSTALLER_ARGS=(
  --noconfirm
  --clean
  --windowed
  --onedir
  --name "$APP_NAME"
  --distpath "$DIST_PATH"
  --workpath "$WORK_PATH"
  --specpath "$SPEC_PATH"
  --add-data "$PROJECT_ROOT/krok_helper/assets/logo/logo.jpg:krok_helper/assets/logo"
)

for module in "${EXCLUDED_MODULES[@]}"; do
  PYINSTALLER_ARGS+=(--exclude-module "$module")
done

echo "Building macOS package..."
if ! "$PYTHON_BIN" -m PyInstaller "${PYINSTALLER_ARGS[@]}" app.py; then
  echo
  echo "Build failed."
  if [ -z "${CI:-}" ]; then
    read -r -p "Press Enter to close..."
  fi
  exit 1
fi

echo "Trimming macOS package..."
PYSIDE_DIR="$(find "$APP_DIST" -type d -name PySide6 | head -n 1 || true)"
if [ -z "$PYSIDE_DIR" ] || [ ! -d "$PYSIDE_DIR" ]; then
  echo
  echo "Package trimming failed: PySide6 directory not found."
  if [ -z "${CI:-}" ]; then
    read -r -p "Press Enter to close..."
  fi
  exit 1
fi

TRANSLATIONS_DIR="$PYSIDE_DIR/translations"
if [ -d "$TRANSLATIONS_DIR" ]; then
  while IFS= read -r -d '' file; do
    keep_file=0
    for keep in "${KEEP_TRANSLATIONS[@]}"; do
      if [ "$(basename "$file")" = "$keep" ]; then
        keep_file=1
        break
      fi
    done
    if [ "$keep_file" -eq 0 ]; then
      rm -f "$file"
    fi
  done < <(find "$TRANSLATIONS_DIR" -type f -print0)
fi

PLUGINS_DIR="$PYSIDE_DIR/plugins"
if [ -d "$PLUGINS_DIR" ]; then
  for rel in "${REMOVE_PLUGIN_FILES[@]}"; do
    target="$PLUGINS_DIR/$rel"
    if [ -e "$target" ]; then
      rm -f "$target"
    fi
  done

  for rel in "${REMOVE_PLUGIN_DIRS[@]}"; do
    target="$PLUGINS_DIR/$rel"
    if [ -d "$target" ] && [ -z "$(find "$target" -mindepth 1 -print -quit)" ]; then
      rmdir "$target"
    fi
  done
fi

for rel in "${REMOVE_QT_LIBS[@]}"; do
  while IFS= read -r -d '' target; do
    rm -rf "$target"
  done < <(find "$APP_DIST" -name "$rel" -print0)
done

echo
echo "Build complete:"
echo "$APP_DIST"
if [ -z "${CI:-}" ]; then
  read -r -p "Press Enter to close..."
fi
