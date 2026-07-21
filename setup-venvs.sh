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

_is_cross_platform_venv() {
  local venv_dir="$1"
  local cfg="$venv_dir/pyvenv.cfg"
  [[ -f "$cfg" ]] || return 1
  # Windows venv copied to macOS/Linux (Scripts/ + C:\ paths in pyvenv.cfg)
  if grep -qE '(^home = C:\\|^executable = C:\\)' "$cfg" 2>/dev/null; then
    case "$(uname -s)" in
      MINGW*|MSYS*|CYGWIN*) return 1 ;;
      *) return 0 ;;
    esac
  fi
  return 1
}

_recreate_venv() {
  local venv_dir="$1"
  echo "  Removing unusable venv: $venv_dir"
  rm -rf "$venv_dir"
  echo "  Creating venv with $PYTHON $(_python_version "$PYTHON")..."
  "$PYTHON" -m venv "$venv_dir"
  echo "  + venv created"
}

_verify_aws_cdk() {
  local venv_python="$1"
  if "$venv_python" -c "import aws_cdk" 2>/dev/null; then
    echo "  + aws_cdk import OK"
    return 0
  fi
  echo "  ! aws_cdk missing — installing CDK Python libs..."
  "$venv_python" -m pip install --quiet --upgrade -r "$LAUNCHER_DIR/cdk/requirements.txt"
  if "$venv_python" -c "import aws_cdk" 2>/dev/null; then
    echo "  + aws_cdk import OK"
    return 0
  fi
  echo "  ! aws_cdk still missing after installing launcher/cdk/requirements.txt" >&2
  return 1
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

  if [[ -d "$venv_dir" ]] && _is_cross_platform_venv "$venv_dir"; then
    echo "  ! venv was created on Windows — recreating for this OS"
    _recreate_venv "$venv_dir"
  elif [[ ! -d "$venv_dir" ]]; then
    _recreate_venv "$venv_dir"
  else
    local existing_python
    existing_python="$(_venv_python "$venv_dir")"
    if [[ -z "$existing_python" ]]; then
      echo "  ! venv exists but python not found — recreating"
      _recreate_venv "$venv_dir"
    else
      echo "  + venv already exists — upgrading pip only"
    fi
  fi

  local venv_python
  venv_python="$(_venv_python "$venv_dir")"
  if [[ -z "$venv_python" ]]; then
    echo "  ! venv python not found — recreate with:" >&2
    echo "      rm -rf $venv_dir && $PYTHON -m venv $venv_dir" >&2
    return 1
  fi

  "$venv_python" -m pip install --quiet --upgrade pip
  echo "  Installing $requirements ..."
  "$venv_python" -m pip install --quiet --upgrade -r "$requirements"
  if [[ "$label" == "bootstrap" ]]; then
    _verify_aws_cdk "$venv_python"
  fi
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
echo "  python3.12 bootstrap/install.py synth"
echo "  # or: bootstrap/venv/bin/python bootstrap/install.py synth"
echo "  python bootstrap/install.py write-state --env-name <env> --aws-profile <profile>"
