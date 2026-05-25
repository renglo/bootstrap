#!/usr/bin/env python3
"""
Generate IAM policy JSON for operators who run the full platform installation
(launcher + extensions-service) for a given environment.

Two policy documents are produced:
  - <env>_deployment_tt_policy.json      — launcher resources
  - <env>_deployment_ext_policy.json     — extensions-service resources

Both files are written to bootstrap/state/<env>/ by default.

Requires either --account-id or --aws-profile to resolve the AWS account.

Usage:

  python bootstrap/helpers/generate_env_deployment_tt_policy.py <environment> --aws-profile <profile> --aws-region us-east-1

  # or with a fixed account ID (no AWS call):
  python bootstrap/helpers/generate_env_deployment_tt_policy.py <environment> --account-id <id> --aws-region us-east-1

"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore


# ---------------------------------------------------------------------------
# Launcher policy
# ---------------------------------------------------------------------------

def _policy_document(
    env_name: str,
    region: str,
    account_id: str,
    *,
    deployment_operator_role_arn: str | None = None,
) -> dict:
    """
    Permissions needed to run launcher/scripts/deploy_environment.py and
    launcher/scripts/teardown_environment.py for <env_name>.

    Covers: STS, DynamoDB, Cognito, S3 (backend bucket), IAM (tt_role,
    tt_policy, OIDC deploy roles/policies, codedeploy service role), ECR
    (backend), Lambda (backend-*), REST API Gateway, WebSocket API Gateway V2,
    CodeDeploy, CloudWatch Logs (Lambda).
    """
    table_arn      = f"arn:aws:dynamodb:{region}:{account_id}:table/{env_name}_*"
    tt_role_arn    = f"arn:aws:iam::{account_id}:role/{env_name}_tt_role"
    tt_policy_arn  = f"arn:aws:iam::{account_id}:policy/{env_name}_tt_policy"
    oidc_role_arn  = f"arn:aws:iam::{account_id}:role/GitHubActionsDeployRole-{env_name}-*"
    oidc_pol_arn   = f"arn:aws:iam::{account_id}:policy/GitHubActionsDeployPolicy-{env_name}-*"
    cd_role_arn    = f"arn:aws:iam::{account_id}:role/{env_name}-codedeploy-lambda-role"
    ecr_arn        = f"arn:aws:ecr:{region}:{account_id}:repository/{env_name}_backend"
    lambda_arn     = f"arn:aws:lambda:{region}:{account_id}:function:{env_name}-backend-*"
    lambda_log_arn = f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/{env_name}-backend-*:*"
    s3_bucket      = f"arn:aws:s3:::{env_name}-{account_id}-{region}"
    s3_objects     = f"arn:aws:s3:::{env_name}-{account_id}-{region}/*"
    apigw_rest_root = f"arn:aws:apigateway:{region}::/restapis"
    apigw_rest     = f"arn:aws:apigateway:{region}::/restapis/*"
    apigw_ws_root  = f"arn:aws:apigateway:{region}::/apis"
    apigw_ws       = f"arn:aws:apigateway:{region}::/apis/*"
    cd_app_arn     = f"arn:aws:codedeploy:{region}:{account_id}:application:{env_name}-backend-codedeploy"
    cd_group_arn   = f"arn:aws:codedeploy:{region}:{account_id}:deploymentgroup:{env_name}-backend-codedeploy/*"
    cd_config_arn  = f"arn:aws:codedeploy:{region}:{account_id}:deploymentconfig:*"

    _iam_role_actions = [
        "iam:GetRole",
        "iam:CreateRole",
        "iam:UpdateAssumeRolePolicy",
        "iam:UpdateRoleDescription",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:DeleteRole",
    ]
    _iam_policy_actions = [
        "iam:GetPolicy",
        "iam:CreatePolicy",
        "iam:ListPolicyVersions",
        "iam:GetPolicyVersion",
        "iam:CreatePolicyVersion",
        "iam:DeletePolicyVersion",
        "iam:DeletePolicy",
    ]

    statements: list[dict] = [
        {
            "Sid": "ReadIdentity",
            "Effect": "Allow",
            "Action": "sts:GetCallerIdentity",
            "Resource": "*",
        },
        # DynamoDB — all {env}_* tables (create, seed blueprints, teardown)
        {
            "Sid": "DynamoEnvTables",
            "Effect": "Allow",
            "Action": [
                "dynamodb:CreateTable",
                "dynamodb:DescribeTable",
                "dynamodb:PutItem",
                "dynamodb:DeleteTable",
            ],
            "Resource": table_arn,
        },
        # Cognito — can only be scoped after pool is created
        {
            "Sid": "CognitoEnvPool",
            "Effect": "Allow",
            "Action": [
                "cognito-idp:ListUserPools",
                "cognito-idp:CreateUserPool",
                "cognito-idp:DescribeUserPool",
                "cognito-idp:ListUserPoolClients",
                "cognito-idp:CreateUserPoolClient",
                "cognito-idp:DeleteUserPool",
            ],
            "Resource": "*",
        },
        # S3 backend bucket
        {
            "Sid": "S3BackendBucket",
            "Effect": "Allow",
            "Action": [
                "s3:CreateBucket",
                "s3:HeadBucket",
                "s3:GetBucketLocation",
                "s3:GetBucketVersioning",
                "s3:PutBucketVersioning",
                "s3:PutBucketPolicy",
                "s3:PutBucketPublicAccessBlock",
                "s3:PutBucketTagging",
                "s3:GetBucketTagging",
                "s3:ListBucket",
                "s3:ListBucketVersions",
                "s3:DeleteBucket",
            ],
            "Resource": s3_bucket,
        },
        {
            "Sid": "S3BackendObjects",
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:DeleteObjects",
            ],
            "Resource": s3_objects,
        },
        # IAM — tt_role (Lambda execution + event trust)
        {
            "Sid": "IamTTRole",
            "Effect": "Allow",
            "Action": _iam_role_actions,
            "Resource": tt_role_arn,
        },
        # IAM — tt_policy (platform runtime permissions document)
        {
            "Sid": "IamTTPolicy",
            "Effect": "Allow",
            "Action": _iam_policy_actions,
            "Resource": tt_policy_arn,
        },
        # IAM — GitHub OIDC provider (shared, can't be scoped)
        {
            "Sid": "IamOIDCProvider",
            "Effect": "Allow",
            "Action": [
                "iam:GetOpenIDConnectProvider",
                "iam:CreateOpenIDConnectProvider",
            ],
            "Resource": "*",
        },
        # IAM — GitHub OIDC deploy roles (production + staging)
        {
            "Sid": "IamDeployRoles",
            "Effect": "Allow",
            "Action": _iam_role_actions,
            "Resource": oidc_role_arn,
        },
        # IAM — GitHub OIDC deploy policies
        {
            "Sid": "IamDeployPolicies",
            "Effect": "Allow",
            "Action": _iam_policy_actions,
            "Resource": oidc_pol_arn,
        },
        # IAM — CodeDeploy service role for Lambda blue/green
        {
            "Sid": "IamCodeDeployRole",
            "Effect": "Allow",
            "Action": _iam_role_actions,
            "Resource": cd_role_arn,
        },
        # IAM — pass tt_role to Lambda
        {
            "Sid": "IamPassRoleBackendLambda",
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": tt_role_arn,
            "Condition": {
                "StringEquals": {"iam:PassedToService": "lambda.amazonaws.com"},
            },
        },
        # IAM — pass CodeDeploy service role to CodeDeploy
        {
            "Sid": "IamPassRoleCodeDeploy",
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": cd_role_arn,
            "Condition": {
                "StringEquals": {"iam:PassedToService": "codedeploy.amazonaws.com"},
            },
        },
        # ECR — backend container repository
        {
            "Sid": "EcrBackend",
            "Effect": "Allow",
            "Action": [
                "ecr:DescribeRepositories",
                "ecr:CreateRepository",
                "ecr:DeleteRepository",
                "ecr:TagResource",
                "ecr:BatchGetImage",
                "ecr:BatchCheckLayerAvailability",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
                "ecr:BatchDeleteImage",
                "ecr:ListImages",
                "ecr:GetRepositoryPolicy",
                "ecr:SetRepositoryPolicy",
                "ecr:DeleteRepositoryPolicy",
            ],
            "Resource": ecr_arn,
        },
        {
            "Sid": "EcrAuthToken",
            "Effect": "Allow",
            "Action": "ecr:GetAuthorizationToken",
            "Resource": "*",
        },
        # Lambda — backend functions (provision + update + teardown)
        {
            "Sid": "LambdaBackend",
            "Effect": "Allow",
            "Action": [
                "lambda:GetFunction",
                "lambda:GetFunctionConfiguration",
                "lambda:CreateFunction",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
                "lambda:AddPermission",
                "lambda:RemovePermission",
                "lambda:PublishVersion",
                "lambda:CreateAlias",
                "lambda:UpdateAlias",
                "lambda:GetAlias",
                "lambda:ListAliases",
                "lambda:ListVersionsByFunction",
                "lambda:DeleteFunction",
                "lambda:TagResource",
                "lambda:ListTags",
            ],
            "Resource": lambda_arn,
        },
        # API Gateway — REST (provision, update, teardown)
        {
            "Sid": "ApiGatewayRest",
            "Effect": "Allow",
            "Action": [
                "apigateway:GET",
                "apigateway:POST",
                "apigateway:PUT",
                "apigateway:PATCH",
                "apigateway:DELETE",
            ],
            "Resource": [apigw_rest_root, apigw_rest],
        },
        # API Gateway V2 — WebSocket (provision, teardown)
        {
            "Sid": "ApiGatewayWebSocket",
            "Effect": "Allow",
            "Action": [
                "apigateway:GET",
                "apigateway:POST",
                "apigateway:PUT",
                "apigateway:PATCH",
                "apigateway:DELETE",
            ],
            "Resource": [apigw_ws_root, apigw_ws],
        },
        # CodeDeploy — Lambda blue/green deployment groups
        {
            "Sid": "CodeDeployManage",
            "Effect": "Allow",
            "Action": [
                "codedeploy:GetApplication",
                "codedeploy:CreateApplication",
                "codedeploy:DeleteApplication",
                "codedeploy:GetDeploymentGroup",
                "codedeploy:CreateDeploymentGroup",
                "codedeploy:UpdateDeploymentGroup",
                "codedeploy:DeleteDeploymentGroup",
                "codedeploy:CreateDeployment",
                "codedeploy:GetDeployment",
                "codedeploy:GetDeploymentConfig",
                "codedeploy:RegisterApplicationRevision",
            ],
            "Resource": [cd_app_arn, cd_group_arn, cd_config_arn],
        },
        # CloudWatch Logs — backend Lambda log groups
        {
            "Sid": "LogsBackendLambda",
            "Effect": "Allow",
            "Action": [
                "logs:DescribeLogGroups",
                "logs:CreateLogGroup",
                "logs:PutRetentionPolicy",
                "logs:DeleteLogGroup",
            ],
            "Resource": lambda_log_arn,
        },
    ]

    if deployment_operator_role_arn:
        statements.append(
            {
                "Sid": "AssumeDeploymentOperatorRole",
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Resource": deployment_operator_role_arn,
            }
        )

    return {"Version": "2012-10-17", "Statement": statements}


# ---------------------------------------------------------------------------
# Extensions-service policy
# ---------------------------------------------------------------------------

def _extensions_policy_document(env_name: str, region: str, account_id: str) -> dict:
    """
    Permissions needed to run extensions-service/run.py provision-infra (apply +
    teardown) and deploy (ECS + Lambda) for <env_name>.

    Covers all deploy types: Lambda, ECS Fargate, ECS EC2 (capacity change
    included). Also covers full teardown.

    Resources: S3 handlers-ECS bucket, ECR handlers repo, ECS cluster + task
    definitions + capacity providers, IAM handlers roles/policies (Lambda role,
    ECS execution/task/instance roles, OIDC handlers roles), EC2 launch
    templates + ASG, SSM AMI parameter, CloudWatch Logs.
    """
    # Compute the capitalized policy name used by setup_iam_role.sh / teardown_all.sh
    handlers_policy_name = env_name[0].upper() + env_name[1:] + "HandlersPolicy"

    handlers_role_arn     = f"arn:aws:iam::{account_id}:role/{env_name}-handlers-role"
    handlers_ecs_role_arn = f"arn:aws:iam::{account_id}:role/{env_name}-handlers-ecs-*"
    handlers_policy_arn   = f"arn:aws:iam::{account_id}:policy/{handlers_policy_name}"
    oidc_role_arn         = f"arn:aws:iam::{account_id}:role/GitHubActionsHandlersRole-{env_name}-*"
    oidc_pol_arn          = f"arn:aws:iam::{account_id}:policy/GitHubActionsHandlersPolicy-{env_name}-*"
    instance_profile_arn  = f"arn:aws:iam::{account_id}:instance-profile/{env_name}-handlers-ecs-instance-profile"

    # All handlers IAM roles (lambda role + ecs-execution + ecs-task + ecs-instance)
    handlers_all_roles = [handlers_role_arn, handlers_ecs_role_arn]

    ecr_arn       = f"arn:aws:ecr:{region}:{account_id}:repository/{env_name}-handlers-ecs"
    cluster_arn   = f"arn:aws:ecs:{region}:{account_id}:cluster/{env_name}-handlers"
    task_def_arn  = f"arn:aws:ecs:{region}:{account_id}:task-definition/{env_name}-handlers-ecs:*"
    s3_bucket     = f"arn:aws:s3:::{env_name}-handlers-ecs-{account_id}"
    s3_objects    = f"arn:aws:s3:::{env_name}-handlers-ecs-{account_id}/*"
    lambda_arn    = f"arn:aws:lambda:{region}:{account_id}:function:{env_name}-handlers"
    ecs_log_arn   = f"arn:aws:logs:{region}:{account_id}:log-group:/ecs/{env_name}-handlers-ecs:*"
    lambda_log_arn = f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/{env_name}-handlers:*"
    ssm_ami_arn   = f"arn:aws:ssm:{region}::parameter/aws/service/ecs/*"

    _iam_role_actions = [
        "iam:GetRole",
        "iam:CreateRole",
        "iam:UpdateAssumeRolePolicy",
        "iam:UpdateRoleDescription",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:ListRolePolicies",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:DeleteRole",
    ]
    _iam_policy_actions = [
        "iam:GetPolicy",
        "iam:CreatePolicy",
        "iam:ListPolicyVersions",
        "iam:GetPolicyVersion",
        "iam:CreatePolicyVersion",
        "iam:DeletePolicyVersion",
        "iam:DeletePolicy",
    ]

    statements: list[dict] = [
        {
            "Sid": "ReadIdentity",
            "Effect": "Allow",
            "Action": "sts:GetCallerIdentity",
            "Resource": "*",
        },
        # S3 — ECS task handshake results bucket
        {
            "Sid": "S3HandlersECSBucket",
            "Effect": "Allow",
            "Action": [
                "s3:CreateBucket",
                "s3:HeadBucket",
                "s3:GetBucketLocation",
                "s3:GetBucketVersioning",
                "s3:PutBucketVersioning",
                "s3:PutBucketTagging",
                "s3:GetBucketTagging",
                "s3:PutLifecycleConfiguration",
                "s3:ListBucket",
                "s3:ListBucketVersions",
                "s3:DeleteBucket",
            ],
            "Resource": s3_bucket,
        },
        {
            "Sid": "S3HandlersECSObjects",
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:DeleteObjects",
            ],
            "Resource": s3_objects,
        },
        # ECR — handlers container repository
        {
            "Sid": "EcrHandlers",
            "Effect": "Allow",
            "Action": [
                "ecr:DescribeRepositories",
                "ecr:CreateRepository",
                "ecr:DeleteRepository",
                "ecr:TagResource",
                "ecr:BatchGetImage",
                "ecr:BatchCheckLayerAvailability",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
                "ecr:BatchDeleteImage",
                "ecr:ListImages",
            ],
            "Resource": ecr_arn,
        },
        {
            "Sid": "EcrAuthToken",
            "Effect": "Allow",
            "Action": "ecr:GetAuthorizationToken",
            "Resource": "*",
        },
        # ECS — cluster lifecycle
        {
            "Sid": "ECSCluster",
            "Effect": "Allow",
            "Action": [
                "ecs:DescribeClusters",
                "ecs:CreateCluster",
                "ecs:DeleteCluster",
                "ecs:TagResource",
                "ecs:PutClusterCapacityProviders",
            ],
            "Resource": cluster_arn,
        },
        # ECS — task definitions: register/deregister/describe.
        # RegisterTaskDefinition requires * (task def doesn't exist at call time).
        {
            "Sid": "ECSTaskDefinitions",
            "Effect": "Allow",
            "Action": [
                "ecs:RegisterTaskDefinition",
                "ecs:ListTaskDefinitions",
            ],
            "Resource": "*",
        },
        {
            "Sid": "ECSTaskDefinitionsScoped",
            "Effect": "Allow",
            "Action": [
                "ecs:DescribeTaskDefinition",
                "ecs:DeregisterTaskDefinition",
                "ecs:TagResource",
            ],
            "Resource": task_def_arn,
        },
        # ECS — run tasks (Lambda handlers trigger ECS; deploy tooling may also run test tasks)
        {
            "Sid": "ECSRunTask",
            "Effect": "Allow",
            "Action": [
                "ecs:RunTask",
                "ecs:DescribeTasks",
                "ecs:ListTasks",
            ],
            "Resource": [cluster_arn, task_def_arn],
        },
        # ECS — capacity providers (EC2 launch type)
        {
            "Sid": "ECSCapacityProviders",
            "Effect": "Allow",
            "Action": [
                "ecs:DescribeCapacityProviders",
                "ecs:CreateCapacityProvider",
                "ecs:DeleteCapacityProvider",
                "ecs:TagResource",
            ],
            # Fargate built-ins cannot be scoped to account ARNs
            "Resource": "*",
        },
        # IAM — handlers + OIDC roles (merged to stay under managed-policy size quota)
        {
            "Sid": "IamHandlersRoles",
            "Effect": "Allow",
            "Action": _iam_role_actions,
            "Resource": [handlers_role_arn, handlers_ecs_role_arn, oidc_role_arn],
        },
        # IAM — handlers + OIDC managed policies
        {
            "Sid": "IamHandlersPolicies",
            "Effect": "Allow",
            "Action": _iam_policy_actions,
            "Resource": [handlers_policy_arn, oidc_pol_arn],
        },
        # IAM — EC2 instance profile (ECS EC2 launch type)
        {
            "Sid": "IamHandlersInstanceProfile",
            "Effect": "Allow",
            "Action": [
                "iam:GetInstanceProfile",
                "iam:CreateInstanceProfile",
                "iam:DeleteInstanceProfile",
                "iam:AddRoleToInstanceProfile",
                "iam:RemoveRoleFromInstanceProfile",
            ],
            "Resource": instance_profile_arn,
        },
        # IAM — GitHub OIDC provider (shared; also needed in handlers bootstrap)
        {
            "Sid": "IamOIDCProvider",
            "Effect": "Allow",
            "Action": [
                "iam:GetOpenIDConnectProvider",
                "iam:CreateOpenIDConnectProvider",
            ],
            "Resource": "*",
        },
        # IAM — pass roles to Lambda and ECS (including EC2 instance profile)
        {
            "Sid": "IamPassRoleHandlers",
            "Effect": "Allow",
            "Action": "iam:PassRole",
            "Resource": handlers_all_roles,
            "Condition": {
                "StringEquals": {
                    "iam:PassedToService": [
                        "lambda.amazonaws.com",
                        "ecs-tasks.amazonaws.com",
                        "ec2.amazonaws.com",
                    ]
                }
            },
        },
        # Lambda — handlers function (deploy_as_a_service + setup_iam)
        {
            "Sid": "LambdaHandlers",
            "Effect": "Allow",
            "Action": [
                "lambda:GetFunction",
                "lambda:GetFunctionConfiguration",
                "lambda:CreateFunction",
                "lambda:UpdateFunctionCode",
                "lambda:UpdateFunctionConfiguration",
                "lambda:AddPermission",
                "lambda:RemovePermission",
                "lambda:PublishVersion",
                "lambda:CreateAlias",
                "lambda:UpdateAlias",
                "lambda:GetAlias",
                "lambda:ListAliases",
                "lambda:ListVersionsByFunction",
                "lambda:PutFunctionEventInvokeConfig",
                "lambda:GetFunctionEventInvokeConfig",
                "lambda:TagResource",
                "lambda:ListTags",
                "lambda:DeleteFunction",
            ],
            "Resource": lambda_arn,
        },
        # EC2 — launch template lifecycle (ECS EC2 capacity)
        {
            "Sid": "EC2LaunchTemplates",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeLaunchTemplates",
                "ec2:DescribeLaunchTemplateVersions",
                "ec2:CreateLaunchTemplate",
                "ec2:CreateLaunchTemplateVersion",
                "ec2:DeleteLaunchTemplate",
            ],
            "Resource": "*",
        },
        # EC2 — RunInstances (required by ASG when using a launch template)
        {
            "Sid": "EC2RunInstances",
            "Effect": "Allow",
            "Action": [
                "ec2:RunInstances",
                "ec2:CreateTags",
            ],
            "Resource": "*",
        },
        # EC2 — VPC discovery (read-only; describe ops require *)
        {
            "Sid": "EC2VPCRead",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeVpcs",
                "ec2:DescribeSubnets",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeInstances",
                "ec2:DescribeInstanceTypes",
                "ec2:DescribeImages",
            ],
            "Resource": "*",
        },
        # Auto Scaling — ASG lifecycle for ECS EC2 capacity
        {
            "Sid": "AutoScalingManage",
            "Effect": "Allow",
            "Action": [
                "autoscaling:DescribeAutoScalingGroups",
                "autoscaling:DescribeScalingActivities",
                "autoscaling:CreateAutoScalingGroup",
                "autoscaling:UpdateAutoScalingGroup",
                "autoscaling:DeleteAutoScalingGroup",
                "autoscaling:CreateOrUpdateTags",
            ],
            # ASG ARNs can be scoped; use wildcard for simplicity given the
            # group name is deterministic: {env}-handlers-ecs-asg
            "Resource": "*",
        },
        # SSM — resolve ECS-optimized AMI ID at deploy time
        {
            "Sid": "SSMReadECSAMI",
            "Effect": "Allow",
            "Action": "ssm:GetParameter",
            "Resource": ssm_ami_arn,
        },
        # CloudWatch Logs — ECS task log group + optional Lambda handlers log group
        {
            "Sid": "LogsHandlers",
            "Effect": "Allow",
            "Action": [
                "logs:DescribeLogGroups",
                "logs:CreateLogGroup",
                "logs:PutRetentionPolicy",
                "logs:DeleteLogGroup",
            ],
            "Resource": [ecs_log_arn, lambda_log_arn],
        },
    ]

    return {"Version": "2012-10-17", "Statement": statements}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# IAM customer-managed policy document size limit (compact JSON).
IAM_MANAGED_POLICY_MAX_CHARS = 6144


def _policy_document_size(policy: dict) -> int:
    return len(json.dumps(policy, separators=(",", ":")))


def _resolve_account_id(account_id: str | None, aws_profile: str | None, region: str) -> str:
    if account_id:
        return account_id.strip()
    if not aws_profile:
        raise SystemExit(
            "Provide --account-id (12 digits) or --aws-profile so the account can be resolved."
        )
    if boto3 is None:
        raise SystemExit("boto3 is required when using --aws-profile. pip install boto3")
    session = boto3.Session(profile_name=aws_profile, region_name=region)
    aid = session.client("sts").get_caller_identity().get("Account")
    if not aid:
        raise SystemExit("Could not read Account from sts:GetCallerIdentity.")
    return aid


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    bootstrap_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(
        description=(
            "Generate IAM policy JSON files for the full platform install operator "
            "(launcher + extensions-service)."
        )
    )
    parser.add_argument(
        "environment_name",
        help="Environment prefix (same as deploy_environment.py argument), e.g. dev or xyz",
    )
    parser.add_argument(
        "--aws-region",
        default="us-east-1",
        help="AWS region where resources are created (default: us-east-1)",
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help="12-digit AWS account ID (omit if using --aws-profile)",
    )
    parser.add_argument(
        "--aws-profile",
        default=None,
        help="AWS profile; used with STS to resolve account if --account-id is omitted",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=(
            "Output directory for policy files "
            "(default: bootstrap/state/<environment_name>/). "
            "Two files are written: <env>_deployment_tt_policy.json and "
            "<env>_deployment_ext_policy.json"
        ),
    )

    args = parser.parse_args()
    env_name = args.environment_name.strip()
    if not env_name:
        print("environment_name must be non-empty.", file=sys.stderr)
        return 1

    account_id = _resolve_account_id(args.account_id, args.aws_profile, args.aws_region)
    if not account_id.isdigit() or len(account_id) != 12:
        print("Resolved account ID must be a 12-digit AWS account ID.", file=sys.stderr)
        return 1

    out_dir: Path
    if args.output is not None:
        out_dir = Path(args.output).expanduser().resolve()
    else:
        out_dir = bootstrap_root / "state" / env_name
    out_dir.mkdir(parents=True, exist_ok=True)

    launcher_doc = _policy_document(
        env_name,
        args.aws_region,
        account_id,
        deployment_operator_role_arn=None,
    )
    extensions_doc = _extensions_policy_document(env_name, args.aws_region, account_id)

    for label, doc in (
        ("launcher", launcher_doc),
        ("extensions", extensions_doc),
    ):
        n = _policy_document_size(doc)
        if n > IAM_MANAGED_POLICY_MAX_CHARS:
            raise SystemExit(
                f"{label} policy document is {n} characters (limit "
                f"{IAM_MANAGED_POLICY_MAX_CHARS}). Split statements or reduce scope."
            )

    launcher_path = out_dir / f"{env_name}_deployment_tt_policy.json"
    extensions_path = out_dir / f"{env_name}_deployment_ext_policy.json"

    launcher_path.write_text(json.dumps(launcher_doc, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote launcher policy  : {launcher_path}")

    extensions_path.write_text(json.dumps(extensions_doc, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote extensions policy: {extensions_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
