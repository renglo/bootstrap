"""Post-deploy state writer.

Reads CloudFormation stack outputs, uploads DynamoDB blueprints,
and writes state JSON files to s3://{data-bucket}/params/.

Run after CloudFormation completes:

    python bootstrap/write_state.py \\
        --env-name myenv \\
        --aws-profile my-profile \\
        --aws-region us-east-1 \\
        [--compute-type fargate|ec2|lambda_only]

The data bucket is discovered from the {env_name}-storage stack output.
All state files match the structure previously written to bootstrap/state/<env>/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_BLUEPRINTS_DIR = _WORKSPACE_ROOT / "launcher" / "scripts" / "blueprints"
_CUSTOMER_CONFIG_PATH = _WORKSPACE_ROOT / "launcher" / "cdk" / "customer-config.json"

_REST_API_ID_RE = re.compile(r"https://([a-z0-9]+)\.execute-api\.")

_CODEDEPLOY_CONFIG = {
    "production": "CodeDeployDefault.LambdaCanary10Percent10Minutes",
    "staging": "CodeDeployDefault.LambdaAllAtOnce",
}

_PRESERVED_SECRET_KEYS = ("OPENAI_API_KEY",)


def _ts() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _session(profile: str | None, region: str) -> boto3.Session:
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def _load_customer_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or _CUSTOMER_CONFIG_PATH
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _find_extension_infra_dirs(explicit: Path | None) -> list[Path]:
    if explicit is not None:
        return [explicit] if explicit.is_dir() else []
    dirs: list[Path] = []
    for child in sorted(_WORKSPACE_ROOT.iterdir()):
        infra = child / "installer" / "infra"
        if infra.is_dir():
            dirs.append(infra)
    return dirs


def _load_extension_payload(
    env_name: str,
    extension_infra_dir: Path | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Merge extension vars/secrets from */installer/infra/*.json."""
    vars_out: dict[str, str] = {}
    secrets_out: dict[str, str] = {}

    for infra_dir in _find_extension_infra_dirs(extension_infra_dir):
        ext_cfg = infra_dir / "extension_config.json"
        if ext_cfg.is_file():
            data = json.loads(ext_cfg.read_text(encoding="utf-8"))
            for key, value in data.items():
                if key == "SECRETS" and isinstance(value, dict):
                    secrets_out.update({k: str(v) for k, v in value.items()})
                elif not key.startswith("_") and isinstance(value, str):
                    vars_out[key] = value

        extra = infra_dir / "extra_resources.json"
        if extra.is_file():
            data = json.loads(extra.read_text(encoding="utf-8"))
            file_env = str(data.get("env_name", "")).strip()
            if file_env and file_env != env_name:
                continue
            for key, value in data.get("vars", {}).items():
                vars_out[key] = str(value)
            for key, value in data.get("secrets", {}).items():
                secrets_out[key] = str(value)

    return vars_out, secrets_out


def _get_stack_outputs(cf_client, stack_name: str) -> dict[str, str]:
    try:
        resp = cf_client.describe_stacks(StackName=stack_name)
        outputs = resp["Stacks"][0].get("Outputs", [])
        return {o["OutputKey"]: o["OutputValue"] for o in outputs}
    except cf_client.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ValidationError", "StackNotFoundException"):
            print(f"  [warn] Stack {stack_name!r} not found — skipping")
            return {}
        raise


def _get_stack_parameters(cf_client, stack_name: str) -> dict[str, str]:
    try:
        resp = cf_client.describe_stacks(StackName=stack_name)
        params = resp["Stacks"][0].get("Parameters", [])
        return {p["ParameterKey"]: p["ParameterValue"] for p in params}
    except cf_client.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ValidationError", "StackNotFoundException"):
            return {}
        raise


def _find_handlers_security_group(cf_client, stack_name: str) -> str:
    try:
        paginator = cf_client.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=stack_name):
            for resource in page.get("StackResourceSummaries", []):
                if resource.get("ResourceType") != "AWS::EC2::SecurityGroup":
                    continue
                logical_id = resource.get("LogicalResourceId", "")
                if "HandlersAsgInstanceSecurityGroup" in logical_id:
                    return resource.get("PhysicalResourceId", "")
    except cf_client.exceptions.ClientError:
        pass
    return ""


def _discover_default_vpc_networking(ec2_client) -> tuple[str, str]:
    vpcs = ec2_client.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpcs_list = vpcs.get("Vpcs", [])
    if not vpcs_list:
        return "", ""

    vpc_id = vpcs_list[0]["VpcId"]
    subnets_resp = ec2_client.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    subnets = ",".join(s["SubnetId"] for s in subnets_resp.get("Subnets", []))

    sgs_resp = ec2_client.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    sg_ids = [
        sg["GroupId"]
        for sg in sgs_resp.get("SecurityGroups", [])
        if sg.get("GroupName") != "default"
    ]
    if not sg_ids:
        default_sg = next(
            (sg["GroupId"] for sg in sgs_resp.get("SecurityGroups", []) if sg.get("GroupName") == "default"),
            "",
        )
        if default_sg:
            sg_ids = [default_sg]
    return subnets, ",".join(sg_ids)


def _resolve_ecs_networking(
    cf_client,
    ec2_client,
    env_name: str,
    compute_type: str,
    network_mode_cfg: str | None,
) -> dict[str, str]:
    if compute_type == "lambda_only":
        return {
            "ECS_LAUNCH_TYPE": "",
            "ECS_NETWORK_MODE": "",
            "ECS_SUBNETS": "",
            "ECS_SECURITY_GROUPS": "",
        }

    launch_type = compute_type
    network_mode = "awsvpc" if compute_type == "fargate" else (network_mode_cfg or "bridge").strip() or "bridge"

    subnets = ""
    security_groups = ""
    if compute_type == "ec2":
        params = _get_stack_parameters(cf_client, f"{env_name}-compute")
        subnets = params.get("SubnetIds", "")
        security_groups = _find_handlers_security_group(cf_client, f"{env_name}-compute")

    if not subnets or (compute_type == "fargate" and not security_groups):
        default_subnets, default_sgs = _discover_default_vpc_networking(ec2_client)
        if not subnets:
            subnets = default_subnets
        if not security_groups:
            security_groups = default_sgs

    return {
        "ECS_LAUNCH_TYPE": launch_type,
        "ECS_NETWORK_MODE": network_mode,
        "ECS_SUBNETS": subnets,
        "ECS_SECURITY_GROUPS": security_groups,
    }


def _parse_rest_api_id(rest_url: str) -> str:
    match = _REST_API_ID_RE.search(rest_url or "")
    return match.group(1) if match else ""


def _normalize_url(url: str) -> str:
    return (url or "").rstrip("/")


def _lambda_arn(region: str, account: str, function_name: str) -> str:
    return f"arn:aws:lambda:{region}:{account}:function:{function_name}"


def _api_gateway_arn(region: str, account: str, api_id: str) -> str:
    return f"arn:aws:execute-api:{region}:{account}:{api_id}/*"


def _dynamodb_vars(env_name: str) -> dict[str, str]:
    return {
        "DYNAMODB_ENTITY_TABLE": f"{env_name}_entities",
        "DYNAMODB_BLUEPRINT_TABLE": f"{env_name}_blueprints",
        "DYNAMODB_RINGDATA_TABLE": f"{env_name}_data",
        "DYNAMODB_REL_TABLE": f"{env_name}_rel",
        "DYNAMODB_CHAT_TABLE": f"{env_name}_chat",
        "DYNAMODB_SESSION_TABLE": f"{env_name}_session",
        "DYNAMODB_SEARCH_TABLE": f"{env_name}_search",
    }


def _try_read_s3_json(s3_client, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(resp["Body"].read().decode("utf-8"))
    except ClientError:
        return None


def _preserve_secrets(
    s3_client,
    bucket: str,
    *,
    extension_secrets: dict[str, str],
) -> dict[str, str]:
    preserved: dict[str, str] = {}
    for key in _PRESERVED_SECRET_KEYS:
        if extension_secrets.get(key):
            preserved[key] = extension_secrets[key]

    for s3_key in ("params/platform_vars.production.json", "params/deploy_input.json"):
        existing = _try_read_s3_json(s3_client, bucket, s3_key)
        if not existing:
            continue
        for secret_key in _PRESERVED_SECRET_KEYS:
            value = (existing.get("SECRETS") or {}).get(secret_key, "")
            if value and secret_key not in preserved:
                preserved[secret_key] = str(value)
    return preserved


def _upload_blueprints(session: boto3.Session, env_name: str) -> dict[str, list[str]]:
    table_name = f"{env_name}_blueprints"
    dynamodb = session.resource("dynamodb")
    table = dynamodb.Table(table_name)
    results: dict[str, list[str]] = {"success": [], "failed": []}

    if not _BLUEPRINTS_DIR.is_dir():
        print(f"  [warn] Blueprints directory not found at {_BLUEPRINTS_DIR} — skipping")
        return results

    for json_file in sorted(_BLUEPRINTS_DIR.glob("*.json")):
        try:
            blueprint = json.loads(json_file.read_text(encoding="utf-8"))
            if "irn" not in blueprint:
                blueprint["irn"] = json_file.stem
            if "version" not in blueprint:
                blueprint["version"] = "latest"
            table.put_item(Item=blueprint)
            key = f"{blueprint['irn']}@{blueprint['version']}"
            print(f"  [blueprint] uploaded {key}")
            results["success"].append(key)
        except Exception as exc:
            irn = json_file.stem
            print(f"  [blueprint] failed {irn}: {exc}")
            results["failed"].append(f"{irn}: {exc}")

    return results


def _put_s3(s3_client, bucket: str, key: str, body: str, *, content_type: str) -> None:
    s3_client.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"), ContentType=content_type)
    print(f"  [s3] s3://{bucket}/{key}")


def _merge_vars(*parts: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for part in parts:
        merged.update({k: str(v) for k, v in part.items() if v is not None and str(v) != ""})
    return merged


def _build_launcher_vars(
    *,
    stage: str,
    env_name: str,
    aws_region: str,
    aws_account: str,
    data_bucket: str,
    cognito: dict[str, str],
    runtime: dict[str, str],
    api: dict[str, str],
    compute: dict[str, str],
    ecs_network: dict[str, str],
    extension_vars: dict[str, str],
) -> dict[str, str]:
    is_prod = stage == "production"
    fn_key = "BackendLambdaFunctionNameProduction" if is_prod else "BackendLambdaFunctionNameStaging"
    rest_key = "RestApiUrlProduction" if is_prod else "RestApiUrlStaging"
    ws_conn_key = "WebSocketConnectionsUrlProduction" if is_prod else "WebSocketConnectionsUrlStaging"
    ws_url_key = "WebSocketUrlProduction" if is_prod else "WebSocketUrlStaging"

    backend_fn = runtime.get(fn_key, f"{env_name}-backend-{stage}")
    rest_url = _normalize_url(api.get(rest_key, ""))
    ws_connections = _normalize_url(api.get(ws_conn_key, ""))
    ws_url = _normalize_url(api.get(ws_url_key, ""))
    api_id = _parse_rest_api_id(rest_url)
    handlers_fn = compute.get("HandlersLambdaFunctionName", f"{env_name}-handlers")
    codedeploy_app = runtime.get("CodeDeployAppName", f"{env_name}-backend-codedeploy")

    base: dict[str, str] = {
        "WL_NAME": env_name,
        "BASE_URL": rest_url,
        "API_GATEWAY_ARN": _api_gateway_arn(aws_region, aws_account, api_id) if api_id else "",
        "LAMBDA_BACKEND_ARN": _lambda_arn(aws_region, aws_account, backend_fn),
        "LAMBDA_EXTERNAL_HANDLERS_ARN": _lambda_arn(aws_region, aws_account, handlers_fn),
        "ROLE_ARN": runtime.get("TenantRoleArn", ""),
        **_dynamodb_vars(env_name),
        "COGNITO_REGION": aws_region,
        "COGNITO_USERPOOL_ID": cognito.get("user_pool_id", ""),
        "COGNITO_APP_CLIENT_ID": cognito.get("app_client_id", ""),
        "COGNITO_CHECK_TOKEN_EXPIRATION": "True",
        "PREVIEW_LAYER": "2",
        "S3_BUCKET_NAME": data_bucket,
        "ALLOW_DEV_ORIGINS": "true",
        "CODEDEPLOY_APPLICATION_NAME": codedeploy_app,
        "CODEDEPLOY_DEPLOYMENT_GROUP_NAME": f"{env_name}-backend-{stage}",
        "CODEDEPLOY_DEPLOYMENT_CONFIG_NAME": _CODEDEPLOY_CONFIG[stage],
        "AWS_REGION": aws_region,
        "AWS_DEFAULT_REGION": aws_region,
        "AWS_ECR_REPOSITORY": runtime.get("BackendEcrRepoName", f"{env_name}_backend"),
        "WEBSOCKET_CONNECTIONS": ws_connections,
        "WEBSOCKET_URL": ws_url,
        "VITE_WEBSOCKET_URL": ws_url,
        "ECS_CLUSTER": compute.get("HandlersEcsClusterName", ""),
        "ECS_TASK_DEFINITION": compute.get("HandlersTaskFamily", ""),
        "ECS_RESULTS_BUCKET": compute.get("HandlersResultsBucketName", ""),
        "EXTERNAL_HANDLERS": env_name,
        **ecs_network,
    }
    return _merge_vars(base, extension_vars)


def _build_platform_vars_envelope(
    *,
    github_repo: str,
    stage: str,
    vars_dict: dict[str, str],
    secrets: dict[str, str],
) -> dict[str, Any]:
    return {
        "GITHUB_REPOSITORY": github_repo,
        "ENVIRONMENT": stage,
        "VARS": vars_dict,
        "SECRETS": secrets,
    }


def _build_deploy_input(
    *,
    github_handlers_repo: str,
    env_name: str,
    aws_region: str,
    aws_account: str,
    data_bucket: str,
    cognito: dict[str, str],
    runtime: dict[str, str],
    api: dict[str, str],
    compute: dict[str, str],
    ecs_network: dict[str, str],
    extension_vars: dict[str, str],
    secrets: dict[str, str],
) -> dict[str, Any]:
    handlers_fn = compute.get("HandlersLambdaFunctionName", f"{env_name}-handlers")
    handlers_ecr_uri = compute.get("HandlersEcrRepoUri", "")
    ws_connections = _normalize_url(api.get("WebSocketConnectionsUrlProduction", ""))
    ws_url = _normalize_url(api.get("WebSocketUrlProduction", ""))

    vars_dict = _merge_vars(
        {
            "WL_NAME": env_name,
            "AWS_REGION": aws_region,
            "AWS_DEFAULT_REGION": aws_region,
            "ECS_CLUSTER": compute.get("HandlersEcsClusterName", ""),
            "ECS_TASK_DEFINITION": compute.get("HandlersTaskFamily", ""),
            "ECS_RESULTS_BUCKET": compute.get("HandlersResultsBucketName", ""),
            "LAMBDA_EXTERNAL_HANDLERS_ARN": _lambda_arn(aws_region, aws_account, handlers_fn),
            **_dynamodb_vars(env_name),
            "COGNITO_REGION": aws_region,
            "COGNITO_USERPOOL_ID": cognito.get("user_pool_id", ""),
            "COGNITO_APP_CLIENT_ID": cognito.get("app_client_id", ""),
            "COGNITO_CHECK_TOKEN_EXPIRATION": "True",
            "S3_BUCKET_NAME": data_bucket,
            "ROLE_ARN": runtime.get("TenantRoleArn", ""),
            "WEBSOCKET_CONNECTIONS": ws_connections,
            "WEBSOCKET_URL": ws_url,
            "LAMBDA_HANDLERS_FUNCTION_NAME": handlers_fn,
            "ECR_IMAGE_URI": f"{handlers_ecr_uri}:latest" if handlers_ecr_uri else "",
            "EXTERNAL_HANDLERS": env_name,
            **ecs_network,
        },
        extension_vars,
    )

    deploy_secrets = dict(secrets)
    handlers_oidc = compute.get("HandlersOidcDeployRoleArnProduction", "")
    if handlers_oidc:
        deploy_secrets["AWS_GITHUB_OIDC_ROLE_ARN"] = handlers_oidc

    return _build_platform_vars_envelope(
        github_repo=github_handlers_repo,
        stage="production",
        vars_dict=vars_dict,
        secrets=deploy_secrets,
    )


def _build_env_config_py(
    *,
    env_name: str,
    aws_region: str,
    cognito: dict[str, str],
    s3_bucket_name: str,
    api: dict[str, str],
    compute: dict[str, str],
    ecs_network: dict[str, str],
    handlers_fn: str,
    aws_account: str,
    extension_vars: dict[str, str],
) -> str:
    prod_ws_conn = _normalize_url(api.get("WebSocketConnectionsUrlProduction", ""))
    prod_ws_url = _normalize_url(api.get("WebSocketUrlProduction", ""))
    staging_ws_conn = _normalize_url(api.get("WebSocketConnectionsUrlStaging", ""))
    staging_ws_url = _normalize_url(api.get("WebSocketUrlStaging", ""))
    handlers_arn = _lambda_arn(aws_region, aws_account, handlers_fn)

    lines = [
        '"""Auto-generated by bootstrap/write_state.py — do not edit manually."""',
        f"# Generated at: {_ts()}",
        "",
    ]
    for key, value in _dynamodb_vars(env_name).items():
        lines.append(f"{key} = {value!r}")

    lines.extend(
        [
            "",
            f"COGNITO_REGION = {aws_region!r}",
            f"COGNITO_USERPOOL_ID = {cognito.get('user_pool_id', '')!r}",
            f"COGNITO_APP_CLIENT_ID = {cognito.get('app_client_id', '')!r}",
            "",
            f"S3_BUCKET_NAME = {s3_bucket_name!r}",
            "",
            f"WEBSOCKET_CONNECTIONS = {prod_ws_conn!r}",
            f"WEBSOCKET_URL = {prod_ws_url!r}",
            f"VITE_WEBSOCKET_URL = {prod_ws_url!r}",
            f"WEBSOCKET_CONNECTIONS_STAGING = {staging_ws_conn!r}",
            f"WEBSOCKET_URL_STAGING = {staging_ws_url!r}",
            f"VITE_WEBSOCKET_URL_STAGING = {staging_ws_url!r}",
            "",
            "# ECS extensions-service configuration",
            f"ECS_CLUSTER = {compute.get('HandlersEcsClusterName', '')!r}",
            f"ECS_TASK_DEFINITION = {compute.get('HandlersTaskFamily', '')!r}",
            f"ECS_RESULTS_BUCKET = {compute.get('HandlersResultsBucketName', '')!r}",
            f"ECS_LAUNCH_TYPE = {ecs_network.get('ECS_LAUNCH_TYPE', '')!r}",
            f"ECS_NETWORK_MODE = {ecs_network.get('ECS_NETWORK_MODE', '')!r}",
            f"ECS_SUBNETS = {ecs_network.get('ECS_SUBNETS', '')!r}",
            f"ECS_SECURITY_GROUPS = {ecs_network.get('ECS_SECURITY_GROUPS', '')!r}",
            f"LAMBDA_EXTERNAL_HANDLERS_ARN = {handlers_arn!r}",
        ]
    )

    skip = set(_dynamodb_vars(env_name)) | {
        "EXTERNAL_HANDLERS",
        "EXTERNAL_HANDLERS_ECS_HANDLERS",
        "ARBITIUM_THREAT_EVENTS_BUCKET",
    }
    for key, value in sorted(extension_vars.items()):
        if key in skip:
            continue
        lines.append(f"{key} = {value!r}")

    for key in ("EXTERNAL_HANDLERS", "EXTERNAL_HANDLERS_ECS_HANDLERS", "ARBITIUM_THREAT_EVENTS_BUCKET"):
        if key in extension_vars:
            lines.extend(["", f"{key} = {extension_vars[key]!r}"])

    return "\n".join(lines) + "\n"


def write_state(
    env_name: str,
    aws_profile: str | None,
    aws_region: str,
    compute_type: str | None = None,
    *,
    customer_config: dict[str, Any] | None = None,
    extension_infra_dir: Path | None = None,
) -> dict[str, Any]:
    cfg = customer_config if customer_config is not None else _load_customer_config()
    if cfg.get("env_name", env_name) == env_name:
        aws_region = str(cfg.get("aws_region", aws_region)).strip() or aws_region
    if not compute_type:
        compute_type = str(cfg.get("compute_type", "fargate")).strip() or "fargate"

    github_repo = str(cfg.get("github_repo", "")).strip()
    github_handlers_repo = str(cfg.get("github_handlers_repo", github_repo)).strip() or github_repo
    network_mode_cfg = str(cfg.get("network_mode", "")).strip() or None
    aws_account = str(cfg.get("aws_account", "")).strip()

    sess = _session(aws_profile, aws_region)
    if not aws_account:
        aws_account = sess.client("sts").get_caller_identity()["Account"]

    cf = sess.client("cloudformation")
    ec2 = sess.client("ec2")
    s3c = sess.client("s3")

    print(f"\nReading CloudFormation stack outputs for env: {env_name}")
    auth = _get_stack_outputs(cf, f"{env_name}-auth")
    storage = _get_stack_outputs(cf, f"{env_name}-storage")
    runtime = _get_stack_outputs(cf, f"{env_name}-runtime")
    api = _get_stack_outputs(cf, f"{env_name}-api")
    compute: dict[str, str] = {}
    if compute_type != "lambda_only":
        compute = _get_stack_outputs(cf, f"{env_name}-compute")

    data_bucket = storage.get("DataBucketName", "")
    if not data_bucket:
        raise RuntimeError(
            f"Could not resolve data bucket name from {env_name}-storage stack output 'DataBucketName'. "
            "Make sure CloudFormation has completed successfully."
        )

    extension_vars, extension_secrets = _load_extension_payload(env_name, extension_infra_dir)
    preserved_secrets = _preserve_secrets(s3c, data_bucket, extension_secrets=extension_secrets)
    ecs_network = _resolve_ecs_networking(cf, ec2, env_name, compute_type, network_mode_cfg)

    print("\nUploading DynamoDB blueprints...")
    blueprint_results = _upload_blueprints(sess, env_name)

    cognito_out = {
        "user_pool_id": auth.get("UserPoolId", ""),
        "user_pool_arn": auth.get("UserPoolArn", ""),
        "app_client_id": auth.get("AppClientId", ""),
    }
    handlers_fn = compute.get("HandlersLambdaFunctionName", f"{env_name}-handlers")

    platform_resources: dict[str, Any] = {
        "updated_at": _ts(),
        "env_name": env_name,
        "aws_region": aws_region,
        "aws_account": aws_account,
        "compute_type": compute_type,
        "github_repo": github_repo,
        "github_handlers_repo": github_handlers_repo,
        "cognito": cognito_out,
        "s3": {"bucket_name": data_bucket, "bucket_arn": storage.get("DataBucketArn", "")},
        "dynamodb": {
            "env_prefix": env_name,
            "tables": list(_dynamodb_vars(env_name).values()) + [f"{env_name}_graph"],
        },
        "iam": {
            "tenant_policy_arn": runtime.get("TenantPolicyArn", ""),
            "tenant_role_arn": runtime.get("TenantRoleArn", ""),
        },
        "backend": {
            "ecr_repo_name": runtime.get("BackendEcrRepoName", ""),
            "ecr_repo_uri": runtime.get("BackendEcrRepoUri", ""),
            "production": {
                "lambda_function_name": runtime.get(
                    "BackendLambdaFunctionNameProduction", f"{env_name}-backend-production"
                ),
                "lambda_alias_arn": runtime.get("BackendLambdaAliasArnProduction", ""),
                "lambda_execution_role_arn": runtime.get(
                    "BackendLambdaExecutionRoleArn", runtime.get("TenantRoleArn", "")
                ),
                "lambda_log_group": runtime.get("BackendLambdaLogGroupNameProduction", ""),
                "rest_api_url": api.get("RestApiUrlProduction", ""),
                "websocket_url": api.get("WebSocketUrlProduction", ""),
                "websocket_connections_url": api.get("WebSocketConnectionsUrlProduction", ""),
                "codedeploy_app": runtime.get("CodeDeployAppName", ""),
                "codedeploy_group": f"{env_name}-backend-production",
            },
            "staging": {
                "lambda_function_name": runtime.get(
                    "BackendLambdaFunctionNameStaging", f"{env_name}-backend-staging"
                ),
                "lambda_alias_arn": runtime.get("BackendLambdaAliasArnStaging", ""),
                "lambda_execution_role_arn": runtime.get(
                    "BackendLambdaExecutionRoleArn", runtime.get("TenantRoleArn", "")
                ),
                "lambda_log_group": runtime.get("BackendLambdaLogGroupNameStaging", ""),
                "rest_api_url": api.get("RestApiUrlStaging", ""),
                "websocket_url": api.get("WebSocketUrlStaging", ""),
                "websocket_connections_url": api.get("WebSocketConnectionsUrlStaging", ""),
                "codedeploy_group": f"{env_name}-backend-staging",
            },
        },
        "oidc": {
            "provider_arn": runtime.get("OidcProviderArn", ""),
            "deploy_role_arn_production": runtime.get("OidcDeployRoleArnProduction", ""),
            "deploy_role_arn_staging": runtime.get("OidcDeployRoleArnStaging", ""),
            "handlers_deploy_role_arn_production": compute.get("HandlersOidcDeployRoleArnProduction", ""),
            "handlers_deploy_role_arn_staging": compute.get("HandlersOidcDeployRoleArnStaging", ""),
        },
        "handlers": {
            "lambda_function_name": handlers_fn,
            "ecr_repo_name": compute.get("HandlersEcrRepoName", ""),
            "ecr_repo_uri": compute.get("HandlersEcrRepoUri", ""),
            "ecs_cluster": compute.get("HandlersEcsClusterName", ""),
            "ecs_task_family": compute.get("HandlersTaskFamily", ""),
            "results_bucket": compute.get("HandlersResultsBucketName", ""),
            "execution_role_arn": compute.get("HandlersExecutionRoleArn", ""),
            "task_role_arn": compute.get("HandlersTaskRoleArn", ""),
            "lambda_role_arn": compute.get("HandlersLambdaRoleArn", ""),
            "lambda_log_group": compute.get("HandlersLambdaLogGroupName", ""),
            **ecs_network,
        },
        "extension_vars": extension_vars,
        "blueprints_upload": blueprint_results,
    }

    shared_ctx = {
        "env_name": env_name,
        "aws_region": aws_region,
        "aws_account": aws_account,
        "data_bucket": data_bucket,
        "cognito": cognito_out,
        "runtime": runtime,
        "api": api,
        "compute": compute,
        "ecs_network": ecs_network,
        "extension_vars": extension_vars,
    }

    prod_vars = _build_launcher_vars(stage="production", **shared_ctx)
    staging_vars = _build_launcher_vars(stage="staging", **shared_ctx)

    prod_secrets = dict(preserved_secrets)
    prod_oidc = runtime.get("OidcDeployRoleArnProduction", "")
    if prod_oidc:
        prod_secrets["AWS_GITHUB_OIDC_ROLE_ARN"] = prod_oidc

    staging_secrets = dict(preserved_secrets)
    staging_oidc = runtime.get("OidcDeployRoleArnStaging", "")
    if staging_oidc:
        staging_secrets["AWS_GITHUB_OIDC_ROLE_ARN"] = staging_oidc

    platform_vars_production = _build_platform_vars_envelope(
        github_repo=github_repo, stage="production", vars_dict=prod_vars, secrets=prod_secrets
    )
    platform_vars_staging = _build_platform_vars_envelope(
        github_repo=github_repo, stage="staging", vars_dict=staging_vars, secrets=staging_secrets
    )
    deploy_input = _build_deploy_input(
        github_handlers_repo=github_handlers_repo,
        cognito=cognito_out,
        runtime=runtime,
        api=api,
        compute=compute,
        ecs_network=ecs_network,
        extension_vars=extension_vars,
        secrets=preserved_secrets,
        env_name=env_name,
        aws_region=aws_region,
        aws_account=aws_account,
        data_bucket=data_bucket,
    )
    env_config_py = _build_env_config_py(
        env_name=env_name,
        aws_region=aws_region,
        cognito=cognito_out,
        s3_bucket_name=data_bucket,
        api=api,
        compute=compute,
        ecs_network=ecs_network,
        handlers_fn=handlers_fn,
        aws_account=aws_account,
        extension_vars=extension_vars,
    )

    print(f"\nWriting state to s3://{data_bucket}/params/")
    _put_s3(s3c, data_bucket, "params/platform_resources.json", json.dumps(platform_resources, indent=2), content_type="application/json")
    _put_s3(s3c, data_bucket, "params/platform_vars.production.json", json.dumps(platform_vars_production, indent=2), content_type="application/json")
    _put_s3(s3c, data_bucket, "params/platform_vars.staging.json", json.dumps(platform_vars_staging, indent=2), content_type="application/json")
    _put_s3(s3c, data_bucket, "params/deploy_input.json", json.dumps(deploy_input, indent=2), content_type="application/json")
    _put_s3(s3c, data_bucket, "params/env_config.py", env_config_py, content_type="text/x-python")

    print(f"\nState written successfully to s3://{data_bucket}/params/")
    print(f"  platform_vars.production VARS: {len(prod_vars)} keys")
    print(f"  deploy_input VARS: {len(deploy_input.get('VARS', {}))} keys")
    return platform_resources


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Post-deploy state writer: reads CloudFormation outputs, uploads blueprints, "
            "and writes state JSON files to S3."
        )
    )
    parser.add_argument("--env-name", required=True, help="Environment name (e.g. myenv)")
    parser.add_argument("--aws-profile", default=None, help="AWS named profile")
    parser.add_argument("--aws-region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--compute-type",
        default=None,
        choices=["lambda_only", "fargate", "ec2"],
        help="Compute type (default: from customer-config.json or fargate)",
    )
    parser.add_argument(
        "--customer-config",
        default=None,
        help="Path to customer-config.json (default: launcher/cdk/customer-config.json)",
    )
    parser.add_argument(
        "--extension-infra-dir",
        default=None,
        help="Path to extension installer/infra dir (default: auto-discover */installer/infra/)",
    )
    args = parser.parse_args()

    config_path = Path(args.customer_config) if args.customer_config else None
    customer_config = _load_customer_config(config_path)
    compute_type = args.compute_type or customer_config.get("compute_type")
    extension_dir = Path(args.extension_infra_dir) if args.extension_infra_dir else None

    try:
        write_state(
            env_name=args.env_name,
            aws_profile=args.aws_profile,
            aws_region=args.aws_region,
            compute_type=str(compute_type) if compute_type else None,
            customer_config=customer_config,
            extension_infra_dir=extension_dir,
        )
        print("\nDone.")
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
