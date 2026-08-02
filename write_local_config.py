"""Generate local developer config files from bootstrap SSM platform-vars.

Run after write-state (stack-b deployed):

    python bootstrap/install.py write-local-config \\
        --env-name stanley0731 \\
        --aws-profile maker \\
        --aws-region us-east-1

Output (default): bootstrap/output/<env>/local-dev/
  env_config.py       → copy to dev/renglo-api/
  .env.development    → copy to console/
  run.sh              → copy to dev/renglo-api/
  README.md           → handoff notes for admins → developers
  manifest.json       → generation metadata

Re-run after infrastructure or SSM changes to refresh values for developers.
"""

from __future__ import annotations

import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BOOTSTRAP_DIR = Path(__file__).resolve().parent
_WORKSPACE_ROOT = _BOOTSTRAP_DIR.parent

if str(_BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_DIR))

from helpers.merge_bootstrap_ssm import fetch_platform_vars  # noqa: E402

_DEFAULT_INVITE_FE = "http://127.0.0.1:5174"
_DEFAULT_API_LOCAL = "http://127.0.0.1:5001"
_DEFAULT_WS_LOCAL = "ws://127.0.0.1:8080"
_DEFAULT_WSS_BACKEND = "http://127.0.0.1:8080/send_to_client"
_DEFAULT_EXTENSIONS = "schd,data,pes"


def _env_config_str(value: Any) -> str:
    """Python string literal with single quotes (matches env_config.py.TEMPLATE style)."""
    return repr(str(value if value is not None else ""))


def _py_str(value: Any) -> str:
    return json.dumps(str(value if value is not None else ""))


def _existing_secrets(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for key in ("SECRET_KEY", "CSRF_SESSION_KEY"):
        prefix = f"{key} = "
        for line in text.splitlines():
            if line.startswith(prefix):
                raw = line[len(prefix) :].strip()
                if raw.startswith(("'", '"')):
                    out[key] = raw.strip("'\"")
                break
    return out


def _resolve_external_handlers(vars_block: dict[str, str]) -> str:
    """Return comma-separated extension names with external handlers, not the env name."""
    raw = str(vars_block.get("EXTERNAL_HANDLERS") or "").strip()
    wl_name = str(vars_block.get("WL_NAME") or "").strip()
    if raw.lower() == wl_name.lower():
        return ""
    return raw


def _render_env_config(
    vars_block: dict[str, str],
    *,
    invite_fe_base_url: str,
    wss_backend_url: str,
    secret_key: str,
    csrf_session_key: str,
) -> str:
    def v(key: str, default: str = "") -> str:
        return str(vars_block.get(key) or default)

    doc_base = v("DOC_BASE_URL") or v("FE_BASE_URL")
    cloud_ws_connections = v("WEBSOCKET_CONNECTIONS")
    lines = [
        f"WL_NAME = {_env_config_str(v('WL_NAME'))}",
        "",
        f"BASE_URL = {_env_config_str(v('BASE_URL'))}",
        "# Cloud Amplify console URL (from SSM). Production invite links when INVITE_FE_BASE_URL is unset.",
        f"FE_BASE_URL = {_env_config_str(v('FE_BASE_URL'))}",
        "# Local console URL for team-invite email links. Match console/vite.config.ts server.port.",
        f"INVITE_FE_BASE_URL = {_env_config_str(invite_fe_base_url)}",
        f"DOC_BASE_URL = {_env_config_str(doc_base)}",
        f"FROM_EMAIL = {_env_config_str(v('FROM_EMAIL'))}",
        f"AWS_REGION = {_env_config_str(v('AWS_REGION'))}",
        "",
        "# Crontab/Cronjob",
        f"API_GATEWAY_ARN = {_env_config_str(v('API_GATEWAY_ARN'))}",
        f"ROLE_ARN = {_env_config_str(v('ROLE_ARN'))}",
        "SYS_ENV = 'development'",
        "",
        "# DynamoDB",
        f"DYNAMODB_ENTITY_TABLE = {_env_config_str(v('DYNAMODB_ENTITY_TABLE'))}",
        f"DYNAMODB_BLUEPRINT_TABLE = {_env_config_str(v('DYNAMODB_BLUEPRINT_TABLE'))}",
        f"DYNAMODB_RINGDATA_TABLE = {_env_config_str(v('DYNAMODB_RINGDATA_TABLE'))}",
        f"DYNAMODB_REL_TABLE = {_env_config_str(v('DYNAMODB_REL_TABLE'))}",
        f"DYNAMODB_CHAT_TABLE = {_env_config_str(v('DYNAMODB_CHAT_TABLE'))}",
        f"DYNAMODB_GRAPH_TABLE = {_env_config_str(v('DYNAMODB_GRAPH_TABLE'))}",
        f"DYNAMODB_SEARCH_TABLE = {_env_config_str(v('DYNAMODB_SEARCH_TABLE'))}",
        f"DYNAMODB_SESSION_TABLE = {_env_config_str(v('DYNAMODB_SESSION_TABLE'))}",
        "",
        "# GraphDB",
        "GRAPH_DB_ENABLED = True",
        "",
        f"CSRF_SESSION_KEY = {_env_config_str(csrf_session_key)}",
        f"SECRET_KEY = {_env_config_str(secret_key)}",
        "",
        "# flask_cognito",
        f"COGNITO_REGION = {_env_config_str(v('COGNITO_REGION'))}",
        f"COGNITO_USERPOOL_ID = {_env_config_str(v('COGNITO_USERPOOL_ID'))}",
        f"COGNITO_APP_CLIENT_ID = {_env_config_str(v('COGNITO_APP_CLIENT_ID'))}",
        "COGNITO_CHECK_TOKEN_EXPIRATION = True",
        "",
        "# UI",
        "PREVIEW_LAYER = 2",
        "",
        "#-----------",
        "",
        f"S3_BUCKET_NAME = {_env_config_str(v('S3_BUCKET_NAME'))}",
        "",
        "# OPEN AI — set locally if needed (not stored in SSM)",
        "OPENAI_API_KEY = ''",
        "",
        "# WEB SOCKET — local WSS (dev/wss). Uncomment cloud line to test against API Gateway instead.",
        f"# WEBSOCKET_CONNECTIONS = {_env_config_str(cloud_ws_connections)}",
        f"WEBSOCKET_CONNECTIONS = {_env_config_str(wss_backend_url)}",
        "",
        "ALLOW_DEV_ORIGINS = True",
        "",
        "# Comma-separated extension names with external Lambda/ECS handlers (e.g. pes). Empty = in-process.",
        f"EXTERNAL_HANDLERS = {_env_config_str(_resolve_external_handlers(vars_block))}",
        "EXTERNAL_HANDLERS_USE_DEV_DOCKER = ''",
        "",
    ]
    return "\n".join(lines) + "\n"


def _render_env_development(
    vars_block: dict[str, str],
    *,
    api_local_url: str,
    ws_local_url: str,
    extensions: str,
) -> str:
    def v(key: str) -> str:
        return str(vars_block.get(key) or "")

    cloud_ws = v("VITE_WEBSOCKET_URL") or v("WEBSOCKET_URL")

    return "\n".join(
        [
            f"VITE_API_URL={_py_str(api_local_url)}",
            "# Cloud API Gateway WebSocket — uncomment to test against cloud instead of local WSS",
            f"# VITE_WEBSOCKET_URL={_py_str(cloud_ws)}",
            f"VITE_WEBSOCKET_URL={_py_str(ws_local_url)}",
            "",
            f"VITE_COGNITO_REGION={_py_str(v('COGNITO_REGION'))}",
            f"VITE_COGNITO_USERPOOL_ID={_py_str(v('COGNITO_USERPOOL_ID'))}",
            f"VITE_COGNITO_APP_CLIENT_ID={_py_str(v('COGNITO_APP_CLIENT_ID'))}",
            "",
            "VITE_WL_LOGO='/small_logo.jpg'",
            "VITE_WL_LOGIN='/large_logo.jpg'",
            "",
            "VITE_GOOGLE_MAPS_API_KEY=''",
            "",
            "VITE_DEV_MODE=true",
            f"VITE_EXTENSIONS={_py_str(extensions)}",
            "",
        ]
    )


def _render_run_sh(*, aws_region: str) -> str:
    return "\n".join(
        [
            "#!/bin/bash",
            "export RENGLO_CONFIG_PATH=./env_config.py",
            "# Set to your local AWS CLI profile (see: aws configure list-profiles)",
            "export AWS_PROFILE=<your-aws-profile>",
            f"export AWS_DEFAULT_REGION={aws_region}",
            "renglo-serve --host 127.0.0.1 --port 5001 --debug",
            "",
        ]
    )


def _render_handoff_readme(
    *,
    env_name: str,
    aws_region: str,
    invite_fe_base_url: str,
) -> str:
    return f"""# Local development config — `{env_name}`

This folder is for **developers**. Your infrastructure operator prepared these files so you can run the Renglo app on your laptop against the `{env_name}` AWS account.

## Two kinds of environment

| Kind | What it is | These files? |
| ---- | ---------- | ------------ |
| **Local development** | API and console run on your machine (`127.0.0.1`). They still use cloud Cognito, DynamoDB, S3, and email in AWS. | **Yes — this bundle** |
| **Cloud** | Hosted API (Lambda) and console (Amplify). Deployed later for real production use. | **No** — different config; not what this folder is for |

Use only the files in this folder for local development. Do not treat them as production / cloud deployment settings.

## 1. Install the developer workspace first

If you have not set up the app repos yet, follow the installation guide in **`renglo-api`** (clone console, `renglo-lib`, `renglo-api`, and **`wss`** into `dev/`; create the venv, install dependencies, etc.):

- Repo: [github.com/renglo/renglo-api](https://github.com/renglo/renglo-api)
- File: **`README.md`** at the root of that repo

Complete that guide **before** copying the files below. This folder only supplies environment-specific config; it does not replace that setup.

You will also need AWS credentials that can access the `{env_name}` account in region **`{aws_region}`**. Your operator will confirm you have the right access.

## 2. Copy the config files you were given

Your operator will send you this folder (or an updated copy of it) when the environment is ready or when infrastructure changes. Place the three app files into your workspace:

```bash
# From the folder you received (this local-dev bundle):
cp env_config.py       <your-workspace>/dev/renglo-api/env_config.py
cp run.sh              <your-workspace>/dev/renglo-api/run.sh
chmod +x <your-workspace>/dev/renglo-api/run.sh

cp .env.development    <your-workspace>/console/.env.development
```

Do **not** copy `README.md` or `manifest.json` into the app repos.

**Set your AWS profile:** open `dev/renglo-api/run.sh` and replace `<your-aws-profile>` with the name of **your** local CLI profile (list profiles with `aws configure list-profiles`). Profile names differ on every machine — use whatever you configured, not the operator's.

When the operator sends a **new** bundle later (new tables, Cognito IDs, email settings, etc.), replace these three files again. Keep any secrets you added yourself (for example `OPENAI_API_KEY` in `env_config.py` or your `AWS_PROFILE` in `run.sh`) if you still need them.

## 3. Local WebSocket server (WSS)

Chat and other real-time features use WebSockets. In production, **API Gateway WebSocket** handles this. On your laptop, use the **`wss`** repo — a small local server that emulates API Gateway in both directions (console → backend and backend → console).

Without WSS, chat messages from your local console would hit the **cloud** WebSocket/API and responses would never reach your local API.

**Setup** (once per machine — see also `dev/wss/README.md`):

```bash
cd dev/wss
./setup_venv.sh
source wss-venv/bin/activate
python dev_ws_service.py
```

Leave WSS running on **`127.0.0.1:8080`** while you develop.

The copied config files already point at local WSS:

| File | Active (local) | Commented (cloud) |
| ---- | -------------- | ----------------- |
| `console/.env.development` | `VITE_WEBSOCKET_URL='ws://127.0.0.1:8080'` | Cloud `wss://…execute-api…` URL |
| `dev/renglo-api/env_config.py` | `WEBSOCKET_CONNECTIONS='http://127.0.0.1:8080/send_to_client'` | Cloud connections URL from SSM |

To test against **cloud** WebSockets instead (corner cases), swap the comments: comment the local lines and uncomment the cloud lines in both files.

## 4. Run the app locally

Follow the run steps in the **`renglo-api` README** (local API + console). You need **three** processes:

```bash
# Terminal 1 — local WebSocket (WSS)
cd dev/wss && source wss-venv/bin/activate && python dev_ws_service.py

# Terminal 2 — API
cd dev/renglo-api && ./run.sh

# Terminal 3 — console
cd console && npm run dev
```

- Console: `{invite_fe_base_url}` (team invite links in email also use this URL)
- API: `http://127.0.0.1:5001`
- WSS: `ws://127.0.0.1:8080`

Log in at `/login` (not `/register`). Your operator creates the first Cognito admin via Step 7.4 in the bootstrap README; that admin completes setup at `/invite?setup=admin&email=...` before using `/login`. After that, new people join by team invite.

## Files in this folder

| File | Purpose |
| ---- | ------- |
| `env_config.py` | Backend config for local API → copy to `dev/renglo-api/` |
| `run.sh` | Starts the local API — **edit `AWS_PROFILE`** to your profile → copy to `dev/renglo-api/` |
| `.env.development` | Vite / console env for local UI → copy to `console/` |
| `manifest.json` | Operator metadata — ignore |
"""


def run_write_local_config(
    *,
    env_name: str,
    aws_profile: str | None,
    aws_region: str,
    output_dir: Path | None = None,
    stage: str = "production",
    invite_fe_base_url: str = _DEFAULT_INVITE_FE,
    api_local_url: str = _DEFAULT_API_LOCAL,
    ws_local_url: str = _DEFAULT_WS_LOCAL,
    wss_backend_url: str = _DEFAULT_WSS_BACKEND,
    extensions: str = _DEFAULT_EXTENSIONS,
    preserve_secrets: bool = True,
    dry_run: bool = False,
) -> Path:
    payload = fetch_platform_vars(
        env_name,
        stage,
        aws_profile=aws_profile,
        aws_region=aws_region,
    )
    vars_block = {str(k): str(v) for k, v in payload.get("VARS", {}).items()}

    out_dir = output_dir or (_BOOTSTRAP_DIR / "output" / env_name / "local-dev")
    env_config_path = out_dir / "env_config.py"

    existing = _existing_secrets(env_config_path) if preserve_secrets else {}
    secret_key = existing.get("SECRET_KEY") or secrets.token_hex(32)
    csrf_key = existing.get("CSRF_SESSION_KEY") or secrets.token_hex(32)

    files: dict[str, str] = {
        "env_config.py": _render_env_config(
            vars_block,
            invite_fe_base_url=invite_fe_base_url,
            wss_backend_url=wss_backend_url,
            secret_key=secret_key,
            csrf_session_key=csrf_key,
        ),
        ".env.development": _render_env_development(
            vars_block,
            api_local_url=api_local_url,
            ws_local_url=ws_local_url,
            extensions=extensions,
        ),
        "run.sh": _render_run_sh(aws_region=aws_region),
        "README.md": _render_handoff_readme(
            env_name=env_name,
            aws_region=aws_region,
            invite_fe_base_url=invite_fe_base_url,
        ),
    }

    manifest = {
        "env_name": env_name,
        "stage": stage,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ssm_path": f"/{env_name}/bootstrap/platform-vars/{stage}",
        "aws_region": aws_region,
        "aws_profile": aws_profile,
        "output_dir": str(out_dir),
        "invite_fe_base_url": invite_fe_base_url,
        "files": list(files.keys()),
    }
    files["manifest.json"] = json.dumps(manifest, indent=2) + "\n"

    if dry_run:
        print(f"[dry-run] Would write local-dev bundle to: {out_dir}")
        for name in files:
            print(f"  {name}")
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        if name == "run.sh":
            path.chmod(0o755)

    print(f"Local dev config written to: {out_dir}")
    print("  env_config.py")
    print("  .env.development")
    print("  run.sh")
    print("  README.md")
    print("  manifest.json")
    print("\nShare this folder with developers (see README.md inside).")
    return out_dir
