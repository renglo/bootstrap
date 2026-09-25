# Bootstrap — architecture reference (JSON & design)

**Operational quick start (CDK synth, deploy, SSM bootstrap config):** [README.md](README.md)
 
This file keeps **example payloads** and design notes that are too long for the README.

**Current path:** Two CDK stacks (`<env>-stack-a`, `<env>-stack-b`) synthesized to `bootstrap/output/<env>/` via `bootstrap/install.py` (`synth`). Stack A can optionally create the account GitHub OIDC provider via the `CreateGitHubOIDC` parameter. Bootstrap config is written to SSM Parameter Store automatically when stack-b deploys. Handler compute and OIDC helpers come from `bom-helper`.

**Legacy path:** `install.py` orchestrator calling `deploy_environment.py` (see [launcher/ENVIRONMENT_README.md](../launcher/ENVIRONMENT_README.md)). Each repo can still be used standalone.

---

## Repo layout (`<main-launcher-root>` / `infra-installer/`)

```
├── launcher/                  # Core backend (DynamoDB, Cognito, backend Lambda, API GW, CodeDeploy)
├── bom-helper/                # Handler compute stack + peer CDK helpers
└── bootstrap/                 # Orchestrator + merged state
    ├── install.py
    ├── uninstall.py
    ├── lib/merger.py
    └── state/<extension>/     # Output after install (gitignored)
```

---

## What `install.py` does

1. **Synth** — packages launcher CDK plus `bom-helper` compute/OIDC modules into `bootstrap/output/<env>/cdk/`.
2. **Deploy** — stack A then stack B (`renglo stack deploy` or raw `cdk deploy`).
3. **State** — `write-state` publishes platform vars to SSM; `write-local-config` writes `output/<env>/local-dev/`.

Partial venv setup: `bash bootstrap/setup-venvs.sh --launcher-only`.

**Uninstall** (`bootstrap/uninstall.py`): optional extension-specific teardown → launcher teardown → delete `bootstrap/state/<extension>/`. Prefer `renglo system destroy` / `renglo stack destroy` for CDK stacks.

### Merged state (`bootstrap/state/<extension>/`)

| File | Purpose |
|------|---------|
| `platform_vars.production.json` | **Releases** repo → `inject_github_env_vars.py` |
| `platform_vars.staging.json` | Same for staging (if launcher wrote `staging.json`) |
| `deploy_input.json` | **Handlers** stage 2 + GitHub Environment |
| `env_config.py` | App config (launcher + ECS; ECS keys omitted when lambda-only) |

### State directories

```
launcher/state/<extension>/
    created_resources.json
    production.json
    staging.json
    env_config.py

bootstrap/state/<extension>/
    platform_vars.production.json
    platform_vars.staging.json
    deploy_input.json
    env_config.py
```

State trees are typically gitignored.

### Standalone teardown

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

Deploy merges `VARS` + `SECRETS` into Lambda/ECS runtime except `RUNTIME_ENV_EXCLUDE` (e.g. `AWS_GITHUB_OIDC_ROLE_ARN` for CI only). `VARS` include both `AWS_REGION` and `AWS_DEFAULT_REGION` (same value); ECS task env keeps both; Lambda deploy omits them from `Environment.Variables` (AWS reserved). `FunctionName` comes from `VARS.LAMBDA_HANDLERS_FUNCTION_NAME`. Provision scripts tag IAM/S3/ECR/ECS/CloudWatch resources with the same label where AWS supports it.

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
