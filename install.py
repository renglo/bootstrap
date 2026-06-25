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
    2. Run: python bootstrap/install.py synth
    3. Deliver bootstrap/output/<env>/ to customer (templates + extension state at root)
    4. Customer deploys:
           aws cloudformation deploy ... (templates at output/<env>/ root), or
           cdk deploy from output/<env>/cdk/
           python bootstrap/upload_seed_image.py ... (between stack-a and stack-b)
    5. Run: python bootstrap/install.py write-state --env-name <env> ...
    6. OIDC CI/CD (GitHub Actions): push image to private ECR, create/update Lambda, wire API permissions
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


def _package_deploy_tree(cdk_dir: Path) -> None:
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

    extensions_dest = cdk_dir / "extensions"
    extensions_dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_COMPUTE_STACK_SRC, extensions_dest / "compute_stack.py")


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
    _package_deploy_tree(cdk_dir)

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
    print("\nDeploy with CloudFormation CLI (from env root):")
    print(f"  cd {env_root}")
    print(f"  aws cloudformation deploy --template-file {stack_a}.template.json ...")
    print(f"  cd <infra-installer>")
    print(f"  python bootstrap/upload_seed_image.py --env-name {env_name} --aws-profile <profile>")
    print(f"  cd {env_root}")
    print(f"  aws cloudformation deploy --template-file {stack_b}.template.json ...")
    print("\nDeploy with CDK CLI (from cdk/):")
    print(f"  cd {cdk_dir}")
    print(f'  cdk deploy {stack_a} --app "python app.py" --output . [--parameters CreateGitHubOIDC=true]')
    print(f"  cd <infra-installer>")
    print(f"  python bootstrap/upload_seed_image.py --env-name {env_name} --aws-profile <profile>")
    print(f"  cd {cdk_dir}")
    print(f'  cdk deploy {stack_b} --app "python app.py" --output .')


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
