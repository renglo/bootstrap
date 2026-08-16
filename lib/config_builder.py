"""Bootstrap config JSON builders for SSM parameters.

Shared between CDK synth (Fn.to_json_string) and any offline tooling.
Values may be plain strings or CDK/CloudFormation tokens.
"""

from __future__ import annotations

import re
from typing import Any

MapValue = Any

_REST_API_ID_RE = re.compile(r"https://([a-z0-9]+)\.execute-api\.")

CODEDEPLOY_CONFIG: dict[str, str] = {
    "production": "CodeDeployDefault.LambdaCanary10Percent10Minutes",
    "staging": "CodeDeployDefault.LambdaAllAtOnce",
}

EMPTY_SECRETS: dict[str, str] = {}


def ssm_platform_vars_path(env_name: str, stage: str) -> str:
    return f"/{env_name}/bootstrap/platform-vars/{stage}"


def ssm_deploy_input_path(env_name: str) -> str:
    return f"/{env_name}/bootstrap/deploy-input"


def ssm_ecs_vpc_path(env_name: str) -> str:
    return f"/{env_name}/bootstrap/ecs-vpc"


def ssm_ecs_subnets_path(env_name: str) -> str:
    return f"/{env_name}/bootstrap/ecs-subnets"


def ssm_ecs_security_groups_path(env_name: str) -> str:
    return f"/{env_name}/bootstrap/ecs-security-groups"


ECS_NETWORK_SSM_PATHS: dict[str, str] = {
    "ECS_VPC": "ecs-vpc",
    "ECS_SUBNETS": "ecs-subnets",
    "ECS_SECURITY_GROUPS": "ecs-security-groups",
}


def parse_rest_api_id(rest_url: str) -> str:
    match = _REST_API_ID_RE.search(rest_url or "")
    return match.group(1) if match else ""


def normalize_url(url: MapValue) -> MapValue:
    if not isinstance(url, str):
        return url
    return url.rstrip("/")


def lambda_arn(region: str, account: str, function_name: MapValue) -> str:
    return f"arn:aws:lambda:{region}:{account}:function:{function_name}"


def dynamodb_vars(env_name: str) -> dict[str, str]:
    return {
        "DYNAMODB_ENTITY_TABLE": f"{env_name}_entities",
        "DYNAMODB_BLUEPRINT_TABLE": f"{env_name}_blueprints",
        "DYNAMODB_RINGDATA_TABLE": f"{env_name}_data",
        "DYNAMODB_REL_TABLE": f"{env_name}_rel",
        "DYNAMODB_CHAT_TABLE": f"{env_name}_chat",
        "DYNAMODB_SESSION_TABLE": f"{env_name}_session",
        "DYNAMODB_SEARCH_TABLE": f"{env_name}_search",
        "DYNAMODB_GRAPH_TABLE": f"{env_name}_graph",
    }


def merge_vars(*parts: dict[str, MapValue]) -> dict[str, MapValue]:
    merged: dict[str, MapValue] = {}
    for part in parts:
        merged.update({k: v for k, v in part.items() if v is not None and str(v) != ""})
    return merged


def build_ecs_network_vars(
    *,
    compute_type: str,
    network_mode_cfg: str | None,
) -> dict[str, MapValue]:
    if compute_type == "lambda_only":
        return {
            "ECS_LAUNCH_TYPE": "",
            "ECS_NETWORK_MODE": "",
        }

    launch_type = compute_type
    network_mode = (
        "awsvpc" if compute_type == "fargate" else (network_mode_cfg or "bridge").strip() or "bridge"
    )
    return {
        "ECS_LAUNCH_TYPE": launch_type,
        "ECS_NETWORK_MODE": network_mode,
    }


def build_launcher_vars(
    *,
    stage: str,
    env_name: str,
    aws_region: str,
    aws_account: str,
    data_bucket: MapValue,
    cognito_user_pool_id: MapValue,
    cognito_app_client_id: MapValue,
    cognito_domain: MapValue,
    tenant_role_arn: MapValue,
    backend_ecr_repo_name: MapValue,
    codedeploy_app_name: MapValue,
    amplify_app_id: MapValue,
    amplify_default_domain: MapValue,
    amplify_console_url: MapValue,
    stage_app: dict[str, MapValue],
    compute_outputs: dict[str, MapValue],
    ecs_network: dict[str, MapValue],
    extension_vars: dict[str, MapValue],
    from_email: MapValue = "",
) -> dict[str, MapValue]:
    backend_fn = stage_app.get("fn_name", f"{env_name}-backend-{stage}")
    rest_url = normalize_url(stage_app.get("rest_url", ""))
    ws_connections = normalize_url(stage_app.get("ws_connections", ""))
    ws_url = normalize_url(stage_app.get("ws_url", ""))
    handlers_fn = compute_outputs.get("HandlersLambdaFunctionName", f"{env_name}-handlers")
    console_url = normalize_url(amplify_console_url)

    base: dict[str, MapValue] = {
        "WL_NAME": env_name,
        "BASE_URL": rest_url,
        # Invite links and similar console deep-links use the cloud Amplify URL.
        "FE_BASE_URL": console_url,
        "FROM_EMAIL": from_email,
        "LAMBDA_BACKEND_ARN": lambda_arn(aws_region, aws_account, backend_fn),
        "LAMBDA_EXTERNAL_HANDLERS_ARN": lambda_arn(aws_region, aws_account, handlers_fn),
        "ROLE_ARN": tenant_role_arn,
        **dynamodb_vars(env_name),
        "COGNITO_REGION": aws_region,
        "COGNITO_USERPOOL_ID": cognito_user_pool_id,
        "COGNITO_APP_CLIENT_ID": cognito_app_client_id,
        "COGNITO_DOMAIN": cognito_domain,
        "VITE_COGNITO_DOMAIN": cognito_domain,
        "COGNITO_CHECK_TOKEN_EXPIRATION": "True",
        "PREVIEW_LAYER": "2",
        "S3_BUCKET_NAME": data_bucket,
        "ALLOW_DEV_ORIGINS": "true",
        "CODEDEPLOY_APPLICATION_NAME": codedeploy_app_name,
        "CODEDEPLOY_DEPLOYMENT_GROUP_NAME": f"{env_name}-backend-{stage}",
        "CODEDEPLOY_DEPLOYMENT_CONFIG_NAME": CODEDEPLOY_CONFIG[stage],
        "AWS_REGION": aws_region,
        "AWS_DEFAULT_REGION": aws_region,
        "AWS_ECR_REPOSITORY": backend_ecr_repo_name,
        "WEBSOCKET_CONNECTIONS": ws_connections,
        "WEBSOCKET_URL": ws_url,
        "VITE_WEBSOCKET_URL": ws_url,
        "AMPLIFY_APP_ID": amplify_app_id,
        "AMPLIFY_DEFAULT_DOMAIN": amplify_default_domain,
        "AMPLIFY_CONSOLE_URL": console_url,
        "VITE_AMPLIFY_CONSOLE_URL": console_url,
        "ECS_CLUSTER": compute_outputs.get("HandlersEcsClusterName", ""),
        "ECS_TASK_DEFINITION": compute_outputs.get("HandlersTaskFamily", ""),
        "ECS_RESULTS_BUCKET": compute_outputs.get("HandlersResultsBucketName", ""),
        **ecs_network,
    }
    return merge_vars(base, extension_vars)


def build_deploy_input_vars(
    *,
    env_name: str,
    aws_region: str,
    aws_account: str,
    data_bucket: MapValue,
    cognito_user_pool_id: MapValue,
    cognito_app_client_id: MapValue,
    tenant_role_arn: MapValue,
    production_app: dict[str, MapValue],
    compute_outputs: dict[str, MapValue],
    ecs_network: dict[str, MapValue],
    extension_vars: dict[str, MapValue],
) -> dict[str, MapValue]:
    handlers_fn = compute_outputs.get("HandlersLambdaFunctionName", f"{env_name}-handlers")
    handlers_ecr_uri = compute_outputs.get("HandlersEcrRepoUri", "")
    ws_connections = normalize_url(production_app.get("ws_connections", ""))
    ws_url = normalize_url(production_app.get("ws_url", ""))

    ecr_image_uri: MapValue = handlers_ecr_uri
    if isinstance(handlers_ecr_uri, str) and handlers_ecr_uri:
        ecr_image_uri = f"{handlers_ecr_uri}:latest"

    return merge_vars(
        {
            "WL_NAME": env_name,
            "AWS_REGION": aws_region,
            "AWS_DEFAULT_REGION": aws_region,
            "ECS_CLUSTER": compute_outputs.get("HandlersEcsClusterName", ""),
            "ECS_TASK_DEFINITION": compute_outputs.get("HandlersTaskFamily", ""),
            "ECS_RESULTS_BUCKET": compute_outputs.get("HandlersResultsBucketName", ""),
            "LAMBDA_EXTERNAL_HANDLERS_ARN": lambda_arn(aws_region, aws_account, handlers_fn),
            **dynamodb_vars(env_name),
            "COGNITO_REGION": aws_region,
            "COGNITO_USERPOOL_ID": cognito_user_pool_id,
            "COGNITO_APP_CLIENT_ID": cognito_app_client_id,
            "COGNITO_CHECK_TOKEN_EXPIRATION": "True",
            "S3_BUCKET_NAME": data_bucket,
            "ROLE_ARN": tenant_role_arn,
            "WEBSOCKET_CONNECTIONS": ws_connections,
            "WEBSOCKET_URL": ws_url,
            "LAMBDA_HANDLERS_FUNCTION_NAME": handlers_fn,
            "ECR_IMAGE_URI": ecr_image_uri,
            **ecs_network,
        },
        extension_vars,
    )


def build_platform_vars_envelope(
    *,
    github_repo: str,
    stage: str,
    vars_dict: dict[str, MapValue],
) -> dict[str, Any]:
    return {
        "GITHUB_REPOSITORY": github_repo,
        "ENVIRONMENT": stage,
        "VARS": vars_dict,
        "SECRETS": dict(EMPTY_SECRETS),
    }


def build_deploy_input_envelope(
    *,
    github_handlers_repo: str,
    vars_dict: dict[str, MapValue],
) -> dict[str, Any]:
    return build_platform_vars_envelope(
        github_repo=github_handlers_repo,
        stage="production",
        vars_dict=vars_dict,
    )
