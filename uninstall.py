"""Platform uninstaller orchestrator.

Tears down both extensions-service ECS infra and launcher backend/core infra
by delegating to each repo's own teardown command (same flags unified here).

Reads bootstrap/state/<extension>/platform_resources.json to derive
the AWS region used during provisioning.

Usage:
    python bootstrap/uninstall.py <extension> \\
        --profile acd-arbitium-tt-dev \\
        [--yes] \\
        [--skip-extensions] \\
        [--skip-launcher] \\
        [--skip-tables] \\
        [--skip-cognito] \\
        [--keep-logs]

Teardown order:
  1. extensions-service: python run.py <ext> provision-infra teardown --profile ... --yes [--keep-logs]
  2. launcher:           python scripts/teardown_environment.py <ext> --aws-profile ... --yes [--skip-*] [--keep-logs]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_PLATFORM_INSTALLER_ROOT = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _PLATFORM_INSTALLER_ROOT.parent

sys.path.insert(0, str(_PLATFORM_INSTALLER_ROOT))

# ---------------------------------------------------------------------------
# Venv resolution — mirrors install.py logic
# ---------------------------------------------------------------------------


def _resolve_python(repo_label: str, venv_dir: Path) -> str:
    candidates = [
        venv_dir / "bin" / "python",
        venv_dir / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    print(f"\nERROR: venv not found for {repo_label}.")
    print(f"  Expected: {venv_dir}")
    print(f"\n  Run: bash bootstrap/setup-venvs.sh")
    sys.exit(1)


def _launcher_python() -> str:
    return _resolve_python("launcher", _WORKSPACE_ROOT / "launcher" / "launch-venv")


def _extensions_python() -> str:
    venv_dir = _WORKSPACE_ROOT / "extensions-service" / "venv"
    candidates = [
        venv_dir / "bin" / "python",
        venv_dir / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _run_subprocess(cmd: list[str], cwd: Path, description: str) -> int:
    """Run a subprocess command. Returns the exit code (does not exit on failure)."""
    import subprocess

    print(f"\n{'=' * 60}")
    print(f"  {description}")
    print(f"  {' '.join(cmd)}")
    print(f"{'=' * 60}")
    result = subprocess.run(cmd, cwd=str(cwd))
    return result.returncode


def _read_platform_resources(extension: str) -> dict:
    path = _PLATFORM_INSTALLER_ROOT / "state" / extension / "platform_resources.json"
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _resolve_region(platform_resources: dict, fallback: str) -> str:
    return str(platform_resources.get("aws_region") or fallback)


@dataclass(frozen=True)
class UnifiedTeardownOptions:
    """Flags passed through to underlying teardown CLIs (single source in uninstall)."""

    keep_logs: bool = False
    skip_tables: bool = False
    skip_cognito: bool = False


def _run_extensions_teardown(extension: str, profile: str, opts: UnifiedTeardownOptions) -> int:
    ext_service = _WORKSPACE_ROOT / "extensions-service"
    cmd: list[str] = [
        _extensions_python(),
        "run.py",
        extension,
        "provision-infra",
        "teardown",
        "--profile",
        profile,
        "--yes",
    ]
    if opts.keep_logs:
        cmd.append("--keep-logs")
    return _run_subprocess(
        cmd,
        cwd=ext_service,
        description=f"Extensions-service: teardown '{extension}'",
    )


def _run_launcher_teardown(
    extension: str,
    profile: str,
    aws_region: str,
    opts: UnifiedTeardownOptions,
) -> int:
    launcher_scripts = _WORKSPACE_ROOT / "launcher" / "scripts"
    cmd: list[str] = [
        _launcher_python(),
        "teardown_environment.py",
        extension,
        "--aws-profile",
        profile,
        "--aws-region",
        aws_region,
        "--yes",
    ]
    if opts.skip_tables:
        cmd.append("--skip-tables")
    if opts.skip_cognito:
        cmd.append("--skip-cognito")
    if opts.keep_logs:
        cmd.append("--keep-logs")
    return _run_subprocess(
        cmd,
        cwd=launcher_scripts,
        description=f"Launcher: teardown environment '{extension}'",
    )


def _cleanup_platform_state(extension: str) -> None:
    """Remove the bootstrap/platform state dir for the extension."""
    import shutil

    state_dir = _PLATFORM_INSTALLER_ROOT / "state" / extension
    if state_dir.exists():
        try:
            shutil.rmtree(state_dir)
            print(f"\n  + Removed platform state: {state_dir}")
        except Exception as exc:
            print(f"\n  ! Could not remove platform state: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Platform uninstaller: tears down extensions-service + launcher infra",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "extension",
        help="Extension / environment name to tear down (e.g. arbitiumrs)",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="AWS named profile (passed as --profile to extensions-service, --aws-profile to launcher)",
    )
    parser.add_argument(
        "--aws-region",
        default="us-east-1",
        help="AWS region (fallback if not found in platform_resources.json)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm teardown without interactive prompt",
    )
    parser.add_argument(
        "--skip-extensions",
        action="store_true",
        help="Skip extensions-service teardown",
    )
    parser.add_argument(
        "--skip-launcher",
        action="store_true",
        help="Skip launcher teardown",
    )
    parser.add_argument(
        "--skip-tables",
        action="store_true",
        help="Preserve DynamoDB tables (launcher teardown only)",
    )
    parser.add_argument(
        "--skip-cognito",
        action="store_true",
        help="Preserve Cognito user pool (launcher teardown only)",
    )
    parser.add_argument(
        "--keep-logs",
        action="store_true",
        help="Preserve CloudWatch log groups in both extensions-service and launcher teardowns",
    )
    args = parser.parse_args()

    platform_resources = _read_platform_resources(args.extension)
    aws_region = _resolve_region(platform_resources, args.aws_region)

    opts = UnifiedTeardownOptions(
        keep_logs=args.keep_logs,
        skip_tables=args.skip_tables,
        skip_cognito=args.skip_cognito,
    )

    print(f"\nPlatform uninstaller — extension: {args.extension}")
    print(f"  AWS profile : {args.profile}")
    print(f"  AWS region  : {aws_region}")
    if args.skip_tables:
        print("  DynamoDB tables : preserved (--skip-tables, launcher)")
    if args.skip_cognito:
        print("  Cognito pool    : preserved (--skip-cognito, launcher)")
    if args.keep_logs:
        print("  CloudWatch logs : preserved (--keep-logs, both teardowns)")

    if not args.yes:
        print(f"\nThis will DELETE all provisioned AWS resources for '{args.extension}'.")
        confirm = input("\nType the extension name to confirm: ").strip()
        if confirm != args.extension:
            print("Aborted.")
            return

    errors: list[str] = []

    # Step 1: extensions-service teardown
    if not args.skip_extensions:
        rc = _run_extensions_teardown(
            extension=args.extension,
            profile=args.profile,
            opts=opts,
        )
        if rc != 0:
            errors.append(f"extensions-service teardown exited with code {rc}")
            print(f"\nWarning: extensions-service teardown failed (code {rc}). Continuing...")
    else:
        print("\n[extensions-service teardown] SKIPPED (--skip-extensions)")

    # Step 2: launcher teardown
    if not args.skip_launcher:
        rc = _run_launcher_teardown(
            extension=args.extension,
            profile=args.profile,
            aws_region=aws_region,
            opts=opts,
        )
        if rc != 0:
            errors.append(f"launcher teardown exited with code {rc}")
            print(f"\nWarning: launcher teardown failed (code {rc}).")
    else:
        print("\n[launcher teardown] SKIPPED (--skip-launcher)")

    # Step 3: clean up platform state
    if not errors:
        _cleanup_platform_state(args.extension)

    if errors:
        print(f"\nTeardown completed with errors for '{args.extension}':")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"\nTeardown complete for extension: {args.extension}")


if __name__ == "__main__":
    main()
