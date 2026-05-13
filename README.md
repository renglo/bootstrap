# platform-installer

Orchestrates the two independent provisioning repos — `launcher` and `extensions-service` — into a single install/uninstall workflow, and merges their outputs into a unified state directory.

## Overview

```
infra-installer/
├── launcher/                  # Core backend infra (Lambda, ECR, API GW, DynamoDB, Cognito, IAM, S3)
├── extensions-service/        # ECS extension infra (cluster, task def, ECR, IAM, S3 results bucket)
└── platform-installer/        # This directory — orchestrator + merged state
    ├── install.py
    ├── uninstall.py
    ├── lib/
    │   └── merger.py
    └── state/
        └── <extension>/
            ├── platform_resources.json          # Combined resource inventory
            ├── platform_vars.production.json    # Merged VARS + SECRETS (production)
            ├── platform_vars.staging.json       # Merged VARS + SECRETS (staging, if launcher wrote staging.json)
            ├── platform_vars.json               # Same as production (backward compatibility)
            ├── handlers_vars.production.json    # Handlers-repo ECS + OIDC (if handlers OIDC was provisioned)
            ├── handlers_vars.staging.json       # Same for staging OIDC role when present
            ├── handlers_vars.json               # Alias of handlers production
            └── env_config.py                    # Extended Python config (launcher + ECS constants)
```

Each repo (`launcher`, `extensions-service`) continues to work standalone. `platform-installer` only calls their existing CLIs as subprocesses and reads their state outputs.

---

## Prerequisites

- Python 3.11+ (3.12 recommended for launcher)
- AWS CLI installed and configured with a named profile that has sufficient permissions
- Git (for cloning repos)

---

## First-time setup

Run once from the workspace root (`infra-installer/`) to create the venv for each repo:

```bash
bash platform-installer/setup-venvs.sh
```

Options:
```bash
bash platform-installer/setup-venvs.sh --python python3.12   # specify Python version
bash platform-installer/setup-venvs.sh --launcher-only        # launcher venv only
bash platform-installer/setup-venvs.sh --extensions-only      # extensions-service venv only
```

This creates:
- `launcher/launch-venv/` — boto3, opensearch-py
- `extensions-service/venv/` — boto3 (for `test` and for `provision-infra apply` when using `--github-repo` OIDC bootstrap)

Each repo can also set up its own venv independently:
```bash
# launcher standalone
cd launcher && python3 -m venv launch-venv && launch-venv/bin/pip install -r requirements.txt

# extensions-service standalone
cd extensions-service && python3 -m venv venv && venv/bin/pip install -r requirements.txt
```

`install.py` and `uninstall.py` auto-detect the venv of each repo. If a venv is missing, the error message tells you exactly what to run.

---

## Install (provision all infra)

Run from the workspace root (`infra-installer/`):

```bash
python platform-installer/install.py <extension> \
  --admin-profile acd-arbitium-tt-dev \
  --aws-region us-east-1 \
  --github-repo Org/launcher-repo \
  [--handlers-github-repo Org/handlers-repo] \
  [--handlers-enable-staging-role] \
  --launch-type ec2
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
   - Lambda IAM managed policy (`<Ext>HandlersPolicy`) + execution role
   - ECS cluster
   - ECR repository for ECS handlers
   - S3 results bucket
   - ECS execution + task IAM roles
   - EC2 capacity (if `--launch-type ec2`): launch template, ASG, capacity provider
   - **Handlers GitHub OIDC** (always invoked from this installer): passes `--github-repo` using `--handlers-github-repo` when set, otherwise the same value as `--github-repo`. Creates IAM roles scoped for ECS/ECR deploy (see `extensions-service/utils/github-handlers-*.template.json`).

3. Merges outputs into `platform-installer/state/<extension>/`:
   - `platform_resources.json` — full JSON inventory of all resources (includes `extensions_service.github_oidc` when present)
   - `platform_vars.production.json` — merged VARS + SECRETS for GitHub **production** (ECS vars included; launcher OIDC role ARN)
   - `platform_vars.staging.json` — same for **staging**, if `launcher/state/<ext>/staging.json` exists
   - `platform_vars.json` — identical to production file (backward compatibility)
   - `handlers_vars.production.json` / `handlers_vars.staging.json` / `handlers_vars.json` — ECS-focused payloads for the **handlers** GitHub repo and `SECRETS.AWS_GITHUB_OIDC_ROLE_ARN` for the handlers OIDC role (written when `extensions-service/state/<ext>/handlers_github_oidc.json` exists)
   - `env_config.py` — Python config with both launcher and ECS constants

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--github-repo` | (required) | GitHub org/repo for **launcher** OIDC and `launcher/state` |
| `--handlers-github-repo` | same as `--github-repo` | GitHub org/repo for **handlers** OIDC (extensions-service CI) |
| `--handlers-enable-staging-role` | off | Create a second handlers OIDC role for GitHub Environment `staging` |
| `--skip-launcher` | off | Skip launcher deploy (extensions-service only) |
| `--skip-extensions` | off | Skip extensions-service provision (launcher only) |
| `--merge-only` | off | Skip all provisioning; only (re)merge existing state files |
| `--launch-type` | `ec2` | ECS launch type: `ec2` or `fargate` |

---

## Uninstall (tear down all infra)

```bash
python platform-installer/uninstall.py <extension> \
  --admin-profile acd-arbitium-tt-dev \
  [--yes]
```

### What it does

1. Runs `extensions-service/run.py <extension> provision-infra teardown --yes` — deletes:
   - ECS capacity (ASG, launch template, capacity provider)
   - ECS cluster
   - ECS ECR repository
   - ECS S3 results bucket
   - ECS IAM roles (execution + task)
   - Lambda IAM policy + role

2. Runs `launcher/scripts/teardown_environment.py` — deletes (reverse order):
   - CodeDeploy deployment groups + application
   - Lambda functions (production + staging)
   - REST API Gateways (production + staging)
   - WebSocket API Gateways (production + staging)
   - ECR repository
   - IAM role + policy
   - S3 bucket (only if it was created by the deploy, not pre-existing)
   - Cognito user pool (unless `--skip-cognito`)
   - DynamoDB tables (unless `--skip-tables`)
   - GitHub OIDC deploy roles

3. Removes `platform-installer/state/<extension>/`

### Options

| Flag | Description |
|------|-------------|
| `--yes` | Skip interactive confirmation |
| `--skip-extensions` | Skip extensions-service teardown |
| `--skip-launcher` | Skip launcher teardown |
| `--skip-tables` | Preserve DynamoDB tables (data safety) |
| `--skip-cognito` | Preserve Cognito user pool (user safety) |

---

## Standalone teardown (per-repo)

Both repos have their own teardown commands and work independently:

**extensions-service only:**
```bash
python extensions-service/run.py <extension> provision-infra teardown \
  --profile acd-arbitium-tt-dev --yes
```

**launcher only:**
```bash
cd launcher/scripts
python teardown_environment.py <extension> \
  --aws-profile acd-arbitium-tt-dev \
  --aws-region us-east-1 \
  --yes [--skip-tables] [--skip-cognito]
```

---

## Output files

### `platform_resources.json`

Complete resource inventory used for teardown and service mesh schema:

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
      "staging": { "..." }
    },
    "github_oidc": { "production_role_arn": "...", "staging_role_arn": "..." }
  },
  "extensions_service": {
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

### `platform_vars.production.json` / `platform_vars.staging.json` / `platform_vars.json`

Per-environment GitHub payloads: each mirrors `launcher/state/<ext>/production.json` or `staging.json`, with the same ECS keys merged in from `provision_manifest.json` (`ECS_CLUSTER`, `ECS_TASK_DEFINITION`, subnets, etc.). `platform_vars.json` is a copy of the production file for backward compatibility.

Excludes `OPENAI_API_KEY` (user-managed secret).

```json
{
  "GITHUB_REPOSITORY": "Org/repo",
  "ENVIRONMENT": "production",
  "VARS": {
    "WL_NAME": "arbitiumrs",
    "BASE_URL": "https://...",
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

### `handlers_vars.production.json` / `handlers_vars.staging.json` / `handlers_vars.json`

Present when handlers OIDC was provisioned (`extensions-service/state/<ext>/handlers_github_oidc.json`). Same top-level shape as `platform_vars.*`, but `GITHUB_REPOSITORY` is the handlers repo, `VARS` contains only ECS-related keys (plus `WL_NAME`), and `SECRETS.AWS_GITHUB_OIDC_ROLE_ARN` is the **handlers** deploy role (not the launcher backend role).

```json
{
  "GITHUB_REPOSITORY": "Org/handlers-repo",
  "ENVIRONMENT": "production",
  "VARS": {
    "WL_NAME": "arbitiumrs",
    "AWS_REGION": "us-east-1",
    "ECS_CLUSTER": "arbitiumrs-handlers",
    "ECS_TASK_DEFINITION": "arbitiumrs-handlers-ecs",
    "ECS_LAUNCH_TYPE": "ec2",
    "ECS_SUBNETS": "subnet-xxx,subnet-yyy",
    "ECS_SECURITY_GROUPS": "sg-...",
    "ECS_RESULTS_BUCKET": "arbitiumrs-handlers-ecs-..."
  },
  "SECRETS": {
    "AWS_GITHUB_OIDC_ROLE_ARN": "arn:aws:iam::...:role/GitHubActionsHandlersRole-arbitiumrs-production"
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

# ECS extensions-service configuration (auto-generated by platform-installer)
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
    created_resources.json    # Structured resource inventory (used by teardown)
    created_resources.txt     # Human-readable resource list
    production.json           # GitHub production environment payload
    staging.json              # GitHub staging environment payload
    env_config.py             # Launcher-only Python config

extensions-service/state/<extension>/
    provision_manifest.json   # ECS resource snapshot
    handlers_github_oidc.json # Handlers OIDC roles (when provisioned)
    runtime_profile.json      # ECS sizing config
    release_manifest.json     # Deploy build/push/publish timestamps
    lambda_env_export.json    # Lambda environment variable export

platform-installer/state/<extension>/
    platform_resources.json          # Combined inventory (source of truth for teardown)
    platform_vars.production.json    # Merged VARS + SECRETS (production)
    platform_vars.staging.json       # Merged VARS + SECRETS (staging, if launcher wrote staging.json)
    platform_vars.json               # Same as production
    handlers_vars.production.json    # Handlers repo + ECS + handlers OIDC (when present)
    handlers_vars.staging.json
    handlers_vars.json
    env_config.py                    # Extended Python config (launcher + ECS)
```

All state directories are gitignored.
