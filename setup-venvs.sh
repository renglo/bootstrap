#!/usr/bin/env bash
# Setup virtual environments for launcher and extensions-service.
#
# Each repo manages its own venv and requirements.txt independently.
# This script creates/updates both from the workspace root.
#
# Usage (run from infra-installer/ workspace root):
#   bash platform-installer/setup-venvs.sh
#   bash platform-installer/setup-venvs.sh --python python3.12
#   bash platform-installer/setup-venvs.sh --launcher-only
#   bash platform-installer/setup-venvs.sh --extensions-only
#
# After running this script, platform-installer/install.py will automatically
# detect the correct venv for each repo.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LAUNCHER_DIR="$WORKSPACE_ROOT/launcher"
EXTENSIONS_DIR="$WORKSPACE_ROOT/extensions-service"

PYTHON="${PYTHON:-python3}"
SETUP_LAUNCHER=true
SETUP_EXTENSIONS=true

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)         PYTHON="$2"; shift 2 ;;
    --python=*)       PYTHON="${1#*=}"; shift ;;
    --launcher-only)  SETUP_EXTENSIONS=false; shift ;;
    --extensions-only) SETUP_LAUNCHER=false; shift ;;
    -h|--help)
      echo "Usage: bash platform-installer/setup-venvs.sh [--python python3.12] [--launcher-only] [--extensions-only]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_python_version() {
  "$1" --version 2>&1 || echo "(not found)"
}

_ensure_venv() {
  local label="$1"
  local venv_dir="$2"
  local requirements="$3"

  echo ""
  echo "──────────────────────────────────────────────────────────"
  echo "  $label"
  echo "  venv    : $venv_dir"
  echo "  reqs    : $requirements"
  echo "──────────────────────────────────────────────────────────"

  if [[ ! -f "$requirements" ]]; then
    echo "  ! requirements.txt not found — skipping"
    return 0
  fi

  if [[ ! -d "$venv_dir" ]]; then
    echo "  Creating venv with $PYTHON $(_python_version "$PYTHON")..."
    "$PYTHON" -m venv "$venv_dir"
    echo "  + venv created"
  else
    echo "  + venv already exists — upgrading pip only"
    "$venv_dir/bin/python" -m pip install --quiet --upgrade pip
  fi

  echo "  Installing $requirements ..."
  "$venv_dir/bin/pip" install --quiet --upgrade -r "$requirements"
  echo "  + done"
  echo "  Python : $($venv_dir/bin/python --version)"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo ""
echo "Platform venv setup"
echo "Workspace : $WORKSPACE_ROOT"
echo "Python    : $PYTHON $(_python_version "$PYTHON")"

if $SETUP_LAUNCHER; then
  _ensure_venv \
    "launcher" \
    "$LAUNCHER_DIR/launch-venv" \
    "$LAUNCHER_DIR/requirements.txt"
fi

if $SETUP_EXTENSIONS; then
  _ensure_venv \
    "extensions-service" \
    "$EXTENSIONS_DIR/venv" \
    "$EXTENSIONS_DIR/requirements.txt"
fi

echo ""
echo "Setup complete."
echo ""
echo "Venv locations:"
if $SETUP_LAUNCHER;   then echo "  launcher           : $LAUNCHER_DIR/launch-venv"; fi
if $SETUP_EXTENSIONS; then echo "  extensions-service : $EXTENSIONS_DIR/venv"; fi
echo ""
echo "You can now run:"
echo "  python platform-installer/install.py <extension> --admin-profile <profile> --github-repo Org/repo"
