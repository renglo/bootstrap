#!/usr/bin/env python3
"""
Inject GitHub environment variables and secrets from a JSON file.

Expected JSON shape:
{
  "GITHUB_REPOSITORY": "owner/repo",
  "ENVIRONMENT": "production",
  "VARS": {
    "KEY": "value"
  },
  "SECRETS": {
    "SECRET_KEY": "secret-value"
  }
}

Notes:
- Target repo: top-level "GITHUB_REPOSITORY" (or "REPO") unless --repo overrides.
- GitHub environment name: use --environment, or optional top-level "ENVIRONMENT" / "ENVIROMENT"
  (legacy), or if the JSON path is production.json / staging.json the stem is used.
- A GitHub variable named ENVIRONMENT is always set: from top-level "ENVIRONMENT" / "ENVIROMENT"
  if present, otherwise the resolved GitHub environment name (so workflows can read it).
- If the environment does not exist, it is created via GitHub API.
- VARS and SECRETS are dynamic maps; key lists are not fixed.
- Variables and secrets in GitHub that are absent from JSON are deleted; present keys are
  created or updated so value changes take effect on the remote. ENVIRONMENT is merged into
  the synced variables (top-level value, else VARS.ENVIRONMENT, else GitHub environment name).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


def _run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def _gh_json_array(args: list[str]) -> list[Any]:
    proc = _run_cmd(args)
    text = proc.stdout.strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise SystemExit(f"Expected JSON array from {' '.join(args[:4])}...")
    return data


def _list_environment_variable_names(repo: str, environment: str) -> list[str]:
    rows = _gh_json_array(
        [
            "gh",
            "variable",
            "list",
            "--repo",
            repo,
            "--env",
            environment,
            "--json",
            "name",
        ]
    )
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            names.append(row["name"])
    return names


def _list_environment_secret_names(repo: str, environment: str) -> list[str]:
    rows = _gh_json_array(
        [
            "gh",
            "secret",
            "list",
            "--repo",
            repo,
            "--env",
            environment,
            "--json",
            "name",
        ]
    )
    names: list[str] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            names.append(row["name"])
    return names


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"JSON file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Top-level JSON must be an object.")
    return payload


def _validate_key_values(name: str, value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SystemExit(f"'{name}' must be an object of string keys and values.")
    out: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not k.strip():
            raise SystemExit(f"'{name}' contains an empty or non-string key.")
        out[k.strip()] = "" if v is None else str(v)
    return out


def _resolve_environment_name(
    payload: dict[str, Any],
    *,
    json_path: Path,
    cli_environment: str | None,
) -> str:
    if cli_environment is not None and cli_environment.strip():
        return cli_environment.strip()

    env_name = payload.get("ENVIRONMENT")
    if not env_name:
        env_name = payload.get("ENVIROMENT")
    if isinstance(env_name, str) and env_name.strip():
        return env_name.strip()

    stem = json_path.resolve().stem
    if stem in ("production", "staging"):
        return stem

    raise SystemExit(
        "GitHub environment name missing: pass --environment NAME, "
        'or put "ENVIRONMENT" in the JSON (legacy), '
        "or name the JSON file production.json / staging.json."
    )


def _resolve_repository(
    payload: dict[str, Any],
    *,
    cli_repo: str | None,
) -> str:
    if cli_repo is not None and cli_repo.strip():
        return cli_repo.strip()
    for key in ("GITHUB_REPOSITORY", "REPO"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    raise SystemExit(
        "GitHub repository missing: pass --repo OWNER/REPO "
        'or set top-level "GITHUB_REPOSITORY" (or "REPO") in the JSON.'
    )


def _environment_variable_value_for_github(
    payload: dict[str, Any],
    resolved_github_environment: str,
    vars_from_payload: dict[str, str],
) -> str:
    for key in ("ENVIRONMENT", "ENVIROMENT"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    v = vars_from_payload.get("ENVIRONMENT")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return resolved_github_environment


def _ensure_gh_available() -> None:
    gh = shutil.which("gh") or shutil.which("gh.exe") or shutil.which("gh.cmd")
    if not gh:
        raise SystemExit("GitHub CLI 'gh' was not found in PATH.")


def _ensure_auth() -> None:
    try:
        _run_cmd(["gh", "auth", "status"])
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        msg = "GitHub CLI is not authenticated. Run: gh auth login"
        if stderr:
            msg = f"{msg}\n{stderr}"
        raise SystemExit(msg) from exc


def _create_environment(repo: str, environment: str) -> None:
    _run_cmd(
        [
            "gh",
            "api",
            "--method",
            "PUT",
            f"repos/{repo}/environments/{environment}",
        ]
    )


def _set_variable(repo: str, environment: str, key: str, value: str) -> None:
    _run_cmd(
        [
            "gh",
            "variable",
            "set",
            key,
            "--repo",
            repo,
            "--env",
            environment,
            "--body",
            value,
        ]
    )


def _set_secret(repo: str, environment: str, key: str, value: str) -> None:
    _run_cmd(
        [
            "gh",
            "secret",
            "set",
            key,
            "--repo",
            repo,
            "--env",
            environment,
            "--body",
            value,
        ]
    )


def _delete_variable(repo: str, environment: str, key: str) -> None:
    _run_cmd(
        [
            "gh",
            "variable",
            "delete",
            key,
            "--repo",
            repo,
            "--env",
            environment,
        ]
    )


def _delete_secret(repo: str, environment: str, key: str) -> None:
    _run_cmd(
        [
            "gh",
            "secret",
            "delete",
            key,
            "--repo",
            repo,
            "--env",
            environment,
        ]
    )


def _sync_github_keys(
    kind: str,
    repo: str,
    environment: str,
    desired: dict[str, str],
    remote_names: list[str],
    delete_fn: Callable[[str, str, str], None],
    set_fn: Callable[[str, str, str, str], None],
) -> None:
    desired_keys = set(desired)
    stale = sorted(set(remote_names) - desired_keys)
    if stale:
        print(f"Removing {len(stale)} stale {kind}(s) not in JSON...")
        for key in stale:
            delete_fn(repo, environment, key)
            print(f"  - {kind} removed: {key}")
    if desired:
        print(f"Setting {len(desired)} {kind}(s)...")
        for key, value in desired.items():
            set_fn(repo, environment, key, value)
            print(f"  - {kind} set: {key}")
    elif not stale:
        print(f"No {kind}s in JSON.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create/sync a GitHub Environment: inject VARS/SECRETS from JSON, "
        "update changed values, and remove keys no longer listed."
    )
    parser.add_argument(
        "--repo",
        default=None,
        help=(
            "GitHub repository OWNER/REPO. If omitted, uses GITHUB_REPOSITORY or REPO from the JSON payload."
        ),
    )
    parser.add_argument(
        "--json",
        type=Path,
        required=True,
        help=(
            "Path to JSON payload (e.g. bootstrap/state/<extension>/production.json from install, "
            "or launcher/state/<env>/staging.json)."
        ),
    )
    parser.add_argument(
        "--environment",
        "-e",
        default=None,
        help=(
            "GitHub Environment name (e.g. production, staging). "
            "If omitted, uses ENVIRONMENT in JSON or production.json/staging.json filename stem."
        ),
    )
    args = parser.parse_args()

    _ensure_gh_available()
    _ensure_auth()

    payload = _load_payload(args.json)
    repo = _resolve_repository(payload, cli_repo=args.repo)
    environment = _resolve_environment_name(
        payload,
        json_path=args.json,
        cli_environment=args.environment,
    )
    variables = dict(_validate_key_values("VARS", payload.get("VARS")))
    variables["ENVIRONMENT"] = _environment_variable_value_for_github(payload, environment, variables)
    secrets = _validate_key_values("SECRETS", payload.get("SECRETS"))

    print(f"Ensuring GitHub environment '{environment}' in '{repo}'...")
    _create_environment(repo, environment)

    remote_var_names = _list_environment_variable_names(repo, environment)
    _sync_github_keys(
        "VAR",
        repo,
        environment,
        variables,
        remote_var_names,
        _delete_variable,
        _set_variable,
    )

    remote_secret_names = _list_environment_secret_names(repo, environment)
    _sync_github_keys(
        "SECRET",
        repo,
        environment,
        secrets,
        remote_secret_names,
        _delete_secret,
        _set_secret,
    )

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())