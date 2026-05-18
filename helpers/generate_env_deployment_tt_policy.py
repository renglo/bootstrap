#!/usr/bin/env python3
"""
Generate IAM policy JSON for operators who run scripts/deploy_environment.py.

Writes bootstrap/state/<environment_name>/<environment_name>_deployment_tt_policy.json
by default (for handoff to a sysadmin to attach to an IAM user or group).

Requires either --account-id or --aws-profile to resolve the AWS account.
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


def _policy_document(
    env_name: str,
    region: str,
    account_id: str,
    *,
    deployment_operator_role_arn: str | None = None,
) -> dict:
    """Policy for operators running deploy_environment.py (and optional AssumeRole to deployment role)."""
    role_name = f"{env_name}_tt_role"
    policy_name = f"{env_name}_tt_policy"
    table_arn_prefix = f"arn:aws:dynamodb:{region}:{account_id}:table/{env_name}_"
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
    ecr_repo_arn = f"arn:aws:ecr:{region}:{account_id}:repository/{env_name}_backend"
    lambda_arn = f"arn:aws:lambda:{region}:{account_id}:function:{env_name}-backend-*"
    codedeploy_role_arn = f"arn:aws:iam::{account_id}:role/{env_name}-codedeploy-lambda-role"

    statements: list[dict] = [
        {
            "Sid": "ReadIdentity",
            "Effect": "Allow",
            "Action": "sts:GetCallerIdentity",
            "Resource": "*",
        },
        {
            "Sid": "DynamoEnvTables",
            "Effect": "Allow",
            "Action": [
                "dynamodb:CreateTable",
                "dynamodb:DescribeTable",
                "dynamodb:PutItem",
            ],
            "Resource": f"{table_arn_prefix}*",
        },
        {
            "Sid": "CognitoCreate",
            "Effect": "Allow",
            "Action": [
                "cognito-idp:CreateUserPool",
                "cognito-idp:CreateUserPoolClient",
            ],
            "Resource": "*",
        },
        {
            "Sid": "IamCreateNamedRole",
            "Effect": "Allow",
            "Action": "iam:CreateRole",
            "Resource": "*",
            "Condition": {"StringEquals": {"iam:RoleName": role_name}},
        },
        {
            "Sid": "IamCreateNamedPolicy",
            "Effect": "Allow",
            "Action": "iam:CreatePolicy",
            "Resource": "*",
            "Condition": {"StringEquals": {"iam:PolicyName": policy_name}},
        },
        {
            "Sid": "IamManageEnvRole",
            "Effect": "Allow",
            "Action": ["iam:GetRole", "iam:AttachRolePolicy"],
            "Resource": role_arn,
        },
        {
            "Sid": "IamManageEnvPolicy",
            "Effect": "Allow",
            "Action": [
                "iam:GetPolicy",
                "iam:ListPolicyVersions",
                "iam:GetPolicyVersion",
                "iam:CreatePolicyVersion",
            ],
            "Resource": policy_arn,
        },
        {
            "Sid": "EcrScopedRepository",
            "Effect": "Allow",
            "Action": [
                "ecr:CreateRepository",
                "ecr:DescribeRepositories",
                "ecr:BatchGetImage",
                "ecr:BatchCheckLayerAvailability",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
            ],
            "Resource": ecr_repo_arn,
        },
        {
            "Sid": "EcrAuthTokenGlobal",
            "Effect": "Allow",
            "Action": ["ecr:GetAuthorizationToken"],
            "Resource": "*",
        },
        {
            "Sid": "LambdaScopedBackend",
            "Effect": "Allow",
            "Action": [
                "lambda:CreateFunction",
                "lambda:GetFunction",
                "lambda:UpdateFunctionCode",
                "lambda:AddPermission",
                "lambda:PublishVersion",
                "lambda:CreateAlias",
                "lambda:UpdateAlias",
                "lambda:GetAlias",
                "lambda:ListAliases",
                "lambda:ListVersionsByFunction",
            ],
            "Resource": lambda_arn,
        },
        {
            "Sid": "ApiGatewayManage",
            "Effect": "Allow",
            "Action": [
                "apigateway:GET",
                "apigateway:POST",
                "apigateway:PATCH",
            ],
            "Resource": [
                f"arn:aws:apigateway:{region}::/restapis/*",
                f"arn:aws:apigateway:{region}::/apis/*",
            ],
        },
        {
            "Sid": "CodeDeployLambdaGroups",
            "Effect": "Allow",
            "Action": [
                "codedeploy:CreateApplication",
                "codedeploy:GetApplication",
                "codedeploy:CreateDeploymentGroup",
                "codedeploy:GetDeploymentGroup",
                "codedeploy:UpdateDeploymentGroup",
            ],
            "Resource": "*",
        },
        {
            "Sid": "ManageCodeDeployServiceRole",
            "Effect": "Allow",
            "Action": [
                "iam:CreateRole",
                "iam:GetRole",
                "iam:UpdateAssumeRolePolicy",
                "iam:AttachRolePolicy",
            ],
            "Resource": codedeploy_role_arn,
        },
        {
            "Sid": "AttachAwsManagedCodeDeployPolicy",
            "Effect": "Allow",
            "Action": "iam:AttachRolePolicy",
            "Resource": codedeploy_role_arn,
            "Condition": {
                "StringEquals": {
                    "iam:PolicyARN": "arn:aws:iam::aws:policy/service-role/AWSCodeDeployRoleForLambda"
                }
            },
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


def main() -> int:
    bootstrap_root = Path(__file__).resolve().parent.parent

    parser = argparse.ArgumentParser(
        description="Generate <env>_deployment_tt_policy.json for deploy_environment.py operators."
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
            "Output path "
            "(default: bootstrap/state/<environment_name>/<environment_name>_deployment_tt_policy.json)"
        ),
    )

    args = parser.parse_args()
    env_name = args.environment_name.strip()
    if not env_name:
        print("environment_name must be non-empty.", file=sys.stderr)
        return 1

    default_out = bootstrap_root / "state" / env_name / f"{env_name}_deployment_tt_policy.json"
    out_arg = args.output if args.output is not None else default_out

    account_id = _resolve_account_id(args.account_id, args.aws_profile, args.aws_region)
    if not account_id.isdigit() or len(account_id) != 12:
        print("Resolved account ID must be a 12-digit AWS account ID.", file=sys.stderr)
        return 1

    doc = _policy_document(
        env_name,
        args.aws_region,
        account_id,
        deployment_operator_role_arn=None,
    )
    out_path = Path(out_arg).expanduser()
    out_path = out_path.resolve() if out_path.is_absolute() else (Path.cwd() / out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
