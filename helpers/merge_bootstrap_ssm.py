#!/usr/bin/env python3
"""Fetch bootstrap SSM config and merge ECS network parameters into VARS.

CloudFormation tokens (Fn::If) for ECS VPC/subnets/SG are stored in separate SSM
parameters because Fn.to_json_string cannot embed them in the JSON envelopes.

Usage:
    python bootstrap/helpers/merge_bootstrap_ssm.py <env> production \\
        --aws-profile acd-arbitium-tt-dev --aws-region us-east-1

    # deploy-input (handlers):
    python bootstrap/helpers/merge_bootstrap_ssm.py <env> deploy-input \\
        --aws-profile acd-arbitium-tt-dev
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BOOTSTRAP_DIR = Path(__file__).resolve().parents[1]
if str(_BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_DIR))

from lib.config_builder import (  # noqa: E402
    ECS_NETWORK_SSM_PATHS,
    ssm_deploy_input_path,
    ssm_platform_vars_path,
)


def _ssm_client(profile: str | None, region: str):
    import boto3

    if profile:
        return boto3.Session(profile_name=profile, region_name=region).client("ssm")
    return boto3.Session(region_name=region).client("ssm")


def _get_param(ssm, name: str) -> str | None:
    try:
        resp = ssm.get_parameter(Name=name)
        return str(resp["Parameter"]["Value"])
    except ssm.exceptions.ParameterNotFound:
        return None
    except Exception:
        return None


def _merge_ecs_network(ssm, env_name: str, vars_block: dict[str, str]) -> dict[str, str]:
    merged = dict(vars_block)
    for var_key, path_suffix in ECS_NETWORK_SSM_PATHS.items():
        if merged.get(var_key):
            continue
        value = _get_param(ssm, f"/{env_name}/bootstrap/{path_suffix}")
        if value:
            merged[var_key] = value
    return merged


def fetch_platform_vars(
    env_name: str,
    stage: str,
    *,
    aws_profile: str | None,
    aws_region: str,
) -> dict:
    ssm = _ssm_client(aws_profile, aws_region)
    raw = _get_param(ssm, ssm_platform_vars_path(env_name, stage))
    if not raw:
        raise SystemExit(f"SSM parameter not found: {ssm_platform_vars_path(env_name, stage)}")
    payload = json.loads(raw)
    vars_block = payload.get("VARS")
    if not isinstance(vars_block, dict):
        raise SystemExit("platform-vars payload missing VARS object")
    payload["VARS"] = _merge_ecs_network(ssm, env_name, {str(k): str(v) for k, v in vars_block.items()})
    return payload


def fetch_deploy_input(
    env_name: str,
    *,
    aws_profile: str | None,
    aws_region: str,
) -> dict:
    ssm = _ssm_client(aws_profile, aws_region)
    raw = _get_param(ssm, ssm_deploy_input_path(env_name))
    if not raw:
        raise SystemExit(f"SSM parameter not found: {ssm_deploy_input_path(env_name)}")
    payload = json.loads(raw)
    vars_block = payload.get("VARS")
    if not isinstance(vars_block, dict):
        raise SystemExit("deploy-input payload missing VARS object")
    payload["VARS"] = _merge_ecs_network(ssm, env_name, {str(k): str(v) for k, v in vars_block.items()})
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge bootstrap SSM JSON with ECS network parameters")
    parser.add_argument("env_name", help="Environment name (e.g. arbitiumrs12)")
    parser.add_argument(
        "target",
        choices=["production", "staging", "deploy-input"],
        help="platform-vars stage or deploy-input",
    )
    parser.add_argument("--aws-profile", default=None)
    parser.add_argument("--aws-region", default="us-east-1")
    args = parser.parse_args()

    if args.target == "deploy-input":
        payload = fetch_deploy_input(args.env_name, aws_profile=args.aws_profile, aws_region=args.aws_region)
    else:
        payload = fetch_platform_vars(
            args.env_name,
            args.target,
            aws_profile=args.aws_profile,
            aws_region=args.aws_region,
        )
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
