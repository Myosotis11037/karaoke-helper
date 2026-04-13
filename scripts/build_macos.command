#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_NAME="Krok Helper"
DIST_PATH="$PROJECT_ROOT/dist/macos"
WORK_PATH="$PROJECT_ROOT/build/pyinstaller-macos"
SPEC_PATH="$PROJECT_ROOT/build/spec-macos"

echo "Checking Python..."
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 not found. Please install Python 3.10+ first."
  read -r -p "Press Enter to close..."
  exit 1
fi

echo "Checking PyInstaller..."
if ! "$PYTHON_BIN" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller not found, installing..."
  "$PYTHON_BIN" -m pip install pyinstaller
fi

mkdir -p "$DIST_PATH" "$WORK_PATH" "$SPEC_PATH"

echo "Building macOS package..."
"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name "$APP_NAME" \
  --distpath "$DIST_PATH" \
  --workpath "$WORK_PATH" \
  --specpath "$SPEC_PATH" \
  app.py

echo
echo "Build complete:"
echo "$DIST_PATH/$APP_NAME.app"
read -r -p "Press Enter to close..."
