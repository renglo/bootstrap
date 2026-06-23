"""Platform installer: synthesizes the CDK template for customer delivery.

Usage:
    # Synth templates (reads launcher/cdk/customer-config.json):
    python bootstrap/install.py synth

    # Post-deploy state write (run after customer executes CloudFormation):
    python bootstrap/install.py write-state \\
        --env-name myenv \\
        --aws-profile my-profile \\
        --aws-region us-east-1 \\
        [--compute-type fargate|ec2|lambda_only]

Workflow:
    1. Fill launcher/cdk/customer-config.json from customer-config.example.json
    2. Run: python bootstrap/install.py ensure-oidc --aws-profile <profile>
           (one-time per AWS account; creates the GitHub Actions OIDC provider)
    3. Run: python bootstrap/install.py synth
    4. Deliver bootstrap/output/<env>/ to customer (<env>-stack-a + <env>-stack-b templates)
    5. Customer deploys from bootstrap/output/<env>/:
           cdk deploy <env>-stack-a --app "python app.py"
           python upload_seed_image.py ...
           cdk deploy <env>-stack-b --app "python app.py" [--parameters VpcId=... SubnetIds=...]
    6. Run: python bootstrap/install.py write-state --env-name <env> ...
    7. OIDC CI/CD (GitHub Actions): push image to private ECR, create/update Lambda, wire API permissions
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_BOOTSTRAP_DIR = Path(__file__).resolve().parent
_BOOTSTRAP_VENV = _BOOTSTRAP_DIR / "venv"
_CDK_DIR = _WORKSPACE_ROOT / "launcher" / "cdk"
_COMPUTE_STACK_SRC = _WORKSPACE_ROOT / "extensions-service" / "scripts" / "compute_stack.py"
_SEED_IMAGE_SCRIPT = _WORKSPACE_ROOT / "launcher" / "scripts" / "upload_seed_image.py"
_SEED_IMAGE_DIR = _WORKSPACE_ROOT / "launcher" / "scripts" / "backend" / "seed-image"

_PACKAGE_FILES = (
    "app.py",
    "stack_names.py",
    "extension_loader.py",
    "extension_state_bundle.py",
    "platform_defaults.py",
    "platform_defaults.json",
    "requirements.txt",
    "customer-config.example.json",
)


def _resolve_python(venv_dir: Path) -> str:
    for candidate in (venv_dir / "bin" / "python", venv_dir / "Scripts" / "python.exe"):
        if candidate.is_file():
            return str(candidate)
    print("\nERROR: bootstrap venv not found.")
    print(f"  Expected: {venv_dir}")
    print("\n  Run: bash bootstrap/setup-venvs.sh --bootstrap-only")
    sys.exit(1)


def _bootstrap_python() -> str:
    return _resolve_python(_BOOTSTRAP_VENV)


def _ensure_bootstrap_python() -> None:
    bootstrap_py = _bootstrap_python()
    if Path(sys.executable).resolve() != Path(bootstrap_py).resolve():
        os.execv(bootstrap_py, [bootstrap_py, *sys.argv])


def _cdk_app_command(python: str) -> str:
    if " " in python:
        return f'"{python}" app.py'
    return f"{python} app.py"


def _load_customer_config() -> dict:
    config_file = _CDK_DIR / "customer-config.json"
    if not config_file.is_file():
        return {}
    return json.loads(config_file.read_text(encoding="utf-8"))


def _cdk_executable() -> str:
    if platform.system() == "Windows":
        cdk = shutil.which("cdk.cmd") or shutil.which("cdk")
    else:
        cdk = shutil.which("cdk")
    if not cdk:
        raise RuntimeError(
            "CDK CLI not found. Install with: npm install -g aws-cdk\n"
            "Then run: bash bootstrap/setup-venvs.sh --bootstrap-only"
        )
    return cdk


def _default_output_path(env_name: str) -> Path:
    return _BOOTSTRAP_DIR / "output" / env_name


def _remove_cdk_out_marker(output_path: Path) -> None:
    """Remove CDK synth version marker file that blocks `cdk deploy` on Windows.

    `cdk synth --output <dir>` writes a small `cdk.out` *file* into the assembly
    directory. `cdk deploy` expects `cdk.out/` to be a directory in cwd — remove
    the marker so deploy can create the real cache directory.
    """
    marker = output_path / "cdk.out"
    if marker.is_file():
        marker.unlink()
        print(f"  Removed CDK synth marker file: {marker.name}")


def _package_deploy_tree(output_path: Path) -> None:
    """Copy CDK app sources into the synth output so deploy can run from that folder."""
    for name in _PACKAGE_FILES:
        src = _CDK_DIR / name
        if src.is_file():
            shutil.copy2(src, output_path / name)

    config_src = _CDK_DIR / "customer-config.json"
    if config_src.is_file():
        shutil.copy2(config_src, output_path / "customer-config.json")

    stacks_src = _CDK_DIR / "stacks"
    stacks_dest = output_path / "stacks"
    if stacks_dest.exists():
        shutil.rmtree(stacks_dest)
    shutil.copytree(stacks_src, stacks_dest)

    extensions_dest = output_path / "extensions"
    extensions_dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_COMPUTE_STACK_SRC, extensions_dest / "compute_stack.py")


def _copy_seed_image_tooling(output_path: Path) -> None:
    """Copy seed image upload script and Docker build context into the deploy package."""
    if _SEED_IMAGE_SCRIPT.is_file():
        shutil.copy2(_SEED_IMAGE_SCRIPT, output_path / "upload_seed_image.py")
    seed_dest = output_path / "seed-image"
    if _SEED_IMAGE_DIR.is_dir():
        if seed_dest.exists():
            shutil.rmtree(seed_dest)
        shutil.copytree(_SEED_IMAGE_DIR, seed_dest)


def cmd_synth(extension_path: str | None) -> None:
    """Run cdk synth and report the output path."""
    config_file = _CDK_DIR / "customer-config.json"
    if not config_file.is_file():
        example = _CDK_DIR / "customer-config.example.json"
        print(f"\nERROR: customer-config.json not found.")
        print(f"  Copy the example and fill it in:")
        print(f"    cp {example} {config_file}")
        sys.exit(1)

    cfg = _load_customer_config()
    env_name = str(cfg.get("env_name", "")).strip()
    if not env_name:
        print("\nERROR: customer-config.json must set env_name.")
        sys.exit(1)

    ext_path = (extension_path or str(cfg.get("extension_path", ""))).strip()
    output_path = _default_output_path(env_name)
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    python = sys.executable
    cdk = _cdk_executable()
    print(f"\nRunning cdk synth in {_CDK_DIR}")
    print(f"Output directory: {output_path}")
    result = subprocess.run(
        [cdk, "synth", "--app", _cdk_app_command(python), "--output", str(output_path)],
        cwd=str(_CDK_DIR),
    )
    if result.returncode != 0:
        sys.exit(result.returncode)

    _remove_cdk_out_marker(output_path)

    print("\nPackaging deploy tree...")
    _package_deploy_tree(output_path)

    if ext_path:
        sys.path.insert(0, str(_CDK_DIR))
        from extension_loader import bundle_extension_infra  # noqa: PLC0415
        from extension_state_bundle import emit_extension_state_bundle  # noqa: PLC0415

        print("\nBundling extension infra...")
        bundle_extension_infra(
            output_path,
            workspace_root=_WORKSPACE_ROOT,
            extension_path=ext_path,
        )

        print("\nEmitting extension state bundle...")
        emit_extension_state_bundle(
            output_path,
            env_name=env_name,
            extension_path=ext_path,
            workspace_root=_WORKSPACE_ROOT,
        )

    _copy_seed_image_tooling(output_path)

    templates = sorted(output_path.glob(f"{env_name}-stack-*.template.json"))
    stack_a = f"{env_name}-stack-a"
    stack_b = f"{env_name}-stack-b"
    print("\nSynthesis complete.")
    print(f"Output directory: {output_path}")
    if templates:
        print("Generated templates:")
        for t in templates:
            print(f"  {t.name}")
    print("\nDeploy from this directory:")
    print(f"  cd {output_path}")
    print(f'  cdk deploy {stack_a} --app "python app.py" --output .')
    print(f'  python upload_seed_image.py --env-name {env_name} --aws-profile <profile>')
    print(f'  cdk deploy {stack_b} --app "python app.py" --output .')


def cmd_ensure_oidc(aws_profile: str | None, aws_region: str, dry_run: bool) -> None:
    """Ensure the GitHub Actions OIDC provider exists (account-level prerequisite)."""
    sys.path.insert(0, str(Path(__file__).parent))
    from ensure_oidc_provider import ensure_github_oidc_provider  # noqa: PLC0415

    ensure_github_oidc_provider(
        aws_profile=aws_profile,
        aws_region=aws_region,
        apply_changes=not dry_run,
    )


def cmd_write_state(
    env_name: str,
    aws_profile: str | None,
    aws_region: str,
    compute_type: str | None,
    extension_state_manifest: str | None,
) -> None:
    """Delegate to bootstrap/write_state.py."""
    sys.path.insert(0, str(Path(__file__).parent))
    from write_state import write_state  # noqa: PLC0415

    cfg = _load_customer_config()
    output_path = _default_output_path(env_name)
    manifest_path = Path(extension_state_manifest) if extension_state_manifest else None
    if manifest_path is None:
        candidate = output_path / "extension-state.json"
        if candidate.is_file():
            manifest_path = candidate

    write_state(
        env_name=env_name,
        aws_profile=aws_profile,
        aws_region=aws_region,
        compute_type=compute_type,
        customer_config=cfg,
        synth_output_dir=output_path,
        extension_state_manifest=manifest_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Platform installer — CDK synth + post-deploy state write",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_synth = sub.add_parser("synth", help="Run cdk synth and generate CloudFormation templates")
    p_synth.add_argument(
        "--extension-path",
        default=None,
        help="Extension repo folder under workspace root (default: extension_path in customer-config.json)",
    )

    p_oidc = sub.add_parser(
        "ensure-oidc",
        help="Create the GitHub Actions OIDC provider in the AWS account (one-time, run before cdk deploy)",
    )
    p_oidc.add_argument("--aws-profile", default=None, help="AWS named profile")
    p_oidc.add_argument("--aws-region", default="us-east-1", help="AWS region (default: us-east-1)")
    p_oidc.add_argument("--dry-run", action="store_true", help="Plan without creating resources")

    p_state = sub.add_parser(
        "write-state",
        help="Post-deploy: read CF outputs, upload blueprints, write state to S3",
    )
    p_state.add_argument("--env-name", required=True, help="Environment name")
    p_state.add_argument("--aws-profile", default=None, help="AWS named profile")
    p_state.add_argument("--aws-region", default="us-east-1", help="AWS region")
    p_state.add_argument(
        "--compute-type",
        default=None,
        choices=["lambda_only", "fargate", "ec2"],
        help="Default: customer-config.json compute_type",
    )
    p_state.add_argument(
        "--extension-state-manifest",
        default=None,
        help="Path to extension-state.json from synth (default: bootstrap/output/<env>/extension-state.json)",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    _ensure_bootstrap_python()

    if args.command == "synth":
        cmd_synth(args.extension_path)
    elif args.command == "ensure-oidc":
        cmd_ensure_oidc(args.aws_profile, args.aws_region, args.dry_run)
    elif args.command == "write-state":
        cmd_write_state(
            args.env_name,
            args.aws_profile,
            args.aws_region,
            args.compute_type,
            args.extension_state_manifest,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
