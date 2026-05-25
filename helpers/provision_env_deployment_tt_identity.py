#!/usr/bin/env python3
"""
Create IAM identity for deploy_environment.py operators (sysadmin profile).

Idempotent: skips or merges when user, group, role, or policy already exists.

Two customer-managed policies are created and attached to the user/group/role:
  - <env>_deployment_tt_policy      — launcher resources
  - <env>_deployment_ext_policy     — extensions-service resources (ECS, EC2, ASG…)

Typical usage (from repo root or any cwd; use paths relative to cwd if overriding outputs):
  python bootstrap/helpers/provision_env_deployment_tt_identity.py myenv \\
    --aws-profile <profile> --aws-region us-east-1 --create-access-key

Use flag --custom-username to create more users.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Short pause after IAM creates so downstream APIs (e.g. CreateRole trusting a new user) see the principal.
IAM_CREATE_PROPAGATION_SLEEP_SEC = 5
# CreateRole: initial attempt + 2 retries on MalformedPolicyDocument (IAM eventual consistency).
CREATE_ROLE_MAX_RETRIES = 2

_HELPERS = Path(__file__).resolve().parent
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))

from botocore.exceptions import ClientError

from generate_env_deployment_tt_policy import (
    IAM_MANAGED_POLICY_MAX_CHARS,
    _extensions_policy_document,
    _policy_document,
    _policy_document_size,
    _resolve_account_id,
)

_BOOTSTRAP_ROOT = _HELPERS.parent

try:
    import boto3
except ImportError as e:  # pragma: no cover
    raise SystemExit("boto3 is required. pip install boto3") from e


def _is_no_such_entity(err: ClientError) -> bool:
    code = err.response.get("Error", {}).get("Code", "")
    return code in ("NoSuchEntity", "NoSuchEntityException")


def _normalize_principal_aws(principal: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    v = principal.get("AWS")
    if isinstance(v, str):
        out.add(v)
    elif isinstance(v, list):
        out.update(x for x in v if isinstance(x, str))
    return out


def _merge_user_into_trust_policy(
    existing_document: str | dict[str, Any], account_id: str, user_name: str
) -> str:
    """Ensure the IAM user ARN can sts:AssumeRole; preserve other principals where possible."""
    user_arn = f"arn:aws:iam::{account_id}:user/{user_name}"
    if isinstance(existing_document, str):
        doc = json.loads(existing_document)
    else:
        doc = dict(existing_document)

    statements = doc.setdefault("Statement", [])
    if not isinstance(statements, list):
        statements = [statements]
        doc["Statement"] = statements

    merged = False
    for st in statements:
        act = st.get("Action")
        acts = act if isinstance(act, list) else ([act] if act else [])
        if "sts:AssumeRole" not in acts:
            continue
        merged = True
        pr = dict(st.get("Principal") or {})
        cur = _normalize_principal_aws(pr)
        cur.add(user_arn)
        if len(cur) == 1:
            pr["AWS"] = user_arn
        else:
            pr["AWS"] = sorted(cur)
        st["Principal"] = pr

    if not merged:
        statements.append(
            {
                "Effect": "Allow",
                "Principal": {"AWS": user_arn},
                "Action": "sts:AssumeRole",
            }
        )

    return json.dumps(doc)


def _policy_documents_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return json.dumps(a, sort_keys=True, separators=(",", ":")) == json.dumps(
        b, sort_keys=True, separators=(",", ":")
    )


def _get_default_policy_document(iam, policy_arn: str) -> dict[str, Any]:
    meta = iam.get_policy(PolicyArn=policy_arn)["Policy"]
    raw = iam.get_policy_version(
        PolicyArn=policy_arn,
        VersionId=meta["DefaultVersionId"],
    )["PolicyVersion"]["Document"]
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def _update_customer_managed_policy(iam, policy_arn: str, doc_str: str) -> None:
    versions = iam.list_policy_versions(PolicyArn=policy_arn).get("Versions", [])
    if len(versions) >= 5:
        for v in sorted(versions, key=lambda x: x["CreateDate"]):
            if not v["IsDefaultVersion"]:
                iam.delete_policy_version(PolicyArn=policy_arn, VersionId=v["VersionId"])
                break

    iam.create_policy_version(
        PolicyArn=policy_arn,
        PolicyDocument=doc_str,
        SetAsDefault=True,
    )
    print(f"Updated policy document: {policy_arn}")
    time.sleep(IAM_CREATE_PROPAGATION_SLEEP_SEC)


def _ensure_customer_managed_policy(
    iam,
    account_id: str,
    policy_name: str,
    policy_document: dict[str, Any],
    *,
    update: bool = False,
) -> tuple[str, bool]:
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
    doc_str = json.dumps(policy_document, separators=(",", ":"))
    doc_size = len(doc_str)
    if doc_size > IAM_MANAGED_POLICY_MAX_CHARS:
        raise SystemExit(
            f"Policy {policy_name} is {doc_size} characters (IAM limit "
            f"{IAM_MANAGED_POLICY_MAX_CHARS}). Re-run generate_env_deployment_tt_policy.py "
            "after updating the generator, or split into multiple policies."
        )

    try:
        iam.get_policy(PolicyArn=policy_arn)
        if update:
            current = _get_default_policy_document(iam, policy_arn)
            if _policy_documents_equal(current, policy_document):
                print(f"Policy up to date: {policy_arn}")
            else:
                _update_customer_managed_policy(iam, policy_arn, doc_str)
        else:
            print(f"Policy exists: {policy_arn} (document not auto-updated; pass --update-policies)")
        return policy_arn, False
    except ClientError as e:
        if not _is_no_such_entity(e):
            raise

    resp = iam.create_policy(
        PolicyName=policy_name,
        PolicyDocument=doc_str,
        Description=f"TT deploy_environment operator policy ({policy_name})",
    )
    policy_arn = resp["Policy"]["Arn"]
    print(f"Created policy: {policy_arn}")
    time.sleep(IAM_CREATE_PROPAGATION_SLEEP_SEC)
    return policy_arn, True


def _ensure_user(iam, user_name: str) -> bool:
    try:
        iam.get_user(UserName=user_name)
        print(f"User exists: {user_name}")
        return False
    except ClientError as e:
        if not _is_no_such_entity(e):
            raise
        iam.create_user(UserName=user_name)
        print(f"Created user: {user_name}")
        time.sleep(IAM_CREATE_PROPAGATION_SLEEP_SEC)
        return True


def _ensure_group(iam, group_name: str) -> bool:
    try:
        iam.get_group(GroupName=group_name)
        print(f"Group exists: {group_name}")
        return False
    except ClientError as e:
        if not _is_no_such_entity(e):
            raise
        iam.create_group(GroupName=group_name)
        print(f"Created group: {group_name}")
        time.sleep(IAM_CREATE_PROPAGATION_SLEEP_SEC)
        return True


def _ensure_attach_user_policy(iam, user_name: str, policy_arn: str) -> None:
    attached = iam.list_attached_user_policies(UserName=user_name)
    for p in attached.get("AttachedPolicies", []):
        if p.get("PolicyArn") == policy_arn:
            print(f"Policy already attached to user {user_name}: {policy_arn}")
            return
    iam.attach_user_policy(UserName=user_name, PolicyArn=policy_arn)
    print(f"Attached {policy_arn} to user: {user_name}")


def _ensure_attach_group_policy(iam, group_name: str, policy_arn: str) -> None:
    attached = iam.list_attached_group_policies(GroupName=group_name)
    for p in attached.get("AttachedPolicies", []):
        if p.get("PolicyArn") == policy_arn:
            print(f"Policy already attached to group {group_name}: {policy_arn}")
            return
    iam.attach_group_policy(GroupName=group_name, PolicyArn=policy_arn)
    print(f"Attached {policy_arn} to group: {group_name}")


def _ensure_user_in_group(iam, user_name: str, group_name: str) -> None:
    resp = iam.list_groups_for_user(UserName=user_name)
    for g in resp.get("Groups", []):
        if g.get("GroupName") == group_name:
            print(f"User {user_name} already in group {group_name}")
            return
    iam.add_user_to_group(GroupName=group_name, UserName=user_name)
    print(f"Added user {user_name} to group {group_name}")


def _is_malformed_policy_document(err: ClientError) -> bool:
    return err.response.get("Error", {}).get("Code", "") == "MalformedPolicyDocument"


def _ensure_deployment_role(
    iam,
    account_id: str,
    role_name: str,
    user_name: str,
    policy_arns: list[str],
) -> str:
    """Create (or update) the deployment role and attach all supplied policy ARNs."""
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
    trust = _merge_user_into_trust_policy({}, account_id, user_name)

    try:
        iam.get_role(RoleName=role_name)
        print(f"Role exists: {role_name}; updating assume role policy if needed")
        current = iam.get_role(RoleName=role_name)["Role"]["AssumeRolePolicyDocument"]
        updated = _merge_user_into_trust_policy(current, account_id, user_name)
        if json.dumps(current, sort_keys=True) != json.dumps(json.loads(updated), sort_keys=True):
            iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=updated)
            print("Updated role trust policy")
    except ClientError as e:
        if not _is_no_such_entity(e):
            raise
        for attempt in range(CREATE_ROLE_MAX_RETRIES + 1):
            try:
                iam.create_role(
                    RoleName=role_name,
                    AssumeRolePolicyDocument=trust,
                    Description=f"TT deploy_environment operator role ({role_name})",
                )
                print(f"Created role: {role_arn}")
                time.sleep(IAM_CREATE_PROPAGATION_SLEEP_SEC)
                break
            except ClientError as ce:
                if _is_malformed_policy_document(ce) and attempt < CREATE_ROLE_MAX_RETRIES:
                    print(
                        f"CreateRole MalformedPolicyDocument (attempt {attempt + 1}/"
                        f"{CREATE_ROLE_MAX_RETRIES + 1}); retrying after "
                        f"{IAM_CREATE_PROPAGATION_SLEEP_SEC}s...",
                        file=sys.stderr,
                    )
                    time.sleep(IAM_CREATE_PROPAGATION_SLEEP_SEC)
                    continue
                raise

    attached = {
        p.get("PolicyArn")
        for p in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", [])
    }
    for arn in policy_arns:
        if arn in attached:
            print(f"Policy already attached to role {role_name}: {arn}")
        else:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=arn)
            print(f"Attached {arn} to role: {role_name}")

    return role_arn


def _save_access_key_to_file(path: Path, user_name: str, key: dict[str, Any]) -> None:
    """Append one access key record to JSON; chmod 0600 on Unix."""
    ak = key["AccessKeyId"]
    sk = key["SecretAccessKey"]
    entry: dict[str, Any] = {
        "UserName": user_name,
        "AccessKeyId": ak,
        "SecretAccessKey": sk,
        "Status": key.get("Status", "Active"),
    }
    cd = key.get("CreateDate")
    if cd is not None and hasattr(cd, "isoformat"):
        entry["CreateDate"] = cd.isoformat()

    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any]
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "access_keys" in raw:
                data = raw
            else:
                data = {"user_name": user_name, "access_keys": []}
        except (json.JSONDecodeError, OSError):
            data = {"user_name": user_name, "access_keys": []}
    else:
        data = {"user_name": user_name, "access_keys": []}

    if data.get("user_name") != user_name:
        data["user_name"] = user_name
    keys_list = data.setdefault("access_keys", [])
    keys_list[:] = [e for e in keys_list if e.get("AccessKeyId") != ak]
    keys_list.append(entry)

    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _maybe_create_access_key(
    iam,
    user_name: str,
    create: bool,
    force_new: bool,
    save_path: Path,
) -> Path | None:
    if not create:
        return None

    keys = iam.list_access_keys(UserName=user_name).get("AccessKeyMetadata", [])
    if len(keys) >= 2 and not force_new:
        print(
            "User already has 2 access keys; skipping create. "
            "Remove a key or pass --force-new-access-key (requires a free slot).",
            file=sys.stderr,
        )
        return None
    if keys and not force_new:
        print(
            f"User already has {len(keys)} access key(s); skipping. "
            "Pass --force-new-access-key to add a second key (max 2 per user).",
            file=sys.stderr,
        )
        return None
    if len(keys) >= 2 and force_new:
        print(
            "Cannot create a new access key: user already has 2. Delete one in IAM first.",
            file=sys.stderr,
        )
        return None

    resp = iam.create_access_key(UserName=user_name)["AccessKey"]
    out = Path(save_path).expanduser()
    out = out.resolve() if out.is_absolute() else (Path.cwd() / out).resolve()
    _save_access_key_to_file(out, user_name, resp)
    time.sleep(IAM_CREATE_PROPAGATION_SLEEP_SEC)
    print(f"\nAccess key created; credentials saved to {out} (contains SecretAccessKey; keep private).")
    print(f"AccessKeyId={resp['AccessKeyId']}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Provision IAM user, group, role, and managed policies for TT environment deploy "
            "(launcher + extensions-service)."
        )
    )
    parser.add_argument(
        "environment_name",
        help="Environment prefix (same as deploy_environment.py)",
    )
    parser.add_argument(
        "--aws-profile",
        required=True,
        help="Sysadmin (or sufficient IAM) profile used for all API calls",
    )
    parser.add_argument(
        "--aws-region",
        default="us-east-1",
        help="Region for STS/session (default: us-east-1)",
    )
    parser.add_argument(
        "--custom-username",
        default=None,
        help="Override IAM user name (default: <env>_deployment_tt_user)",
    )
    parser.add_argument(
        "--update-policies",
        action="store_true",
        help="Create a new default policy version when the generated document differs from AWS",
    )
    parser.add_argument(
        "--create-access-key",
        action="store_true",
        help="Create an access key when none exists, or add second key with --force-new-access-key",
    )
    parser.add_argument(
        "--force-new-access-key",
        action="store_true",
        help="Create another access key if fewer than 2 exist (requires --create-access-key)",
    )
    parser.add_argument(
        "--access-key-output",
        type=Path,
        default=None,
        help=(
            "JSON file for new access keys "
            "(default: bootstrap/state/<env>/<env>_deployment_tt_access_keys.json)"
        ),
    )

    args = parser.parse_args()
    env_name = args.environment_name.strip()
    if not env_name:
        print("environment_name must be non-empty.", file=sys.stderr)
        return 1

    user_name         = (args.custom_username or f"{env_name}_deployment_tt_user").strip()
    group_name        = f"{env_name}_deployment_tt_group"
    role_name         = f"{env_name}_deployment_tt_role"
    launcher_policy   = f"{env_name}_deployment_tt_policy"
    extensions_policy = f"{env_name}_deployment_ext_policy"

    account_id = _resolve_account_id(None, args.aws_profile, args.aws_region)
    if not account_id.isdigit() or len(account_id) != 12:
        print("Resolved account ID must be 12 digits.", file=sys.stderr)
        return 1

    deployment_role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    launcher_doc = _policy_document(
        env_name,
        args.aws_region,
        account_id,
        deployment_operator_role_arn=deployment_role_arn,
    )
    extensions_doc = _extensions_policy_document(env_name, args.aws_region, account_id)

    session = boto3.Session(profile_name=args.aws_profile, region_name=args.aws_region)
    iam = session.client("iam")

    print("\n==> Launcher policy")
    launcher_arn, _ = _ensure_customer_managed_policy(
        iam, account_id, launcher_policy, launcher_doc, update=args.update_policies
    )

    print("\n==> Extensions-service policy")
    extensions_arn, _ = _ensure_customer_managed_policy(
        iam, account_id, extensions_policy, extensions_doc, update=args.update_policies
    )

    policy_arns = [launcher_arn, extensions_arn]

    print("\n==> User")
    _ensure_user(iam, user_name)

    print("\n==> Group")
    _ensure_group(iam, group_name)

    print("\n==> Attach policies to user")
    for arn in policy_arns:
        _ensure_attach_user_policy(iam, user_name, arn)

    print("\n==> Attach policies to group")
    for arn in policy_arns:
        _ensure_attach_group_policy(iam, group_name, arn)

    print("\n==> Add user to group")
    _ensure_user_in_group(iam, user_name, group_name)

    print("\n==> Deployment role")
    role_arn = _ensure_deployment_role(
        iam, account_id, role_name, user_name, policy_arns
    )

    default_key_file = _BOOTSTRAP_ROOT / "state" / env_name / f"{env_name}_deployment_tt_access_keys.json"
    key_file = args.access_key_output if args.access_key_output is not None else default_key_file
    saved_keys_path = _maybe_create_access_key(
        iam,
        user_name,
        args.create_access_key,
        args.force_new_access_key,
        key_file,
    )

    print("\nSummary")
    print("-------")
    print(f"  User:              {user_name}")
    print(f"  Group:             {group_name}")
    print(f"  Role:              {role_arn}")
    print(f"  Policy (launcher): {launcher_arn}")
    print(f"  Policy (ext-svc):  {extensions_arn}")
    print(f"  Account:           {account_id}")
    if saved_keys_path:
        print(f"  Access keys:       {saved_keys_path}")
    print("\nUse this profile in ~/.aws/config with role_arn + source_profile, or use access keys on the user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
