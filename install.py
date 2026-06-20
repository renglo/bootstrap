"""Platform installer: synthesizes the CDK template for customer delivery.

Usage:
    # Synth template (reads launcher/cdk/customer-config.json):
    python bootstrap/install.py synth [--output ./output]

    # Post-deploy state write (run after customer executes CloudFormation):
    python bootstrap/install.py write-state \\
        --env-name myenv \\
        --aws-profile my-profile \\
        --aws-region us-east-1 \\
        [--compute-type fargate|ec2|lambda_only]

Workflow:
    1. Fill launcher/cdk/customer-config.json from customer-config.example.json
    2. Run: python bootstrap/install.py synth
    3. Deliver launcher/cdk/output/template.yaml to customer
    4. Customer runs: aws cloudformation deploy --template-file template.yaml \\
           --stack-name <env>-platform --capabilities CAPABILITY_NAMED_IAM \\
           [--parameter-overrides VpcId=<vpc-id> SubnetIds=<subnet-1>,<subnet-2>]
           (VpcId/SubnetIds required when compute_type is ec2; pick default VPC in console or via CLI)
    5. Run: python bootstrap/install.py write-state --env-name <env> ...
    6. OIDC CI/CD (GitHub Actions): push image to private ECR, create/update Lambda, wire API permissions
"""

from __future__ import annotations

import argparse
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


def cmd_synth(output_dir: str) -> None:
    """Run cdk synth and report the output path."""
    config_file = _CDK_DIR / "customer-config.json"
    if not config_file.is_file():
        example = _CDK_DIR / "customer-config.example.json"
        print(f"\nERROR: customer-config.json not found.")
        print(f"  Copy the example and fill it in:")
        print(f"    cp {example} {config_file}")
        sys.exit(1)

    output_path = Path(output_dir) if Path(output_dir).is_absolute() else _CDK_DIR / output_dir
    python = sys.executable
    cdk = _cdk_executable()
    print(f"\nRunning cdk synth in {_CDK_DIR}")
    result = subprocess.run(
        [cdk, "synth", "--app", _cdk_app_command(python), "--output", str(output_path)],
        cwd=str(_CDK_DIR),
    )
    if result.returncode != 0:
        sys.exit(result.returncode)

    templates = list(output_path.glob("*.template.json"))
    yaml_templates = list(output_path.glob("*.yaml"))
    all_outputs = templates + yaml_templates
    print("\nSynthesis complete.")
    print(f"Output directory: {output_path}")
    if all_outputs:
        print("Generated templates:")
        for t in sorted(all_outputs):
            print(f"  {t}")
    print("\nDeliver all .template.json files to the customer for CloudFormation.")


def cmd_write_state(
    env_name: str,
    aws_profile: str | None,
    aws_region: str,
    compute_type: str | None,
    extension_infra_dir: str | None,
) -> None:
    """Delegate to bootstrap/write_state.py."""
    sys.path.insert(0, str(Path(__file__).parent))
    from write_state import write_state  # noqa: PLC0415

    ext_dir = Path(extension_infra_dir) if extension_infra_dir else None
    write_state(
        env_name=env_name,
        aws_profile=aws_profile,
        aws_region=aws_region,
        compute_type=compute_type,
        extension_infra_dir=ext_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Platform installer — CDK synth + post-deploy state write",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # synth sub-command
    p_synth = sub.add_parser("synth", help="Run cdk synth and generate the CloudFormation template")
    p_synth.add_argument(
        "--output",
        default="./output",
        help="Output directory for synthesized templates (default: ./output inside launcher/cdk/)",
    )

    # write-state sub-command
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
        "--extension-infra-dir",
        default=None,
        help="Extension installer/infra dir (default: auto-discover)",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    _ensure_bootstrap_python()

    if args.command == "synth":
        cmd_synth(args.output)
    elif args.command == "write-state":
        cmd_write_state(
            args.env_name,
            args.aws_profile,
            args.aws_region,
            args.compute_type,
            args.extension_infra_dir,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
