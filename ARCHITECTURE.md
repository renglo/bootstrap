# Bootstrap orchestrator — architecture & design

This document describes how **`bootstrap/`** (orchestrator) wires **`launcher/`** and **`extensions-service/`** together, what gets written under `bootstrap/state/<extension>/`, and how install / uninstall / teardown relate. For the shortest path to provision a new environment, see [README.md](README.md).

## Overview

```
infra-installer/
├── launcher/                  # Core backend infra (Lambda, ECR, API GW, DynamoDB, Cognito, IAM, S3)
├── extensions-service/        # ECS extension infra (cluster, task def, ECR, IAM, S3 results bucket)
└── bootstrap/                 # This directory — orchestrator + merged state
    ├── install.py
    ├── uninstall.py
    ├── lib/
    │   └── merger.py
    └── state/
        └── <extension>/
            ├── platform_resources.json          # Combined resource inventory
            ├── platform_vars.production.json    # Merged VARS + SECRETS (production)
            ├── platform_vars.staging.json       # Merged VARS + SECRETS (staging, if launcher wrote staging.json)
            ├── deploy_input.json                # Ready-to-use deploy payload for extensions-service
            └── env_config.py                    # Extended Python config (launcher + ECS constants)
```

Each repo (`launcher`, `extensions-service`) continues to work standalone. The orchestrator only calls their existing CLIs as subprocesses and reads their state outputs.

---

## Prerequisites

- Python 3.12 (used by `bootstrap/setup-venvs.sh` for both venvs)
- AWS CLI installed and configured with a named profile that has sufficient permissions
- Git (for cloning repos)

---

## First-time setup (venvs)

From the workspace root (`infra-installer/`). **Idempotent:** re-running upgrades pip and reinstalls `requirements.txt` for each venv. Default interpreter is **Python 3.12** (`python3.12`, overridable with `PYTHON=...` or `--python`).

```bash
bash bootstrap/setup-venvs.sh
```

Partial setup (optional):

```bash
bash bootstrap/setup-venvs.sh --launcher-only
bash bootstrap/setup-venvs.sh --extensions-only
```

This creates:

- `launcher/launch-venv/` — boto3, opensearch-py
- `extensions-service/venv/` — boto3 (for provision-infra OIDC, etc.)

Each repo can also set up its own venv independently:

```bash
# launcher standalone
cd launcher && python3.12 -m venv launch-venv && launch-venv/bin/pip install -r requirements.txt

# extensions-service standalone
cd extensions-service && python3.12 -m venv venv && venv/bin/pip install -r requirements.txt
```

`install.py` and `uninstall.py` auto-detect the venv of each repo. If a venv is missing, the error message tells you exactly what to run.

---

## Install (provision all infra)

Run from the workspace root (`infra-installer/`). `--launch-type` is **optional**: omit it for a lambda-only environment; add it to also provision ECS infra.

```bash
# Lambda-only (no ECS cluster):
python bootstrap/install.py <extension> \
  --profile acd-arbitium-tt-dev \
  --aws-region us-east-1 \
  --github-repo Org/repo

# Lambda + ECS:
python bootstrap/install.py <extension> \
  --profile acd-arbitium-tt-dev \
  --aws-region us-east-1 \
  --github-repo Org/repo \
  --launch-type ec2          # or fargate
```

### What it does

1. Runs `launcher/scripts/deploy_environment.py` — provisions:
   - GitHub OIDC deploy roles (production + staging)
   - DynamoDB tables
   - Cognito user pool
   - IAM policy + role
   - S3 data bucket
   - ECR repository (shared)
   - Lambda functions + aliases (production + staging)
   - REST API Gateways (production + staging)
   - WebSocket API Gateways (production + staging)
   - CodeDeploy application + deployment groups
   - Default blueprints

2. Runs `extensions-service/run.py <extension> provision-infra apply` — provisions:
   - Lambda IAM managed policy + handlers role (always)
   - **Only when `--launch-type` is given:** ECS cluster, ECR for handlers, S3 results bucket, ECS IAM roles
   - **Only when `--launch-type ec2`:** EC2 capacity (launch template, ASG, capacity provider)
   - **Handlers GitHub OIDC** (optional, via `--handlers-github-repo`)

3. Merges outputs into `bootstrap/state/<extension>/`:
   - `platform_resources.json` — full JSON inventory
   - `platform_vars.production.json` / `platform_vars.staging.json` (launcher release repo)
   - `deploy_input.json` — handlers deploy + GitHub Environment (VARS / SECRETS)
   - `env_config.py` — Python config with launcher + ECS constants (ECS keys absent when lambda-only)

**Idempotent:** re-running with the same flags updates IAM/ECS without destroying resources. Re-running without `--launch-type` on an environment that already has ECS preserves the ECS manifest sections.

### Install CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--profile` | (required) | AWS named profile (→ launcher `--aws-profile`, extensions `--profile`) |
| `--github-repo` | (required) | GitHub org/repo for **release/OIDC** trust and `launcher/state` |
| `--launch-type` | *(none — lambda-only)* | `ec2` or `fargate`: also provision ECS cluster + ECR + S3 results bucket |
| `--handlers-github-repo` | same as `--github-repo` | GitHub org/repo for **handlers** OIDC (extensions-service CI) |
| `--handlers-enable-staging-role` | off | Second handlers OIDC role for GitHub Environment `staging` |
| `--tenant` | *(none)* | Prefix `ENVIRONMENT` in merged bootstrap JSON only: `{tenant}_{production\|staging}` (e.g. `acme_production`) |
| `--skip-launcher` | off | Skip launcher deploy (extensions-service only) |
| `--skip-extensions` | off | Skip extensions-service provision (launcher only) |
| `--merge-only` | off | Skip all provisioning; only (re)merge existing state files |

---

## Uninstall (orchestrated teardown)

```bash
python bootstrap/uninstall.py <extension> \
  --profile acd-arbitium-tt-dev \
  [--yes] \
  [--skip-extensions] [--skip-launcher] \
  [--skip-tables] [--skip-cognito] \
  [--keep-logs]
```

### What uninstall does

1. `extensions-service/run.py <extension> provision-infra teardown --profile ... --yes` (full ECS/handlers teardown; optional `--keep-logs`).
2. `launcher/scripts/teardown_environment.py` (CodeDeploy, Lambdas, APIs, ECR, IAM, S3 if created, Cognito, DynamoDB, GitHub deploy roles; optional `--skip-tables`, `--skip-cognito`, `--keep-logs`).
3. Removes `bootstrap/state/<extension>/`.

### Uninstall CLI options

| Flag | Description |
|------|-------------|
| `--profile` | (required) AWS named profile (extensions `--profile`, launcher `--aws-profile`) |
| `--yes` | Skip interactive confirmation |
| `--skip-extensions` | Skip extensions-service teardown |
| `--skip-launcher` | Skip launcher teardown |
| `--skip-tables` | Preserve DynamoDB (launcher only) |
| `--skip-cognito` | Preserve Cognito (launcher only) |
| `--keep-logs` | Preserve CloudWatch log groups (both teardowns) |

---

## Standalone teardown (per-repo)

**extensions-service only:**

```bash
python extensions-service/run.py <extension> provision-infra teardown \
  --profile acd-arbitium-tt-dev --yes [--keep-logs]
```

**launcher only:**

```bash
cd launcher/scripts
python teardown_environment.py <extension> \
  --aws-profile acd-arbitium-tt-dev \
  --aws-region us-east-1 \
  --yes [--skip-tables] [--skip-cognito] [--keep-logs]
```

---

## Output files (merged state)

### `platform_resources.json`

Complete resource inventory used for uninstall and tooling:

```json
{
  "environment": "arbitiumrs",
  "aws_region": "us-east-1",
  "updated_at": "2026-05-12T00:00:00+00:00",
  "launcher": {
    "dynamodb": { "tables": { "arbitiumrs_entities": "arn:..." } },
    "cognito": { "user_pool_id": "...", "app_client_id": "..." },
    "iam": { "policy_name": "...", "role_name": "..." },
    "s3": { "bucket_name": "...", "created": true },
    "backend": {
      "ecr": { "repository_name": "arbitiumrs_backend" },
      "production": { "lambda_function_name": "...", "rest_api_id": "...", "websocket_api_id": "..." },
      "staging": { "...": "..." }
    },
    "github_oidc": { "production_role_arn": "...", "staging_role_arn": "..." }
  },
  "extensions_service": {
    "lambda": {
      "function_name": "arbitiumrs-handlers",
      "LAMBDA_EXTERNAL_HANDLERS_ARN": "arn:aws:lambda:us-east-1:123456789012:function:arbitiumrs-handlers"
    },
    "ecr": { "repository": "arbitiumrs-handlers-ecs" },
    "ecs": { "cluster": "arbitiumrs-handlers", "launch_type": "ec2", "subnets": ["..."] },
    "buckets": { "ecs_results_bucket": "arbitiumrs-handlers-ecs-..." },
    "iam": { "task_role": "...", "execution_role": "..." },
    "github_oidc": {
      "github_repo": "Org/handlers-repo",
      "oidc_provider_arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com",
      "ecs_results_bucket": "arbitiumrs-handlers-ecs-123456789012",
      "production": { "role_name": "...", "role_arn": "...", "policy_name": "..." },
      "staging": { "role_name": "...", "role_arn": "...", "policy_name": "..." }
    }
  }
}
```

### `platform_vars.production.json` / `platform_vars.staging.json`

Per-environment GitHub payloads for the **launcher / releases** repo: each mirrors `launcher/state/<ext>/production.json` or `staging.json`, with ECS keys merged from `provision_manifest.json`.

Excludes `OPENAI_API_KEY` (user-managed secret).

```json
{
  "GITHUB_REPOSITORY": "Org/repo",
  "ENVIRONMENT": "production",
  "VARS": {
    "WL_NAME": "arbitiumrs",
    "BASE_URL": "https://...",
    "LAMBDA_EXTERNAL_HANDLERS_ARN": "arn:aws:lambda:us-east-1:123456789012:function:arbitiumrs-handlers",
    "ECS_CLUSTER": "arbitiumrs-handlers",
    "ECS_TASK_DEFINITION": "arbitiumrs-handlers-ecs",
    "ECS_LAUNCH_TYPE": "ec2",
    "ECS_SUBNETS": "subnet-xxx,subnet-yyy",
    "...": "..."
  },
  "SECRETS": {
    "AWS_GITHUB_OIDC_ROLE_ARN": "arn:aws:iam::..."
  }
}
```

### `deploy_input.json`

Single file for **extensions-service stage 2** (local deploy) and **handlers GitHub Environment** (CI). Same shape as `platform_vars.*`: `GITHUB_REPOSITORY`, `ENVIRONMENT`, `VARS`, `SECRETS`.

Copy to `extensions-service/state/<extension>/deploy_input.json` for local deploy. For CI, run `python bootstrap/helpers/inject_github_env_vars.py --json bootstrap/state/<ext>/deploy_input.json` on the **handlers** repo.

Deploy scripts merge `VARS` + `SECRETS` into Lambda/ECS runtime environment, except keys in `RUNTIME_ENV_EXCLUDE` (e.g. `AWS_GITHUB_OIDC_ROLE_ARN`, which is only for `configure-aws-credentials` in the workflow). Secrets such as `OPENAI_API_KEY` are included in runtime env when present in `SECRETS`.

Lambda metadata (`Handler`, `Runtime`, `Timeout`, `MemorySize`, role name) are fixed constants in `extensions-service/deploy_input.py`; `FunctionName` comes from `VARS.LAMBDA_HANDLERS_FUNCTION_NAME`.

```json
{
  "GITHUB_REPOSITORY": "Org/handlers-repo",
  "ENVIRONMENT": "production",
  "VARS": {
    "WL_NAME": "arbitiumrs",
    "AWS_REGION": "us-east-1",
    "LAMBDA_HANDLERS_FUNCTION_NAME": "arbitiumrs-handlers",
    "ECR_IMAGE_URI": "982081058012.dkr.ecr.us-east-1.amazonaws.com/arbitiumrs-handlers-ecs:latest",
    "ECS_CLUSTER": "arbitiumrs-handlers",
    "DYNAMODB_ENTITY_TABLE": "arbitiumrs_entities",
    "COGNITO_USERPOOL_ID": "...",
    "S3_BUCKET_NAME": "...",
    "ROLE_ARN": "arn:aws:iam::..."
  },
  "SECRETS": {
    "AWS_GITHUB_OIDC_ROLE_ARN": "arn:aws:iam::...:role/GitHubActionsHandlersRole-arbitiumrs-production",
    "OPENAI_API_KEY": "..."
  }
}
```

### `env_config.py`

Python configuration module for the application. Extends the launcher-generated version with ECS constants:

```python
# --- launcher-generated ---
DYNAMODB_ENTITY_TABLE = 'arbitiumrs_entities'
COGNITO_USERPOOL_ID = '...'
# ...

# ECS extensions-service configuration (merged by bootstrap)
ECS_CLUSTER = 'arbitiumrs-handlers'
ECS_TASK_DEFINITION = 'arbitiumrs-handlers-ecs'
ECS_RESULTS_BUCKET = 'arbitiumrs-handlers-ecs-...'
ECS_LAUNCH_TYPE = 'ec2'
ECS_NETWORK_MODE = 'bridge'
ECS_SUBNETS = 'subnet-xxx,subnet-yyy,...'
ECS_SECURITY_GROUPS = 'sg-...'
```

---

## State directory layout

```
launcher/state/<extension>/
    created_resources.json    # Launcher inventory (teardown_environment.py)
    production.json
    staging.json
    env_config.py

extensions-service/state/<extension>/
    provision_manifest.json
    handlers_github_oidc.json
    runtime_profile.json
    release_manifest.json
    lambda_env_export.json

bootstrap/state/<extension>/
    platform_resources.json
    platform_vars.production.json
    platform_vars.staging.json
    deploy_input.json
    env_config.py
```

State trees are typically gitignored.
