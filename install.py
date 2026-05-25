"""Platform installer orchestrator.

Runs launcher (deploy_environment.py) + extensions-service (provision-infra apply)
then merges both manifests into bootstrap/state/<extension>/.

Usage:
    # Lambda-only (default — no ECS cluster provisioned):
    python bootstrap/install.py <extension> \\
        --profile acd-arbitium-tt-dev \\
        --aws-region us-east-1 \\
        --github-repo Org/repo

    # Lambda + ECS (add ECS cluster, ECR, S3 results bucket):
    python bootstrap/install.py <extension> \\
        --profile acd-arbitium-tt-dev \\
        --aws-region us-east-1 \\
        --github-repo Org/repo \\
        --launch-type ec2    # or fargate

    # Skip one of the two provisioning steps (useful for re-runs):
    python bootstrap/install.py <extension> ... --skip-launcher
    python bootstrap/install.py <extension> ... --skip-extensions

    # Only merge existing manifests without reprovisioning:
    python bootstrap/install.py <extension> ... --merge-only

Prerequisites:
    Run once to create/update the required venvs for each repo:
        bash bootstrap/setup-venvs.sh
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PLATFORM_INSTALLER_ROOT = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _PLATFORM_INSTALLER_ROOT.parent

# Add bootstrap dir to sys.path so lib.merger is importable
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
    print(f"    bash bootstrap/setup-venvs.sh")
    print(f"\n  Or set up {repo_label} standalone:")
    if repo_label == "launcher":
        print(f"    cd launcher && python3.12 -m venv launch-venv && launch-venv/bin/pip install -r requirements.txt")
    else:
        print(f"    cd extensions-service && python3.12 -m venv venv && venv/bin/pip install -r requirements.txt")
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


def _read_ecs_profile(ext_folder: Path) -> dict:
    """Read ecs_profile.json from <ext_folder>/installer/infra/ if it exists."""
    profile_file = ext_folder / "installer" / "infra" / "ecs_profile.json"
    if not profile_file.is_file():
        return {}
    with open(profile_file, encoding="utf-8") as f:
        return json.load(f)


def _seed_runtime_profile(
    ext_folder: Path,
    extension: str,
    extensions_service_root: Path,
) -> None:
    """Seed extensions-service/state/<ext>/runtime_profile.json from ecs_profile.json.

    Only written when the file does not yet exist (first-time default).
    Subsequent runs preserve whatever extensions-service last saved.
    """
    ecs_profile_file = ext_folder / "installer" / "infra" / "ecs_profile.json"
    if not ecs_profile_file.is_file():
        return

    runtime_profile_path = extensions_service_root / "state" / extension / "runtime_profile.json"
    if runtime_profile_path.is_file():
        return  # Already seeded; extensions-service manages updates from here

    from datetime import datetime, timezone
    data = json.loads(ecs_profile_file.read_text(encoding="utf-8"))
    profile = {
        "state_version": 1,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    profile.update(data)
    runtime_profile_path.parent.mkdir(parents=True, exist_ok=True)
    with open(runtime_profile_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
        f.write("\n")
    print(f"\n[extension-specific] Seeded runtime profile → {runtime_profile_path}")


def _run_extension_specific(
    ext_folder: Path,
    env_name: str,
    profile: str,
    aws_region: str,
    dry_run: bool,
) -> Path | None:
    """Run provision_extension.sh from <ext_folder>/installer/infra/ if present.

    Returns the path to extra_resources.json if it was written, else None.
    """
    script = ext_folder / "installer" / "infra" / "provision_extension.sh"
    if not script.is_file():
        print(f"\n[extension-specific] provision_extension.sh not found at {script} — skipping")
        return None
    cmd = ["bash", str(script), env_name, "--aws-profile", profile, "--aws-region", aws_region]
    if dry_run:
        cmd.append("--dry-run")
    _run_subprocess(
        cmd,
        cwd=ext_folder / "installer" / "infra",
        description=f"Extension-specific provision: {ext_folder.name}",
    )
    extra_resources = ext_folder / "installer" / "infra" / "extra_resources.json"
    return extra_resources if extra_resources.is_file() else None


def _run_launcher(extension: str, profile: str, aws_region: str, github_repo: str) -> None:
    launcher_scripts = _WORKSPACE_ROOT / "launcher" / "scripts"
    _run_subprocess(
        [
            _launcher_python(),
            "deploy_environment.py",
            extension,
            "--aws-profile", profile,
            "--aws-region", aws_region,
            "--github-repo", github_repo,
        ],
        cwd=launcher_scripts,
        description=f"Launcher: deploy environment '{extension}'",
    )


def _run_extensions_service(
    extension: str,
    profile: str,
    launch_type: str | None,
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
        profile,
        "--github-repo",
        handlers_github_repo,
    ]
    if launch_type is not None:
        cmd += ["--launch-type", launch_type]
    if handlers_enable_staging_role:
        cmd.append("--enable-handlers-staging-role")
    mode = f"launch-type={launch_type}" if launch_type else "lambda-only"
    _run_subprocess(
        cmd,
        cwd=ext_service,
        description=f"Extensions-service: provision infra '{extension}' ({mode})",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Platform installer: orchestrates launcher + extensions-service provisioning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "extension",
        help="Platform environment name (launcher + extensions prefix, e.g. myenv)",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="AWS named profile with sufficient rights",
    )
    parser.add_argument(
        "--aws-region",
        default="us-east-1",
        help="AWS region (default: us-east-1)",
    )
    parser.add_argument(
        "--github-repo",
        required=True,
        help="GitHub org/repo trusted for release/OIDC deploy roles (e.g. Org/repo)",
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
        default=None,
        choices=["ec2", "fargate"],
        help="ECS launch type: ec2 or fargate. Omit to provision Lambda-only (no ECS cluster).",
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
    parser.add_argument(
        "--tenant",
        default=None,
        help=(
            "Optional tenant prefix for ENVIRONMENT in bootstrap state JSON only "
            "(e.g. acme → ENVIRONMENT acme_production). Does not change launcher or extensions-service."
        ),
    )
    parser.add_argument(
        "--extension-specific",
        default=None,
        metavar="FOLDER",
        help=(
            "Extension repo folder under workspace (path name only, not the platform env). "
            "Runs <folder>/installer/infra/provision_extension.sh if present; "
            "reads <folder>/installer/infra/ecs_profile.json for default ECS settings when --launch-type is omitted."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Skip AWS provisioning for launcher and extensions-service. "
            "If --extension-specific is set, runs provision_extension.sh in dry-run mode. "
            "Merges existing state manifests into platform state."
        ),
    )
    args = parser.parse_args()

    print(f"\nPlatform installer — extension: {args.extension}")
    print(f"  AWS profile : {args.profile}")
    print(f"  AWS region  : {args.aws_region}")
    handlers_repo = args.handlers_github_repo or args.github_repo
    print(f"  GitHub repo (release/OIDC) : {args.github_repo}")
    print(f"  GitHub repo (handlers) : {handlers_repo}")
    if args.dry_run:
        print("  Mode        : DRY-RUN")
    if args.tenant:
        print(f"  Tenant      : {args.tenant.strip()} (GitHub ENVIRONMENT → {args.tenant.strip()}_<stage>)")

    # Resolve ECS launch type: explicit CLI flag > ecs_profile.json > None (lambda-only)
    launch_type = args.launch_type
    ext_folder: Path | None = None
    extra_resources_path: Path | None = None

    if args.extension_specific:
        ext_folder = _WORKSPACE_ROOT / args.extension_specific
        if not ext_folder.is_dir():
            print(f"\nERROR: --extension-specific folder not found: {ext_folder}", file=sys.stderr)
            sys.exit(1)
        if launch_type is None:
            ecs_profile = _read_ecs_profile(ext_folder)
            if ecs_profile.get("launch_type"):
                launch_type = ecs_profile["launch_type"]
                print(
                    f"  ECS launch type : {launch_type} "
                    f"(from {args.extension_specific}/installer/infra/ecs_profile.json)"
                )

    print(f"  Launch type : {launch_type or 'lambda-only (no ECS)'}")

    skip_provisioning = args.merge_only or args.dry_run
    if not skip_provisioning:
        if not args.skip_launcher:
            _run_launcher(
                extension=args.extension,
                profile=args.profile,
                aws_region=args.aws_region,
                github_repo=args.github_repo,
            )
        else:
            print("\n[launcher] SKIPPED (--skip-launcher)")

        # Seed runtime_profile.json from ecs_profile.json before extensions-service runs,
        # so provision_ecs_capacity.sh picks up the extension's declared capacity settings.
        if ext_folder is not None and not args.skip_extensions:
            _seed_runtime_profile(ext_folder, args.extension, _WORKSPACE_ROOT / "extensions-service")

        if not args.skip_extensions:
            _run_extensions_service(
                extension=args.extension,
                profile=args.profile,
                launch_type=launch_type,
                handlers_github_repo=handlers_repo,
                handlers_enable_staging_role=args.handlers_enable_staging_role,
            )
        else:
            print("\n[extensions-service] SKIPPED (--skip-extensions)")
    else:
        reason = "--merge-only" if args.merge_only else "--dry-run"
        print(f"\n[provisioning] SKIPPED ({reason})")

    # Extension-specific provisioning (runs even in dry-run, passing --dry-run through)
    if ext_folder is not None and not args.merge_only:
        extra_resources_path = _run_extension_specific(
            ext_folder=ext_folder,
            env_name=args.extension,
            profile=args.profile,
            aws_region=args.aws_region,
            dry_run=args.dry_run,
        )

    # If merge-only or provision was skipped, try to locate an existing extra_resources.json
    if extra_resources_path is None and ext_folder is not None:
        er = ext_folder / "installer" / "infra" / "extra_resources.json"
        if er.is_file():
            extra_resources_path = er
            print(f"\n[extension-specific] Using existing extra_resources.json: {er}")

    # Merge manifests into platform state
    from lib.merger import merge_manifests

    platform_state = merge_manifests(
        extension=args.extension,
        launcher_root=_WORKSPACE_ROOT / "launcher",
        extensions_service_root=_WORKSPACE_ROOT / "extensions-service",
        platform_installer_root=_PLATFORM_INSTALLER_ROOT,
        aws_region=args.aws_region,
        tenant=args.tenant,
        extension_extra_resources=extra_resources_path,
    )

    print(f"\nInstallation complete for extension: {args.extension}")
    print(f"Platform state : {platform_state}")
    print("\nKey output files:")
    for fname in (
        "platform_resources.json",
        "platform_vars.production.json",
        "platform_vars.staging.json",
        "deploy_input.json",
        "env_config.py",
    ):
        fpath = platform_state / fname
        marker = "+" if fpath.exists() else "!"
        print(f"  [{marker}] {fpath}")


if __name__ == "__main__":
    main()
