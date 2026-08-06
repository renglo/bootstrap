"""Platform installer: synthesizes the CDK template for customer delivery.

Usage:
    # Synth templates (reads launcher/cdk/customer-config.json):
    python bootstrap/install.py synth

Workflow:
    1. Fill launcher/cdk/customer-config.json from customer-config.example.json
    2. Run: python bootstrap/install.py synth
    3. Deliver bootstrap/output/<env>/ to customer (templates + extension state at root)
    4. Customer deploys:
           aws cloudformation deploy ... (templates at output/<env>/ root), or
           cdk deploy from output/<env>/cdk/
           (stack-a builds the seed image automatically; no manual step before stack-b)
    5. python bootstrap/install.py write-state --env-name <env> --aws-profile <profile>
    6. python bootstrap/install.py write-local-config --env-name <env> --aws-profile <profile>
    7. OIDC CI/CD reads bootstrap config from SSM Parameter Store
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
_CDK_SUBDIR = "cdk"

_PACKAGE_FILES = (
    "app.py",
    "stack_names.py",
    "extension_loader.py",
    "platform_defaults.py",
    "platform_defaults.json",
    "requirements.txt",
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
    """Re-exec using bootstrap/venv so CDK runs app.py with venv site-packages.

    Do not compare sys.executable paths with resolve(): on macOS, venv/bin/python
    is a symlink to the same Homebrew binary as python3.12, so resolve() matches
    even when the venv is not active and aws_cdk is missing.
    """
    bootstrap_py = _bootstrap_python()
    venv_root = _BOOTSTRAP_VENV.resolve()
    if Path(sys.prefix).resolve() != venv_root:
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


def _cdk_output_path(env_name: str) -> Path:
    return _default_output_path(env_name) / _CDK_SUBDIR


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


def _package_blueprints(cdk_dir: Path, extension_path: str) -> None:
    """Stage blueprint JSON for the stack-b custom resource asset."""
    dest = cdk_dir / "bootstrap-assets" / "blueprints"
    if dest.exists():
        shutil.rmtree(dest)
    launcher_src = _WORKSPACE_ROOT / "launcher" / "scripts" / "blueprints"
    if launcher_src.is_dir():
        shutil.copytree(launcher_src, dest / "launcher")
    if extension_path:
        ext_root = _WORKSPACE_ROOT / extension_path
        for candidate in (ext_root / "blueprints", ext_root / "installer" / "blueprints"):
            if candidate.is_dir():
                shutil.copytree(candidate, dest / "extension")
                break


def _package_lib(cdk_dir: Path) -> None:
    lib_dest = cdk_dir / "lib"
    lib_dest.mkdir(parents=True, exist_ok=True)
    src = _BOOTSTRAP_DIR / "lib" / "config_builder.py"
    if src.is_file():
        shutil.copy2(src, lib_dest / "config_builder.py")
        init_file = lib_dest / "__init__.py"
        if not init_file.is_file():
            init_file.write_text("", encoding="utf-8")
    cdk_lib_src = _CDK_DIR / "lib" / "config_builder.py"
    if cdk_lib_src.is_file():
        shutil.copy2(cdk_lib_src, lib_dest / "config_builder.py")


def _package_deploy_tree(cdk_dir: Path, *, extension_path: str = "") -> None:
    """Copy CDK app sources into cdk/ so `cdk deploy --app python app.py` works."""
    for name in _PACKAGE_FILES:
        src = _CDK_DIR / name
        if src.is_file():
            shutil.copy2(src, cdk_dir / name)

    config_src = _CDK_DIR / "customer-config.json"
    if config_src.is_file():
        shutil.copy2(config_src, cdk_dir / "customer-config.json")

    stacks_src = _CDK_DIR / "stacks"
    stacks_dest = cdk_dir / "stacks"
    if stacks_dest.exists():
        shutil.rmtree(stacks_dest)
    shutil.copytree(stacks_src, stacks_dest)

    # Lambda zip sources (e.g. webhook_edge) resolved relative to the packaged tree
    assets_src = _CDK_DIR / "assets"
    assets_dest = cdk_dir / "assets"
    if assets_src.is_dir():
        if assets_dest.exists():
            shutil.rmtree(assets_dest)
        shutil.copytree(
            assets_src,
            assets_dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    extensions_dest = cdk_dir / "extensions"
    extensions_dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_COMPUTE_STACK_SRC, extensions_dest / "compute_stack.py")

    _package_lib(cdk_dir)
    _package_blueprints(cdk_dir, extension_path)


def _copy_templates_to_env_root(cdk_dir: Path, env_root: Path, env_name: str) -> list[Path]:
    """Copy CloudFormation templates to env root for aws cloudformation deploy."""
    copied: list[Path] = []
    for src in sorted(cdk_dir.glob(f"{env_name}-stack-*.template.json")):
        dest = env_root / src.name
        shutil.copy2(src, dest)
        copied.append(dest)
    return copied


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
    env_root = _default_output_path(env_name)
    cdk_dir = env_root / _CDK_SUBDIR
    if env_root.exists():
        shutil.rmtree(env_root)
    cdk_dir.mkdir(parents=True, exist_ok=True)

    python = sys.executable
    cdk = _cdk_executable()
    print(f"\nRunning cdk synth in {_CDK_DIR}")
    print(f"CDK assembly: {cdk_dir}")
    result = subprocess.run(
        [cdk, "synth", "--app", _cdk_app_command(python), "--output", str(cdk_dir)],
        cwd=str(_CDK_DIR),
    )
    if result.returncode != 0:
        sys.exit(result.returncode)

    _remove_cdk_out_marker(cdk_dir)

    print("\nPackaging CDK deploy tree...")
    _package_deploy_tree(cdk_dir, extension_path=ext_path)

    if ext_path:
        sys.path.insert(0, str(_CDK_DIR))
        from extension_loader import bundle_extension_infra  # noqa: PLC0415
        from extension_state_bundle import emit_extension_state_bundle  # noqa: PLC0415

        print("\nBundling extension infra...")
        bundle_extension_infra(
            cdk_dir,
            workspace_root=_WORKSPACE_ROOT,
            extension_path=ext_path,
        )

        print("\nEmitting extension state bundle...")
        emit_extension_state_bundle(
            env_root,
            env_name=env_name,
            extension_path=ext_path,
            workspace_root=_WORKSPACE_ROOT,
        )

    templates = _copy_templates_to_env_root(cdk_dir, env_root, env_name)
    stack_a = f"{env_name}-stack-a"
    stack_b = f"{env_name}-stack-b"
    print("\nSynthesis complete.")
    print(f"Output directory: {env_root}")
    if templates:
        print("CloudFormation templates (env root):")
        for t in templates:
            print(f"  {t.name}")
    print(f"\nCDK deploy package: {cdk_dir}")
    print("\nStack-a builds and pushes the seed image automatically (CodeBuild custom resource).")
    print("No manual step is required between stack-a and stack-b.")
    print("\nDeploy with CloudFormation CLI (from env root):")
    print(f"  cd {env_root}")
    print(f"  aws cloudformation deploy --template-file {stack_a}.template.json ...")
    print(f"  aws cloudformation deploy --template-file {stack_b}.template.json ...")
    print("\nDeploy with CDK CLI (from cdk/):")
    print(f"  cd {cdk_dir}")
    print(f'  cdk deploy {stack_a} --app "python app.py" --output . [--parameters CreateGitHubOIDC=true]')
    print(f'  cdk deploy {stack_b} --app "python app.py" --output .')
    print(f"\n  # Manual rebuild of the seed image (optional):")
    print(f"  aws codebuild start-build --project-name {env_name}-seed-image ...")
    print("\nAfter stack-b deploy, write bootstrap config to SSM:")
    print(f"  python bootstrap/install.py write-state --env-name {env_name} --aws-profile <profile>")
    print("\nGenerate local developer config bundle (share with devs):")
    print(f"  python bootstrap/install.py write-local-config --env-name {env_name} --aws-profile <profile>")
    print(f"  → bootstrap/output/{env_name}/local-dev/")
    print("\nSSM paths written by write-state:")
    print(f"  /{env_name}/bootstrap/platform-vars/production")
    print(f"  /{env_name}/bootstrap/platform-vars/staging")
    print(f"  /{env_name}/bootstrap/deploy-input")
    print(f"  /{env_name}/bootstrap/ecs-vpc            (ec2 compute only)")
    print(f"  /{env_name}/bootstrap/ecs-subnets         (ec2 compute only)")
    print(f"  /{env_name}/bootstrap/ecs-security-groups (ec2 compute only)")
    print("Local next step: python bootstrap/install.py write-local-config --env-name <env> ...")
    print("CI/CD (optional, cloud only): see bootstrap/README.md §8.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Platform installer — CDK synth for customer delivery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    p_synth = sub.add_parser("synth", help="Run cdk synth and generate CloudFormation templates")
    p_synth.add_argument(
        "--extension-path",
        default=None,
        help="Extension repo folder under workspace root (default: extension_path in customer-config.json)",
    )

    p_write = sub.add_parser(
        "write-state",
        help="Write bootstrap SSM parameters from deployed stack-a/stack-b outputs",
    )
    p_write.add_argument("--env-name", required=True, help="Environment name (e.g. productora0719)")
    p_write.add_argument("--aws-profile", default=None, help="AWS CLI profile name")
    p_write.add_argument(
        "--aws-region",
        default=None,
        help="AWS region (default: aws_region from customer-config.json)",
    )
    p_write.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing to SSM",
    )

    p_local = sub.add_parser(
        "write-local-config",
        help="Generate local dev config files (env_config.py, .env.development) from SSM",
    )
    p_local.add_argument("--env-name", required=True, help="Environment name (e.g. stanley0731)")
    p_local.add_argument("--aws-profile", default=None, help="AWS CLI profile name")
    p_local.add_argument(
        "--aws-region",
        default=None,
        help="AWS region (default: aws_region from customer-config.json)",
    )
    p_local.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: bootstrap/output/<env>/local-dev)",
    )
    p_local.add_argument(
        "--stage",
        default="production",
        help="platform-vars stage (default: production)",
    )
    p_local.add_argument(
        "--invite-fe-base-url",
        default="http://127.0.0.1:5174",
        help="INVITE_FE_BASE_URL for local invite email links",
    )
    p_local.add_argument(
        "--vite-extensions",
        default="schd,data,pes",
        help="VITE_EXTENSIONS value for .env.development",
    )
    p_local.add_argument(
        "--no-preserve-secrets",
        action="store_true",
        help="Generate new SECRET_KEY / CSRF_SESSION_KEY even if output files exist",
    )
    p_local.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without writing files",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    _ensure_bootstrap_python()

    if args.command == "synth":
        cmd_synth(args.extension_path)
    elif args.command == "write-state":
        from write_state import run_write_state

        run_write_state(
            env_name=args.env_name.strip(),
            aws_profile=args.aws_profile,
            aws_region=args.aws_region,
            dry_run=args.dry_run,
        )
    elif args.command == "write-local-config":
        from write_local_config import run_write_local_config

        cfg = _load_customer_config()
        region = (args.aws_region or cfg.get("aws_region") or "us-east-1").strip()
        output_dir = Path(args.output_dir).resolve() if args.output_dir else None
        run_write_local_config(
            env_name=args.env_name.strip(),
            aws_profile=args.aws_profile,
            aws_region=region,
            output_dir=output_dir,
            stage=args.stage.strip() or "production",
            invite_fe_base_url=args.invite_fe_base_url.strip(),
            extensions=args.vite_extensions.strip(),
            preserve_secrets=not args.no_preserve_secrets,
            dry_run=args.dry_run,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
