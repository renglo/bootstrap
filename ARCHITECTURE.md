# Bootstrap — architecture reference (JSON & design)

**Operational quick start (CDK synth, deploy, SSM bootstrap config):** [README.md](README.md)
 
This file keeps **example payloads** and design notes that are too long for the README.

**Current path:** Two CDK stacks (`<env>-stack-a`, `<env>-stack-b`) synthesized to `bootstrap/output/<env>/` via `bootstrap/install.py` (`synth`). Stack A can optionally create the account GitHub OIDC provider via the `CreateGitHubOIDC` parameter. Bootstrap config is written to SSM Parameter Store automatically when stack-b deploys.

**Legacy path:** `install.py` orchestrator calling `deploy_environment.py` + extensions-service CLIs (see [launcher/ENVIRONMENT_README.md](../launcher/ENVIRONMENT_README.md)). Each repo can still be used standalone.

---

## Repo layout (`<main-launcher-root>` / `infra-installer/`)

```
├── launcher/                  # Core backend (DynamoDB, Cognito, backend Lambda, API GW, CodeDeploy)
├── extensions-service/        # Handlers Lambda IAM; optional ECS/ECR/S3
└── bootstrap/                 # Orchestrator + merged state
    ├── install.py
    ├── uninstall.py
    ├── lib/merger.py
    └── state/<extension>/     # Output after install (gitignored)
```

---

## What `install.py` does

1. **Launcher** — `launcher/scripts/deploy_environment.py`: GitHub OIDC (production + staging), DynamoDB, Cognito, tenant IAM/S3, backend ECR/Lambda/API Gateway/WebSocket per stage, CodeDeploy, default blueprints.
2. **Extensions-service** — `provision-infra apply`: handlers Lambda IAM (always); with `--launch-type`: ECS cluster, handlers ECR, S3 results bucket, ECS IAM; with `ec2`: capacity (ASG); optional handlers GitHub OIDC (`--handlers-github-repo`, `--handlers-enable-staging-role`).
3. **Merge** — writes `bootstrap/state/<extension>/` from `launcher/state` + `extensions-service/state`.

Partial venv setup: `bash bootstrap/setup-venvs.sh --launcher-only` or `--extensions-only`.

**Uninstall** (`bootstrap/uninstall.py`): extensions teardown → launcher teardown → delete `bootstrap/state/<extension>/`.

### Merged state (`bootstrap/state/<extension>/`)

| File | Purpose |
|------|---------|
| `platform_vars.production.json` | **Releases** repo → `inject_github_env_vars.py` |
| `platform_vars.staging.json` | Same for staging (if launcher wrote `staging.json`) |
| `deploy_input.json` | **Handlers** stage 2 + GitHub Environment |
| `env_config.py` | App config (launcher + ECS; ECS keys omitted when lambda-only) |

Copy before handlers stage 2: `bootstrap/state/<extension>/deploy_input.json` → `extensions-service/state/<extension>/deploy_input.json` (or `dev/extensions-service/state/<extension>/` in the product repo).

### State directories

```
launcher/state/<extension>/
    created_resources.json
    production.json
    staging.json
    env_config.py

extensions-service/state/<extension>/
    provision_manifest.json
    handlers_github_oidc.json
    runtime_profile.json
    deploy_input.json
    release_manifest.json
    lambda_env_export.json

bootstrap/state/<extension>/
    platform_vars.production.json
    platform_vars.staging.json
    deploy_input.json
    env_config.py
```

State trees are typically gitignored.

### Standalone teardown

**extensions-service only:**

```bash
python extensions-service/run.py <extension> provision-infra teardown \
  --profile <aws-profile> --yes [--keep-logs]
```

**launcher only:**

```bash
cd launcher/scripts
python teardown_environment.py <extension> \
  --aws-profile <aws-profile> \
  --aws-region <region> \
  --yes [--skip-tables] [--skip-cognito] [--keep-logs]
```

---

## Example: `platform_vars.production.json`

GitHub Environment for the **releases** repo (merged from `launcher/state/<ext>/production.json` + handlers `provision_manifest.json`). `OPENAI_API_KEY` is excluded (user-managed).

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
    "ECS_LAUNCH_TYPE": "ec2"
  },
  "SECRETS": {
    "AWS_GITHUB_OIDC_ROLE_ARN": "arn:aws:iam::..."
  }
}
```

When using `compute_type=ec2`, `ECS_VPC`, `ECS_SUBNETS`, and `ECS_SECURITY_GROUPS` are written to separate SSM parameters (`/{env}/bootstrap/ecs-*`) at deploy time. CI/CD merges them into `VARS` (see `bootstrap/helpers/merge_bootstrap_ssm.py`).

---

## Example: `deploy_input.json`

Handlers **stage 2** deploy and GitHub Environment (same envelope as `platform_vars.*`).

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

Deploy merges `VARS` + `SECRETS` into Lambda/ECS runtime except `RUNTIME_ENV_EXCLUDE` (e.g. `AWS_GITHUB_OIDC_ROLE_ARN` for CI only). `VARS` include both `AWS_REGION` and `AWS_DEFAULT_REGION` (same value); ECS task env keeps both; Lambda deploy omits them from `Environment.Variables` (AWS reserved). Lambda create/update metadata is fixed in `extensions-service/deploy_input.py` (including `Description`: `Reglo Deployment`); `FunctionName` comes from `VARS.LAMBDA_HANDLERS_FUNCTION_NAME`. Provision scripts tag IAM/S3/ECR/ECS/CloudWatch resources with the same label where AWS supports it.

Schema: `extensions-service/state/schemas/deploy_input.schema.json`

---

## Example: merged `env_config.py`

```python
# --- launcher-generated ---
DYNAMODB_ENTITY_TABLE = 'arbitiumrs_entities'
COGNITO_USERPOOL_ID = '...'
# ...

# ECS (merged when --launch-type was used)
ECS_CLUSTER = 'arbitiumrs-handlers'
ECS_TASK_DEFINITION = 'arbitiumrs-handlers-ecs'
ECS_RESULTS_BUCKET = 'arbitiumrs-handlers-ecs-...'
ECS_LAUNCH_TYPE = 'ec2'
ECS_NETWORK_MODE = 'bridge'
ECS_SUBNETS = 'subnet-xxx,subnet-yyy,...'
ECS_SECURITY_GROUPS = 'sg-...'
```
