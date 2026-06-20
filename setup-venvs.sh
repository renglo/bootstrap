#!/usr/bin/env bash
# Setup virtual environments for bootstrap, launcher, and extensions-service.
#
# Usage (run from infra-installer/ workspace root):
#   bash bootstrap/setup-venvs.sh                  # bootstrap only (CDK synth + write-state)
#   bash bootstrap/setup-venvs.sh --bootstrap-only
#   bash bootstrap/setup-venvs.sh --launcher-only  # legacy uninstall
#   bash bootstrap/setup-venvs.sh --extensions-only
#   bash bootstrap/setup-venvs.sh --all
#
# Default Python is 3.12 (python3.12). Idempotent: safe to re-run.
# bootstrap/install.py re-execs into bootstrap/venv automatically.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BOOTSTRAP_DIR="$WORKSPACE_ROOT/bootstrap"
LAUNCHER_DIR="$WORKSPACE_ROOT/launcher"
EXTENSIONS_DIR="$WORKSPACE_ROOT/extensions-service"

PYTHON="${PYTHON:-python3.12}"
SETUP_BOOTSTRAP=true
SETUP_LAUNCHER=false
SETUP_EXTENSIONS=false

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)         PYTHON="$2"; shift 2 ;;
    --python=*)       PYTHON="${1#*=}"; shift ;;
    --bootstrap-only) SETUP_LAUNCHER=false; SETUP_EXTENSIONS=false; shift ;;
    --launcher-only)  SETUP_BOOTSTRAP=false; SETUP_EXTENSIONS=false; shift ;;
    --extensions-only) SETUP_BOOTSTRAP=false; SETUP_LAUNCHER=false; shift ;;
    --all)            SETUP_BOOTSTRAP=true; SETUP_LAUNCHER=true; SETUP_EXTENSIONS=true; shift ;;
    -h|--help)
      echo "Usage: bash bootstrap/setup-venvs.sh [--python <exe>] [--bootstrap-only|--launcher-only|--extensions-only|--all]"
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

_venv_python() {
  local venv_dir="$1"
  if [[ -x "$venv_dir/bin/python" ]]; then
    echo "$venv_dir/bin/python"
  elif [[ -x "$venv_dir/Scripts/python.exe" ]]; then
    echo "$venv_dir/Scripts/python.exe"
  else
    echo ""
  fi
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
  fi

  local venv_python
  venv_python="$(_venv_python "$venv_dir")"
  if [[ -z "$venv_python" ]]; then
    echo "  ! venv python not found — skipping install"
    return 1
  fi

  "$venv_python" -m pip install --quiet --upgrade pip
  echo "  Installing $requirements ..."
  "$venv_python" -m pip install --quiet --upgrade -r "$requirements"
  echo "  + done"
  echo "  Python : $($venv_python --version)"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
echo ""
echo "Platform venv setup"
echo "Workspace : $WORKSPACE_ROOT"
echo "Python    : $PYTHON $(_python_version "$PYTHON")"

if $SETUP_BOOTSTRAP; then
  _ensure_venv \
    "bootstrap" \
    "$BOOTSTRAP_DIR/venv" \
    "$BOOTSTRAP_DIR/requirements.txt"
fi

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
if $SETUP_BOOTSTRAP; then echo "  bootstrap          : $BOOTSTRAP_DIR/venv"; fi
if $SETUP_LAUNCHER;   then echo "  launcher           : $LAUNCHER_DIR/launch-venv"; fi
if $SETUP_EXTENSIONS; then echo "  extensions-service : $EXTENSIONS_DIR/venv"; fi
echo ""
echo "You can now run:"
echo "  python bootstrap/install.py synth"
echo "  python bootstrap/install.py write-state --env-name <env> --aws-profile <profile>"
