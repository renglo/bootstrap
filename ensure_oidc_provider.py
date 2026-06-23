"""Ensure the GitHub Actions OIDC provider exists in the AWS account.

This is an account-level, one-time prerequisite that must be created before the
CDK stacks are deployed.  The CDK stacks reference it via
`iam.OpenIdConnectProvider.from_open_id_connect_provider_arn` — they do NOT create
it — so this script (or a manual equivalent) must run first on any fresh account.

Usage:
    python bootstrap/ensure_oidc_provider.py \\
        --aws-profile my-profile \\
        --aws-region us-east-1

Or via the installer:
    python bootstrap/install.py ensure-oidc \\
        --aws-profile my-profile \\
        [--aws-region us-east-1]
"""

from __future__ import annotations

import argparse
from typing import Optional

import boto3
from botocore.exceptions import ClientError

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_THUMBPRINT = "6938fd4d98bab03faadb97b34396831e3780aea1"
CLIENT_ID = "sts.amazonaws.com"


def _session(profile: Optional[str], region: str) -> boto3.Session:
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def _oidc_provider_arn(account_id: str) -> str:
    return f"arn:aws:iam::{account_id}:oidc-provider/token.actions.githubusercontent.com"


def ensure_github_oidc_provider(
    aws_profile: Optional[str] = None,
    aws_region: str = "us-east-1",
    apply_changes: bool = True,
) -> dict:
    """Create the GitHub Actions OIDC provider if it does not already exist.

    Returns a dict with:
        oidc_provider_arn  – ARN of the provider (existing or newly created)
        created            – True if newly created, False if it already existed
    """
    session = _session(aws_profile, aws_region)
    account_id = session.client("sts").get_caller_identity()["Account"]
    iam = session.client("iam")
    provider_arn = _oidc_provider_arn(account_id)

    try:
        iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider_arn)
        print(f"GitHub OIDC provider already exists: {provider_arn}")
        return {"oidc_provider_arn": provider_arn, "created": False}
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in ("NoSuchEntity", "NoSuchEntityException"):
            raise

    if not apply_changes:
        print(f"[dry-run] Would create GitHub OIDC provider: {provider_arn}")
        return {"oidc_provider_arn": provider_arn, "created": True}

    iam.create_open_id_connect_provider(
        Url=GITHUB_OIDC_URL,
        ClientIDList=[CLIENT_ID],
        ThumbprintList=[GITHUB_OIDC_THUMBPRINT],
    )
    print(f"GitHub OIDC provider created: {provider_arn}")
    return {"oidc_provider_arn": provider_arn, "created": True}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Ensure the GitHub Actions OIDC provider exists in the AWS account. "
            "Run once before deploying CDK stacks."
        )
    )
    parser.add_argument("--aws-profile", default=None, help="AWS named profile")
    parser.add_argument("--aws-region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--dry-run", action="store_true", help="Plan without creating resources")
    args = parser.parse_args()

    result = ensure_github_oidc_provider(
        aws_profile=args.aws_profile,
        aws_region=args.aws_region,
        apply_changes=not args.dry_run,
    )
    print(f"OIDC Provider ARN: {result['oidc_provider_arn']}")


if __name__ == "__main__":
    main()
