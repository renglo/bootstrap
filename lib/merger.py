"""Merge launcher + extensions-service manifests into combined platform state.

Produces under platform-installer/state/<extension>/:
  - platform_resources.json         Combined resource inventory (launcher + ECS + handlers OIDC)
  - platform_vars.production.json   Merged VARS/SECRETS for GitHub environment production (launcher repo)
  - platform_vars.staging.json      Merged VARS/SECRETS for staging (if launcher wrote staging.json)
  - deploy_input.json               Handlers deploy + GitHub env (VARS/SECRETS, same shape as platform_vars)
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


def _normalize_tenant(tenant: str | None) -> str | None:
    if tenant is None:
        return None
    value = tenant.strip()
    if not value:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    if any(ch not in allowed for ch in value):
        raise ValueError(
            f"Invalid --tenant {tenant!r}: use letters, numbers, '-' and '_' only."
        )
    return value


def _github_environment_label(base_environment: str, tenant: str | None) -> str:
    """GitHub Environment name for bootstrap state JSON (optional tenant prefix)."""
    base = (base_environment or "production").strip() or "production"
    if not tenant:
        return base
    prefix = f"{tenant}_"
    if base.startswith(prefix):
        return base
    return f"{prefix}{base}"


def merge_manifests(
    extension: str,
    launcher_root: Path,
    extensions_service_root: Path,
    platform_installer_root: Path,
    aws_region: str,
    tenant: str | None = None,
) -> Path:
    """Merge launcher + extensions-service outputs into platform-installer state.

    When tenant is set, ENVIRONMENT in platform_vars.* becomes {tenant}_{environment}
    (e.g. acme_production). Does not alter launcher or extensions-service state.

    Returns the platform state directory path.
    """
    tenant = _normalize_tenant(tenant)
    launcher_state = launcher_root / "state" / extension
    ext_state = extensions_service_root / "state" / extension
    platform_state = platform_installer_root / "state" / extension
    platform_state.mkdir(parents=True, exist_ok=True)

    launcher_resources = _read_json(launcher_state / "created_resources.json") or {}
    ext_manifest = _read_json(ext_state / "provision_manifest.json") or {}
    handlers_oidc = _read_json(ext_state / "handlers_github_oidc.json")

    _write_platform_resources(platform_state, extension, aws_region, launcher_resources, ext_manifest, handlers_oidc)
    _write_all_platform_vars(platform_state, launcher_state, ext_manifest, tenant, launcher_resources)
    _write_deploy_input(
        platform_state,
        launcher_state,
        extension,
        ext_manifest,
        handlers_oidc,
        tenant,
        launcher_resources,
    )
    _write_env_config(
        platform_state,
        launcher_state / "env_config.py",
        ext_manifest,
        launcher_resources,
    )
    _remove_stale_alias_files(platform_state)

    print(f"\nPlatform state written to: {platform_state}")
    print("  - platform_resources.json")
    print("  - platform_vars.production.json")
    print("  - platform_vars.staging.json (if launcher/staging.json exists)")
    print("  - deploy_input.json")
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


def _remove_stale_alias_files(platform_state: Path) -> None:
    """Drop legacy files no longer written by merge."""
    for name in (
        "platform_vars.json",
        "handlers_vars.json",
        "handlers_vars.production.json",
        "handlers_vars.staging.json",
    ):
        path = platform_state / name
        if path.is_file():
            path.unlink()


def _normalize_websocket_urls(connections_url: str, websocket_url: str) -> dict[str, str]:
    """
    Canonical WEBSOCKET_* vars for JSON / Lambda env.
    WEBSOCKET_CONNECTIONS = https management API base; WEBSOCKET_URL / VITE_WEBSOCKET_URL = wss.
    """
    c = (connections_url or "").strip().rstrip("/")
    w = (websocket_url or "").strip().rstrip("/")
    if c.startswith("wss://"):
        w = w or c
        c = "https://" + c[len("wss://") :]
    elif c.startswith("https://") and not w:
        w = "wss://" + c[len("https://") :]
    if w.startswith("https://"):
        w = "wss://" + w[len("https://") :]
    out: dict[str, str] = {}
    if c:
        out["WEBSOCKET_CONNECTIONS"] = c
    if w:
        out["WEBSOCKET_URL"] = w
        out["VITE_WEBSOCKET_URL"] = w
    return out


def _websocket_vars_from_backend_stage(
    launcher_resources: dict[str, Any], stage: str
) -> dict[str, str]:
    """Read websocket URLs from launcher created_resources.json backend.<stage>."""
    backend = launcher_resources.get("backend") or {}
    stage_data = backend.get(stage) if isinstance(backend.get(stage), dict) else {}
    return _normalize_websocket_urls(
        str(stage_data.get("websocket_connections_url") or ""),
        str(stage_data.get("websocket_url") or ""),
    )


def _apply_websocket_to_vars(
    vars_dict: dict[str, str],
    launcher_resources: dict[str, Any] | None,
    stage: str,
) -> None:
    """Set WEBSOCKET_* on a VARS dict from created_resources (canonical https/wss)."""
    if not launcher_resources:
        return
    for key, value in _websocket_vars_from_backend_stage(launcher_resources, stage).items():
        vars_dict[key] = value


def _launcher_vars_for_stage(launcher_state: Path, stage: str) -> dict[str, str]:
    """VARS block from launcher/state/<ext>/production.json or staging.json."""
    launcher_file = "production.json" if stage == "production" else "staging.json"
    launcher_json = _read_json(launcher_state / launcher_file)
    if not launcher_json and stage == "staging":
        launcher_json = _read_json(launcher_state / "production.json")
    if not launcher_json:
        return {}
    raw = launcher_json.get("VARS") or {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if value is not None and str(value).strip() != ""
    }


def _platform_vars_for_handlers(launcher_vars: dict[str, str]) -> dict[str, str]:
    """Subset of launcher VARS shared with handlers repo (DynamoDB, Cognito, S3, runtime role)."""
    out: dict[str, str] = {}
    for key, value in launcher_vars.items():
        if key.startswith("DYNAMODB_") or key.startswith("COGNITO_"):
            out[key] = value
    if launcher_vars.get("S3_BUCKET_NAME"):
        out["S3_BUCKET_NAME"] = launcher_vars["S3_BUCKET_NAME"]
    for key in ("WL_NAME", "AWS_REGION", "ROLE_ARN"):
        if launcher_vars.get(key):
            out[key] = launcher_vars[key]
    return out


def _ecs_vars_subset(extension: str, ext_manifest: dict[str, Any]) -> dict[str, str]:
    """ECS + handlers Lambda ARN from provision_manifest (extensions-service)."""
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
    handlers_lambda = ext_manifest.get("lambda") or {}
    handlers_arn = handlers_lambda.get("LAMBDA_EXTERNAL_HANDLERS_ARN")
    if handlers_arn:
        launcher_vars["LAMBDA_EXTERNAL_HANDLERS_ARN"] = str(handlers_arn)
    return launcher_vars


def _launcher_secrets_for_stage(launcher_state: Path, stage: str) -> dict[str, str]:
    """SECRETS block from launcher/state/<ext>/production.json or staging.json."""
    launcher_file = "production.json" if stage == "production" else "staging.json"
    launcher_json = _read_json(launcher_state / launcher_file)
    if not launcher_json and stage == "staging":
        launcher_json = _read_json(launcher_state / "production.json")
    if not launcher_json:
        return {}
    raw = launcher_json.get("SECRETS") or {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if value is not None and str(value).strip() != ""
    }


def _deploy_input_payload(
    launcher_state: Path,
    ext_manifest: dict[str, Any],
    extension: str,
    handlers_oidc: dict[str, Any] | None,
    tenant: str | None,
    launcher_resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build deploy_input.json for extensions-service stage 2 and handlers GitHub Environment.

    Shape matches platform_vars (GITHUB_REPOSITORY, ENVIRONMENT, VARS, SECRETS).
    Deploy scripts merge VARS + SECRETS into Lambda/ECS runtime env (excluding RUNTIME_ENV_EXCLUDE).
    """
    ecs_vars = _ecs_vars_subset(extension, ext_manifest)

    launcher_vars = _launcher_vars_for_stage(launcher_state, "production")
    _apply_websocket_to_vars(launcher_vars, launcher_resources, "production")
    handler_vars = _platform_vars_for_handlers(launcher_vars)

    vars_payload: dict[str, str] = {**ecs_vars, **handler_vars}
    for key in ("WEBSOCKET_CONNECTIONS", "WEBSOCKET_URL", "VITE_WEBSOCKET_URL"):
        if launcher_vars.get(key):
            vars_payload[key] = launcher_vars[key]

    function_name: str = (
        (ext_manifest.get("lambda") or {}).get("function_name")
        or f"{extension}-handlers"
    )
    vars_payload["LAMBDA_HANDLERS_FUNCTION_NAME"] = function_name

    ecr_image_uri: str = str((ext_manifest.get("ecr") or {}).get("image_uri") or "")
    if ecr_image_uri:
        vars_payload["ECR_IMAGE_URI"] = ecr_image_uri

    secrets: dict[str, str] = {}
    if handlers_oidc and handlers_oidc.get("role_arn_production"):
        secrets["AWS_GITHUB_OIDC_ROLE_ARN"] = str(handlers_oidc["role_arn_production"])

    launcher_secrets = _launcher_secrets_for_stage(launcher_state, "production")
    launcher_secrets.pop("AWS_GITHUB_OIDC_ROLE_ARN", None)
    secrets.update(launcher_secrets)

    gh_repo = ""
    if handlers_oidc:
        gh_repo = str(handlers_oidc.get("github_repo") or "")
    if not gh_repo:
        launcher_json = _read_json(launcher_state / "production.json") or {}
        gh_repo = str(launcher_json.get("GITHUB_REPOSITORY") or "")

    return {
        "GITHUB_REPOSITORY": gh_repo,
        "ENVIRONMENT": _github_environment_label("production", tenant),
        "VARS": vars_payload,
        "SECRETS": secrets,
    }


def _write_deploy_input(
    platform_state: Path,
    launcher_state: Path,
    extension: str,
    ext_manifest: dict[str, Any],
    handlers_oidc: dict[str, Any] | None,
    tenant: str | None,
    launcher_resources: dict[str, Any] | None = None,
) -> None:
    payload = _deploy_input_payload(
        launcher_state,
        ext_manifest,
        extension,
        handlers_oidc,
        tenant,
        launcher_resources,
    )
    (platform_state / "deploy_input.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


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
            "lambda": ext_manifest.get("lambda", {}),
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
    tenant: str | None,
    launcher_resources: dict[str, Any] | None = None,
    stage_name: str | None = None,
) -> dict[str, Any]:
    """Merge one launcher environment JSON (production or staging) with ECS from provision_manifest."""
    launcher_vars: dict[str, str] = dict(launcher_env_json.get("VARS") or {})
    stage = stage_name or str(launcher_env_json.get("ENVIRONMENT") or "production").strip() or "production"
    if stage.startswith("staging") or stage.endswith("_staging"):
        stage_key = "staging"
    elif stage == "production" or stage.endswith("_production"):
        stage_key = "production"
    else:
        stage_key = stage
    _apply_websocket_to_vars(launcher_vars, launcher_resources, stage_key)
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
    handlers_lambda = ext_manifest.get("lambda") or {}
    handlers_arn = handlers_lambda.get("LAMBDA_EXTERNAL_HANDLERS_ARN")
    if handlers_arn:
        launcher_vars["LAMBDA_EXTERNAL_HANDLERS_ARN"] = str(handlers_arn)

    env_label = str(launcher_env_json.get("ENVIRONMENT") or "").strip() or "production"

    return {
        "GITHUB_REPOSITORY": str(launcher_env_json.get("GITHUB_REPOSITORY", "") or ""),
        "ENVIRONMENT": _github_environment_label(env_label, tenant),
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
    tenant: str | None,
    launcher_resources: dict[str, Any] | None = None,
) -> None:
    """Write platform_vars.<stage>.json for each launcher environment file present."""
    pairs: list[tuple[str, str, str]] = [
        ("production.json", "platform_vars.production.json", "production"),
        ("staging.json", "platform_vars.staging.json", "staging"),
    ]
    for launcher_name, out_name, stage_key in pairs:
        launcher_json = _read_json(launcher_state / launcher_name)
        if not launcher_json:
            continue
        payload = _build_platform_vars_payload(
            launcher_json,
            ext_manifest,
            tenant,
            launcher_resources,
            stage_key,
        )
        (platform_state / out_name).write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )


def _strip_websocket_lines_from_env_config(content: str) -> str:
    """Remove WEBSOCKET_* / VITE_WEBSOCKET_* lines so merge can rewrite from created_resources."""
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(
            (
                "WEBSOCKET_CONNECTIONS",
                "WEBSOCKET_URL",
                "VITE_WEBSOCKET_URL",
            )
        ):
            continue
        lines.append(line)
    return "\n".join(lines).rstrip()


def _websocket_env_config_lines(launcher_resources: dict[str, Any]) -> list[str]:
    """Production + staging websocket constants for bootstrap env_config.py."""
    lines: list[str] = []
    prod = _websocket_vars_from_backend_stage(launcher_resources, "production")
    if prod:
        lines.append("")
        lines.append(f"WEBSOCKET_CONNECTIONS = {prod['WEBSOCKET_CONNECTIONS']!r}")
        lines.append(f"WEBSOCKET_URL = {prod['WEBSOCKET_URL']!r}")
        lines.append(f"VITE_WEBSOCKET_URL = {prod['VITE_WEBSOCKET_URL']!r}")
    stg = _websocket_vars_from_backend_stage(launcher_resources, "staging")
    if stg:
        lines.append(f"WEBSOCKET_CONNECTIONS_STAGING = {stg['WEBSOCKET_CONNECTIONS']!r}")
        lines.append(f"WEBSOCKET_URL_STAGING = {stg['WEBSOCKET_URL']!r}")
        lines.append(f"VITE_WEBSOCKET_URL_STAGING = {stg['VITE_WEBSOCKET_URL']!r}")
    return lines


def _write_env_config(
    platform_state: Path,
    launcher_env_config: Path,
    ext_manifest: dict[str, Any],
    launcher_resources: dict[str, Any] | None = None,
) -> Path:
    """Write env_config.py with launcher base + websocket (from created_resources) + ECS appended."""
    out_path = platform_state / "env_config.py"

    base_content = ""
    if launcher_env_config.is_file():
        base_content = _strip_websocket_lines_from_env_config(
            launcher_env_config.read_text(encoding="utf-8")
        )

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

    handlers_lambda = ext_manifest.get("lambda") or {}
    handlers_arn = handlers_lambda.get("LAMBDA_EXTERNAL_HANDLERS_ARN")
    if handlers_arn:
        ecs_lines.append(f"LAMBDA_EXTERNAL_HANDLERS_ARN = {repr(handlers_arn)}")

    ws_lines: list[str] = []
    if launcher_resources:
        ws_lines = _websocket_env_config_lines(launcher_resources)

    parts = [p for p in (base_content, "\n".join(ws_lines), "\n".join(ecs_lines)) if p]
    content = "\n".join(parts) + "\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path
