"""Write bootstrap SSM parameters from CloudFormation stack outputs.

Run after stack-a (builds ECR :seed tag automatically) and stack-b are deployed:

    python bootstrap/install.py write-state \\
        --env-name productora0719 \\
        --aws-profile <profile> \\
        --aws-region us-east-1
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_BOOTSTRAP_DIR = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _BOOTSTRAP_DIR.parent

if str(_BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_DIR))

from lib.config_builder import (  # noqa: E402
    build_deploy_input_envelope,
    build_deploy_input_vars,
    build_ecs_network_vars,
    build_launcher_vars,
    build_platform_vars_envelope,
    ssm_deploy_input_path,
    ssm_ecs_security_groups_path,
    ssm_ecs_subnets_path,
    ssm_ecs_vpc_path,
    ssm_platform_vars_path,
)

_STACK_OK_STATUSES = frozenset({"CREATE_COMPLETE", "UPDATE_COMPLETE"})

_STAGE_OUTPUT_KEYS: dict[str, dict[str, str]] = {
    "production": {
        "fn_name": "BackendLambdaFunctionNameProduction",
        "rest_url": "RestApiUrlProduction",
        "ws_url": "WebSocketUrlProduction",
        "ws_connections": "WebSocketConnectionsUrlProduction",
    },
    "staging": {
        "fn_name": "BackendLambdaFunctionNameStaging",
        "rest_url": "RestApiUrlStaging",
        "ws_url": "WebSocketUrlStaging",
        "ws_connections": "WebSocketConnectionsUrlStaging",
    },
}

_AMPLIFY_CONSOLE_URL_KEYS = {
    "production": "AmplifyConsoleUrlProduction",
    "staging": "AmplifyConsoleUrlStaging",
}

_ECS_NETWORK_OUTPUT_KEYS = {
    "vpc": "HandlersComputeVpcId",
    "subnets": "HandlersComputeSubnetIds",
    "security_group": "HandlersComputeSecurityGroupId",
}


def _load_customer_config(env_name: str) -> dict[str, Any]:
    candidates = (
        _BOOTSTRAP_DIR / "output" / env_name / "cdk" / "customer-config.json",
        _WORKSPACE_ROOT / "launcher" / "cdk" / "customer-config.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg_env = str(cfg.get("env_name", "")).strip()
        if cfg_env and cfg_env != env_name:
            continue
        return cfg
    raise SystemExit(
        f"ERROR: customer-config.json not found for env_name={env_name!r}.\n"
        f"  Expected one of:\n"
        + "\n".join(f"    {p}" for p in candidates)
    )


def _cfn_client(aws_profile: str | None, aws_region: str):
    import boto3

    if aws_profile:
        return boto3.Session(profile_name=aws_profile, region_name=aws_region).client("cloudformation")
    return boto3.Session(region_name=aws_region).client("cloudformation")


def _ssm_client(aws_profile: str | None, aws_region: str):
    import boto3

    if aws_profile:
        return boto3.Session(profile_name=aws_profile, region_name=aws_region).client("ssm")
    return boto3.Session(region_name=aws_region).client("ssm")


def _stack_outputs(cfn, stack_name: str) -> dict[str, str]:
    resp = cfn.describe_stacks(StackName=stack_name)
    stacks = resp.get("Stacks") or []
    if not stacks:
        raise SystemExit(f"ERROR: stack not found: {stack_name}")
    stack = stacks[0]
    status = str(stack.get("StackStatus", ""))
    if status not in _STACK_OK_STATUSES:
        raise SystemExit(f"ERROR: stack {stack_name} is {status!r}; expected CREATE_COMPLETE or UPDATE_COMPLETE")
    outputs: dict[str, str] = {}
    for item in stack.get("Outputs") or []:
        key = str(item.get("OutputKey", "")).strip()
        value = item.get("OutputValue")
        if key and value is not None:
            outputs[key] = str(value)
    return outputs


def _require_output(outputs: dict[str, str], key: str, *, stack_name: str) -> str:
    value = outputs.get(key, "").strip()
    if not value:
        raise SystemExit(f"ERROR: missing CloudFormation output {key!r} on stack {stack_name}")
    return value


def _stage_app(outputs_b: dict[str, str], stage: str, env_name: str) -> dict[str, str]:
    mapping = _STAGE_OUTPUT_KEYS[stage]
    app: dict[str, str] = {}
    for field, output_key in mapping.items():
        value = outputs_b.get(output_key, "").strip()
        if value:
            app[field] = value
    if "fn_name" not in app:
        app["fn_name"] = f"{env_name}-backend-{stage}"
    return app


def _extension_vars(env_name: str, outputs_b: dict[str, str]) -> dict[str, str]:
    manifest_path = _BOOTSTRAP_DIR / "output" / env_name / "extension-state.json"
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    keys: list[str] = []
    for block in ("runtime_stack_outputs", "inventory_stack_outputs"):
        raw = manifest.get(block) or []
        if isinstance(raw, list):
            keys.extend(str(k) for k in raw if str(k).strip())
    vars_out: dict[str, str] = {}
    for key in keys:
        value = outputs_b.get(key, "").strip()
        if value:
            vars_out[key] = value
    return vars_out


def _put_parameter(
    ssm,
    name: str,
    payload: dict[str, Any],
    *,
    dry_run: bool,
) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if dry_run:
        print(f"  [dry-run] would write {name} ({len(body)} bytes)")
        return
    ssm.put_parameter(Name=name, Value=body, Type="String", Overwrite=True)
    print(f"  wrote {name}")


def _put_plain_parameter(ssm, name: str, value: str, *, dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] would write {name} = {value!r}")
        return
    ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True)
    print(f"  wrote {name}")


def run_write_state(
    *,
    env_name: str,
    aws_profile: str | None = None,
    aws_region: str | None = None,
    dry_run: bool = False,
) -> None:
    cfg = _load_customer_config(env_name)
    region = (aws_region or str(cfg.get("aws_region", "us-east-1")).strip() or "us-east-1")
    account = str(cfg.get("aws_account", "")).strip()
    if not account:
        raise SystemExit("ERROR: customer-config.json must set aws_account")

    github_repo = str(cfg.get("github_repo", "")).strip()
    if not github_repo:
        raise SystemExit("ERROR: customer-config.json must set github_repo")
    github_handlers_repo = str(cfg.get("github_handlers_repo", github_repo)).strip() or github_repo
    enable_staging = bool(cfg.get("enable_staging", True))
    compute_type = str(cfg.get("compute_type", "fargate")).strip() or "fargate"
    from_email = str(cfg.get("email_from", "") or "").strip()

    stack_a = f"{env_name}-stack-a"
    stack_b = f"{env_name}-stack-b"

    cfn = _cfn_client(aws_profile, region)
    ssm = _ssm_client(aws_profile, region)

    print(f"\nReading CloudFormation outputs ({region})...")
    outputs_a = _stack_outputs(cfn, stack_a)
    outputs_b = _stack_outputs(cfn, stack_b)

    data_bucket = _require_output(outputs_a, "DataBucketName", stack_name=stack_a)
    cognito_user_pool_id = _require_output(outputs_a, "UserPoolId", stack_name=stack_a)
    cognito_app_client_id = _require_output(outputs_a, "AppClientId", stack_name=stack_a)
    cognito_domain = _require_output(outputs_a, "CognitoDomain", stack_name=stack_a)
    tenant_role_arn = _require_output(outputs_a, "TenantRoleArn", stack_name=stack_a)
    backend_ecr_repo_name = _require_output(outputs_a, "BackendEcrRepoName", stack_name=stack_a)
    codedeploy_app_name = _require_output(outputs_a, "CodeDeployAppName", stack_name=stack_a)
    amplify_app_id = _require_output(outputs_a, "AmplifyAppId", stack_name=stack_a)
    amplify_default_domain = _require_output(outputs_a, "AmplifyDefaultDomain", stack_name=stack_a)
    if not from_email:
        from_email = outputs_a.get("FromEmail", "").strip()

    compute_outputs = {
        k: v
        for k, v in outputs_b.items()
        if k.startswith("Handlers") or k in {"HandlersEcrRepoUri", "HandlersEcrRepoName"}
    }
    extension_vars = _extension_vars(env_name, outputs_b)
    ecs_network = build_ecs_network_vars(compute_type=compute_type, network_mode_cfg=None)

    print("\nWriting SSM parameters...")
    stages = ["production"]
    if enable_staging:
        stages.append("staging")

    for stage in stages:
        if stage == "staging" and not outputs_b.get("BackendLambdaFunctionNameStaging"):
            print(f"  skip platform-vars/{stage} (staging outputs missing on {stack_b})")
            continue
        amplify_key = _AMPLIFY_CONSOLE_URL_KEYS[stage]
        amplify_console_url = outputs_a.get(amplify_key, "").strip()
        if not amplify_console_url and stage == "production":
            amplify_console_url = _require_output(outputs_a, amplify_key, stack_name=stack_a)

        stage_app = _stage_app(outputs_b, stage, env_name)
        vars_dict = build_launcher_vars(
            stage=stage,
            env_name=env_name,
            aws_region=region,
            aws_account=account,
            data_bucket=data_bucket,
            cognito_user_pool_id=cognito_user_pool_id,
            cognito_app_client_id=cognito_app_client_id,
            cognito_domain=cognito_domain,
            tenant_role_arn=tenant_role_arn,
            backend_ecr_repo_name=backend_ecr_repo_name,
            codedeploy_app_name=codedeploy_app_name,
            amplify_app_id=amplify_app_id,
            amplify_default_domain=amplify_default_domain,
            amplify_console_url=amplify_console_url,
            stage_app=stage_app,
            compute_outputs=compute_outputs,
            ecs_network=ecs_network,
            extension_vars=extension_vars,
            from_email=from_email,
        )
        vars_dict["LAMBDA_FUNCTION_NAME"] = stage_app["fn_name"]
        vars_dict["LAMBDA_ALIAS"] = stage

        envelope = build_platform_vars_envelope(
            github_repo=github_repo,
            stage=stage,
            vars_dict=vars_dict,
        )
        _put_parameter(ssm, ssm_platform_vars_path(env_name, stage), envelope, dry_run=dry_run)

    production_app = _stage_app(outputs_b, "production", env_name)
    deploy_vars = build_deploy_input_vars(
        env_name=env_name,
        aws_region=region,
        aws_account=account,
        data_bucket=data_bucket,
        cognito_user_pool_id=cognito_user_pool_id,
        cognito_app_client_id=cognito_app_client_id,
        tenant_role_arn=tenant_role_arn,
        production_app=production_app,
        compute_outputs=compute_outputs,
        ecs_network=ecs_network,
        extension_vars=extension_vars,
    )
    deploy_envelope = build_deploy_input_envelope(
        github_handlers_repo=github_handlers_repo,
        vars_dict=deploy_vars,
    )
    _put_parameter(ssm, ssm_deploy_input_path(env_name), deploy_envelope, dry_run=dry_run)

    if compute_type == "ec2":
        vpc = outputs_b.get(_ECS_NETWORK_OUTPUT_KEYS["vpc"], "").strip()
        subnets = outputs_b.get(_ECS_NETWORK_OUTPUT_KEYS["subnets"], "").strip()
        sg = outputs_b.get(_ECS_NETWORK_OUTPUT_KEYS["security_group"], "").strip()
        if vpc:
            _put_plain_parameter(ssm, ssm_ecs_vpc_path(env_name), vpc, dry_run=dry_run)
        if subnets:
            _put_plain_parameter(ssm, ssm_ecs_subnets_path(env_name), subnets, dry_run=dry_run)
        if sg:
            _put_plain_parameter(ssm, ssm_ecs_security_groups_path(env_name), sg, dry_run=dry_run)

    print("\nBootstrap SSM config written.")
    print(f"  {ssm_platform_vars_path(env_name, 'production')}")
    if enable_staging and outputs_b.get("BackendLambdaFunctionNameStaging"):
        print(f"  {ssm_platform_vars_path(env_name, 'staging')}")
    print(f"  {ssm_deploy_input_path(env_name)}")
    if compute_type == "ec2":
        print(f"  {ssm_ecs_vpc_path(env_name)}")
        print(f"  {ssm_ecs_subnets_path(env_name)}")
        print(f"  {ssm_ecs_security_groups_path(env_name)}")
