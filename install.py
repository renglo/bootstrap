"""Platform installer orchestrator.

Runs launcher (deploy_environment.py) + extensions-service (provision-infra apply)
then merges both manifests into platform-installer/state/<extension>/.

Usage:
    python platform-installer/install.py <extension> \\
        --admin-profile acd-arbitium-tt-dev \\
        --aws-region us-east-1 \\
        --github-repo Org/launcher-repo \\
        [--handlers-github-repo Org/handlers-repo] \\
        [--handlers-enable-staging-role] \\
        --launch-type ec2

    # Skip one of the two provisioning steps (useful for re-runs):
    python platform-installer/install.py <extension> ... --skip-launcher
    python platform-installer/install.py <extension> ... --skip-extensions

    # Only merge existing manifests without reprovisioning:
    python platform-installer/install.py <extension> ... --merge-only

Prerequisites:
    Run once to create/update the required venvs for each repo:
        bash platform-installer/setup-venvs.sh
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PLATFORM_INSTALLER_ROOT = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _PLATFORM_INSTALLER_ROOT.parent

# Add platform-installer to sys.path so lib.merger is importable
sys.path.insert(0, str(_PLATFORM_INSTALLER_ROOT))

# ---------------------------------------------------------------------------
# Venv resolution — each repo has its own venv; fall back to sys.executable
# with a clear error message so the user knows what to fix.
# ---------------------------------------------------------------------------

def _resolve_python(repo_label: str, venv_dir: Path) -> str:
    """Return the Python executable for a repo venv, or abort with setup instructions."""
    candidates = [
        venv_dir / "bin" / "python",       # Linux / macOS / WSL
        venv_dir / "Scripts" / "python.exe",  # Windows native
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    print(f"\nERROR: venv not found for {repo_label}.")
    print(f"  Expected: {venv_dir}")
    print(f"\n  Run the following to create all required venvs:")
    print(f"    bash platform-installer/setup-venvs.sh")
    print(f"\n  Or set up {repo_label} standalone:")
    if repo_label == "launcher":
        print(f"    cd launcher && python3 -m venv launch-venv && launch-venv/bin/pip install -r requirements.txt")
    else:
        print(f"    cd extensions-service && python3 -m venv venv && venv/bin/pip install -r requirements.txt")
    sys.exit(1)


def _launcher_python() -> str:
    return _resolve_python("launcher", _WORKSPACE_ROOT / "launcher" / "launch-venv")


def _extensions_python() -> str:
    """extensions-service venv (boto3) for provision-infra OIDC; fall back to sys.executable."""
    venv_dir = _WORKSPACE_ROOT / "extensions-service" / "venv"
    candidates = [
        venv_dir / "bin" / "python",
        venv_dir / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    # Fallback: OIDC bootstrap needs boto3; install with handlers OIDC should use venv (see setup-venvs.sh).
    return sys.executable


def _run_subprocess(cmd: list[str], cwd: Path, description: str) -> None:
    """Run a subprocess command, printing progress. Exits on failure."""
    import subprocess

    print(f"\n{'=' * 60}")
    print(f"  {description}")
    print(f"  {' '.join(cmd)}")
    print(f"{'=' * 60}")
    result = subprocess.run(cmd, cwd=str(cwd))
    if result.returncode != 0:
        print(f"\nFailed: {description} (exit code {result.returncode})")
        sys.exit(result.returncode)


def _run_launcher(extension: str, admin_profile: str, aws_region: str, github_repo: str) -> None:
    launcher_scripts = _WORKSPACE_ROOT / "launcher" / "scripts"
    _run_subprocess(
        [
            _launcher_python(),
            "deploy_environment.py",
            extension,
            "--aws-profile", admin_profile,
            "--aws-region", aws_region,
            "--github-repo", github_repo,
        ],
        cwd=launcher_scripts,
        description=f"Launcher: deploy environment '{extension}'",
    )


def _run_extensions_service(
    extension: str,
    admin_profile: str,
    launch_type: str,
    handlers_github_repo: str,
    handlers_enable_staging_role: bool,
) -> None:
    ext_service = _WORKSPACE_ROOT / "extensions-service"
    cmd: list[str] = [
        _extensions_python(),
        "run.py",
        extension,
        "provision-infra",
        "apply",
        "--profile",
        admin_profile,
        "--launch-type",
        launch_type,
        "--github-repo",
        handlers_github_repo,
    ]
    if handlers_enable_staging_role:
        cmd.append("--enable-handlers-staging-role")
    _run_subprocess(
        cmd,
        cwd=ext_service,
        description=f"Extensions-service: provision infra '{extension}' (launch-type={launch_type})",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Platform installer: orchestrates launcher + extensions-service provisioning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "extension",
        help="Extension / environment name (e.g. arbitiumrs)",
    )
    parser.add_argument(
        "--admin-profile",
        required=True,
        help="AWS named profile with admin rights (e.g. acd-arbitium-tt-dev)",
    )
    parser.add_argument(
        "--aws-region",
        default="us-east-1",
        help="AWS region (default: us-east-1)",
    )
    parser.add_argument(
        "--github-repo",
        required=True,
        help="GitHub org/repo for launcher OIDC trust (e.g. Org/repo)",
    )
    parser.add_argument(
        "--handlers-github-repo",
        default=None,
        help="GitHub org/repo for handlers (extensions) OIDC; defaults to --github-repo when omitted",
    )
    parser.add_argument(
        "--handlers-enable-staging-role",
        action="store_true",
        help="Also create handlers OIDC IAM role for GitHub Environment 'staging'",
    )
    parser.add_argument(
        "--launch-type",
        default="ec2",
        choices=["ec2", "fargate"],
        help="ECS launch type for extensions-service (default: ec2)",
    )
    parser.add_argument(
        "--skip-launcher",
        action="store_true",
        help="Skip launcher deploy (run extensions-service only)",
    )
    parser.add_argument(
        "--skip-extensions",
        action="store_true",
        help="Skip extensions-service provision (run launcher only)",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Skip provisioning; only merge existing state manifests",
    )
    args = parser.parse_args()

    print(f"\nPlatform installer — extension: {args.extension}")
    print(f"  AWS profile : {args.admin_profile}")
    print(f"  AWS region  : {args.aws_region}")
    handlers_repo = args.handlers_github_repo or args.github_repo
    print(f"  GitHub repo (launcher) : {args.github_repo}")
    print(f"  GitHub repo (handlers) : {handlers_repo}")
    print(f"  Launch type : {args.launch_type}")

    if not args.merge_only:
        if not args.skip_launcher:
            _run_launcher(
                extension=args.extension,
                admin_profile=args.admin_profile,
                aws_region=args.aws_region,
                github_repo=args.github_repo,
            )
        else:
            print("\n[launcher] SKIPPED (--skip-launcher)")

        if not args.skip_extensions:
            _run_extensions_service(
                extension=args.extension,
                admin_profile=args.admin_profile,
                launch_type=args.launch_type,
                handlers_github_repo=handlers_repo,
                handlers_enable_staging_role=args.handlers_enable_staging_role,
            )
        else:
            print("\n[extensions-service] SKIPPED (--skip-extensions)")
    else:
        print("\n[provisioning] SKIPPED (--merge-only)")

    # Merge manifests into platform state
    from lib.merger import merge_manifests

    platform_state = merge_manifests(
        extension=args.extension,
        launcher_root=_WORKSPACE_ROOT / "launcher",
        extensions_service_root=_WORKSPACE_ROOT / "extensions-service",
        platform_installer_root=_PLATFORM_INSTALLER_ROOT,
        aws_region=args.aws_region,
    )

    print(f"\nInstallation complete for extension: {args.extension}")
    print(f"Platform state : {platform_state}")
    print("\nKey output files:")
    for fname in (
        "platform_resources.json",
        "platform_vars.production.json",
        "platform_vars.staging.json",
        "platform_vars.json",
        "handlers_vars.production.json",
        "handlers_vars.staging.json",
        "handlers_vars.json",
        "env_config.py",
    ):
        fpath = platform_state / fname
        marker = "+" if fpath.exists() else "!"
        print(f"  [{marker}] {fpath}")


if __name__ == "__main__":
    main()
