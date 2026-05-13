"""Merge launcher + extensions-service manifests into combined platform state.

Produces under platform-installer/state/<extension>/:
  - platform_resources.json         Combined resource inventory (launcher + ECS + handlers OIDC)
  - platform_vars.production.json   Merged VARS/SECRETS for GitHub environment production
  - platform_vars.staging.json      Merged VARS/SECRETS for staging (if launcher wrote staging.json)
  - platform_vars.json              Same as production (backward compatibility)
  - handlers_vars.production.json   Handlers-repo ECS vars + OIDC role (if handlers_github_oidc.json exists)
  - handlers_vars.staging.json      Same for staging OIDC role when present
  - handlers_vars.json              Alias of handlers production
  - env_config.py                   Extended env_config with ECS constants appended
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return None
    return data


def merge_manifests(
    extension: str,
    launcher_root: Path,
    extensions_service_root: Path,
    platform_installer_root: Path,
    aws_region: str,
) -> Path:
    """Merge launcher + extensions-service outputs into platform-installer state.

    Returns the platform state directory path.
    """
    launcher_state = launcher_root / "state" / extension
    ext_state = extensions_service_root / "state" / extension
    platform_state = platform_installer_root / "state" / extension
    platform_state.mkdir(parents=True, exist_ok=True)

    launcher_resources = _read_json(launcher_state / "created_resources.json") or {}
    ext_manifest = _read_json(ext_state / "provision_manifest.json") or {}
    handlers_oidc = _read_json(ext_state / "handlers_github_oidc.json")

    _write_platform_resources(platform_state, extension, aws_region, launcher_resources, ext_manifest, handlers_oidc)
    _write_all_platform_vars(platform_state, launcher_state, ext_manifest)
    _write_handlers_vars(platform_state, extension, ext_manifest, handlers_oidc)
    _write_env_config(platform_state, launcher_state / "env_config.py", ext_manifest)

    print(f"\nPlatform state written to: {platform_state}")
    print("  - platform_resources.json")
    print("  - platform_vars.production.json")
    print("  - platform_vars.staging.json (if launcher/staging.json exists)")
    print("  - platform_vars.json (alias of production)")
    print("  - handlers_vars*.json (if extensions-service state has handlers_github_oidc.json)")
    print("  - env_config.py")

    return platform_state


def _handlers_github_oidc_block(handlers_oidc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not handlers_oidc or not handlers_oidc.get("role_arn_production"):
        return None
    block: dict[str, Any] = {
        "github_repo": handlers_oidc.get("github_repo"),
        "oidc_provider_arn": handlers_oidc.get("oidc_provider_arn"),
        "ecs_results_bucket": handlers_oidc.get("ecs_results_bucket"),
        "production": {
            "role_name": handlers_oidc.get("role_name_production"),
            "role_arn": handlers_oidc.get("role_arn_production"),
            "policy_name": handlers_oidc.get("policy_name_production"),
        },
    }
    if handlers_oidc.get("role_arn_staging"):
        block["staging"] = {
            "role_name": handlers_oidc.get("role_name_staging"),
            "role_arn": handlers_oidc.get("role_arn_staging"),
            "policy_name": handlers_oidc.get("policy_name_staging"),
        }
    return block


def _ecs_vars_subset(extension: str, ext_manifest: dict[str, Any]) -> dict[str, str]:
    """ECS-related VARS for handlers workflows (no launcher API/Cognito keys)."""
    launcher_vars: dict[str, str] = {"WL_NAME": extension}
    ecs = ext_manifest.get("ecs") or {}
    buckets = ext_manifest.get("buckets") or {}
    if ext_manifest.get("aws_region"):
        launcher_vars["AWS_REGION"] = str(ext_manifest["aws_region"])
    if ecs.get("cluster"):
        launcher_vars["ECS_CLUSTER"] = str(ecs["cluster"])
    if ecs.get("task_definition"):
        launcher_vars["ECS_TASK_DEFINITION"] = str(ecs["task_definition"])
    if ecs.get("launch_type"):
        launcher_vars["ECS_LAUNCH_TYPE"] = str(ecs["launch_type"])
    if ecs.get("network_mode"):
        launcher_vars["ECS_NETWORK_MODE"] = str(ecs["network_mode"])
    subnets = ecs.get("subnets") or []
    if subnets:
        launcher_vars["ECS_SUBNETS"] = ",".join(subnets) if isinstance(subnets, list) else str(subnets)
    sgs = ecs.get("security_groups") or []
    if sgs:
        launcher_vars["ECS_SECURITY_GROUPS"] = ",".join(sgs) if isinstance(sgs, list) else str(sgs)
    if buckets.get("ecs_results_bucket"):
        launcher_vars["ECS_RESULTS_BUCKET"] = str(buckets["ecs_results_bucket"])
    return launcher_vars


def _handlers_vars_payload(
    handlers_oidc: dict[str, Any],
    ext_manifest: dict[str, Any],
    extension: str,
    environment: str,
) -> dict[str, Any] | None:
    if environment == "production":
        role_arn = str(handlers_oidc.get("role_arn_production") or "")
    else:
        role_arn = str(handlers_oidc.get("role_arn_staging") or "")
    if not role_arn:
        return None
    gh_repo = str(handlers_oidc.get("github_repo") or "")
    return {
        "GITHUB_REPOSITORY": gh_repo,
        "ENVIRONMENT": environment,
        "VARS": _ecs_vars_subset(extension, ext_manifest),
        "SECRETS": {"AWS_GITHUB_OIDC_ROLE_ARN": role_arn},
    }


def _write_handlers_vars(
    platform_state: Path,
    extension: str,
    ext_manifest: dict[str, Any],
    handlers_oidc: dict[str, Any] | None,
) -> None:
    if not handlers_oidc:
        return
    production_text: str | None = None
    prod = _handlers_vars_payload(handlers_oidc, ext_manifest, extension, "production")
    if prod:
        production_text = json.dumps(prod, indent=2) + "\n"
        (platform_state / "handlers_vars.production.json").write_text(production_text, encoding="utf-8")

    staging = _handlers_vars_payload(handlers_oidc, ext_manifest, extension, "staging")
    if staging:
        (platform_state / "handlers_vars.staging.json").write_text(
            json.dumps(staging, indent=2) + "\n", encoding="utf-8"
        )

    if production_text is not None:
        (platform_state / "handlers_vars.json").write_text(production_text, encoding="utf-8")


def _write_platform_resources(
    platform_state: Path,
    extension: str,
    aws_region: str,
    launcher_resources: dict[str, Any],
    ext_manifest: dict[str, Any],
    handlers_oidc: dict[str, Any] | None,
) -> Path:
    """Write combined resource inventory as structured JSON."""
    out_path = platform_state / "platform_resources.json"

    ext_iam: dict[str, Any] = {}
    ecs = ext_manifest.get("ecs") or {}
    if ecs.get("task_definition"):
        ext_iam["task_role"] = f"{extension}-handlers-ecs-task"
        ext_iam["execution_role"] = f"{extension}-handlers-ecs-execution"
        ext_iam["handlers_policy"] = f"{extension.capitalize()}HandlersPolicy"

    payload: dict[str, Any] = {
        "environment": extension,
        "aws_region": launcher_resources.get("aws_region") or ext_manifest.get("aws_region") or aws_region,
        "updated_at": _utc_now_iso(),
        "launcher": {
            "dynamodb": launcher_resources.get("dynamodb", {}),
            "cognito": launcher_resources.get("cognito", {}),
            "iam": launcher_resources.get("iam", {}),
            "s3": launcher_resources.get("s3", {}),
            "backend": launcher_resources.get("backend", {}),
            "github_oidc": launcher_resources.get("github_oidc", {}),
        },
        "extensions_service": {
            "ecr": ext_manifest.get("ecr", {}),
            "ecs": ext_manifest.get("ecs", {}),
            "buckets": ext_manifest.get("buckets", {}),
            "iam": ext_iam,
            "github_oidc": _handlers_github_oidc_block(handlers_oidc),
        },
    }

    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out_path


def _build_platform_vars_payload(
    launcher_env_json: dict[str, Any],
    ext_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Merge one launcher environment JSON (production or staging) with ECS from provision_manifest."""
    launcher_vars: dict[str, str] = dict(launcher_env_json.get("VARS") or {})
    launcher_secrets: dict[str, str] = dict(launcher_env_json.get("SECRETS") or {})

    ecs = ext_manifest.get("ecs") or {}
    buckets = ext_manifest.get("buckets") or {}

    if ecs.get("cluster"):
        launcher_vars["ECS_CLUSTER"] = str(ecs["cluster"])
    if ecs.get("task_definition"):
        launcher_vars["ECS_TASK_DEFINITION"] = str(ecs["task_definition"])
    if ecs.get("launch_type"):
        launcher_vars["ECS_LAUNCH_TYPE"] = str(ecs["launch_type"])
    if ecs.get("network_mode"):
        launcher_vars["ECS_NETWORK_MODE"] = str(ecs["network_mode"])
    subnets = ecs.get("subnets") or []
    if subnets:
        launcher_vars["ECS_SUBNETS"] = ",".join(subnets) if isinstance(subnets, list) else str(subnets)
    sgs = ecs.get("security_groups") or []
    if sgs:
        launcher_vars["ECS_SECURITY_GROUPS"] = ",".join(sgs) if isinstance(sgs, list) else str(sgs)
    if buckets.get("ecs_results_bucket"):
        launcher_vars["ECS_RESULTS_BUCKET"] = str(buckets["ecs_results_bucket"])

    env_label = str(launcher_env_json.get("ENVIRONMENT") or "").strip() or "production"

    return {
        "GITHUB_REPOSITORY": str(launcher_env_json.get("GITHUB_REPOSITORY", "") or ""),
        "ENVIRONMENT": env_label,
        "VARS": launcher_vars,
        "SECRETS": {
            k: v
            for k, v in launcher_secrets.items()
            if k not in {"OPENAI_API_KEY"}
        },
    }


def _write_all_platform_vars(
    platform_state: Path,
    launcher_state: Path,
    ext_manifest: dict[str, Any],
) -> None:
    """Write platform_vars.<stage>.json for each launcher environment file present."""
    pairs: list[tuple[str, str]] = [
        ("production.json", "platform_vars.production.json"),
        ("staging.json", "platform_vars.staging.json"),
    ]
    production_text: str | None = None
    for launcher_name, out_name in pairs:
        launcher_json = _read_json(launcher_state / launcher_name)
        if not launcher_json:
            continue
        payload = _build_platform_vars_payload(launcher_json, ext_manifest)
        text = json.dumps(payload, indent=2) + "\n"
        (platform_state / out_name).write_text(text, encoding="utf-8")
        if launcher_name == "production.json":
            production_text = text

    if production_text is not None:
        (platform_state / "platform_vars.json").write_text(production_text, encoding="utf-8")


def _write_env_config(
    platform_state: Path,
    launcher_env_config: Path,
    ext_manifest: dict[str, Any],
) -> Path:
    """Write env_config.py with launcher base + ECS constants appended."""
    out_path = platform_state / "env_config.py"

    base_content = ""
    if launcher_env_config.is_file():
        base_content = launcher_env_config.read_text(encoding="utf-8").rstrip()

    ecs = ext_manifest.get("ecs") or {}
    buckets = ext_manifest.get("buckets") or {}

    ecs_lines: list[str] = [
        "",
        "# ECS extensions-service configuration (auto-generated by platform-installer)",
    ]
    if ecs.get("cluster"):
        ecs_lines.append(f"ECS_CLUSTER = {repr(ecs['cluster'])}")
    if ecs.get("task_definition"):
        ecs_lines.append(f"ECS_TASK_DEFINITION = {repr(ecs['task_definition'])}")
    if buckets.get("ecs_results_bucket"):
        ecs_lines.append(f"ECS_RESULTS_BUCKET = {repr(buckets['ecs_results_bucket'])}")
    if ecs.get("launch_type"):
        ecs_lines.append(f"ECS_LAUNCH_TYPE = {repr(ecs['launch_type'])}")
    if ecs.get("network_mode"):
        ecs_lines.append(f"ECS_NETWORK_MODE = {repr(ecs['network_mode'])}")
    subnets = ecs.get("subnets") or []
    if subnets:
        subnets_str = ",".join(subnets) if isinstance(subnets, list) else subnets
        ecs_lines.append(f"ECS_SUBNETS = {repr(subnets_str)}")
    sgs = ecs.get("security_groups") or []
    if sgs:
        sgs_str = ",".join(sgs) if isinstance(sgs, list) else sgs
        ecs_lines.append(f"ECS_SECURITY_GROUPS = {repr(sgs_str)}")

    content = base_content + "\n".join(ecs_lines) + "\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path
