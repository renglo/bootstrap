# Bootstrap — environment installation (CDK + CloudFormation)

Step-by-step guide from scratch. Examples use **bash** (Linux/macOS/WSL).

**Default path:** install the **Renglo platform only** — backend API, Cognito, DynamoDB, S3, and a handlers Lambda (`compute_type: lambda_only`, no extension repo). Most new environments should follow §1–§7 as written.

**Advanced path:** add an external extension and/or ECS/EC2 handler capacity — see [Advanced: extensions and ECS/EC2 handlers](#advanced-extensions-and-ecsec2-handlers). That section lists config changes only; it does not repeat the install steps.

---



## Local requirements

- AWS CLI configured (`aws configure list-profiles`)
- **Python 3.12 installed on this machine** (see [§2](#2-virtualenvs) — do not copy `bootstrap/venv` from another computer or OS)
- Node.js + CDK CLI (`npm install -g aws-cdk`)
- Git
- GitHub CLI `gh` (optional — only for uploading vars/secrets to GitHub Environments)

**macOS:** install Python 3.12 if needed, then create the bootstrap venv locally:

```bash
brew install python@3.12
python3.12 --version
```

---



## 1. Clone repos

Create a workspace folder and clone the three **platform** repos:

```bash
mkdir <workspace> && cd <workspace>
git clone https://github.com/renglo/bootstrap.git bootstrap
git clone https://github.com/renglo/launcher.git launcher
git clone https://github.com/renglo/extensions-service.git extensions-service
```

You can use any name instead of `infra-installer` for the workspace folder (e.g. `ops/`).

`extensions-service` is required even without an extension: CDK synth imports `compute_stack.py` from it to define the handlers Lambda and OIDC roles. You do **not** clone an extension repo unless you need [Advanced: extensions](#advanced-extensions-and-ecsec2-handlers).

---



## 2. Virtualenvs

Run from the **workspace root** (the folder that contains `bootstrap/`, `launcher/`, and `extensions-service/`).

**Do not copy** `bootstrap/venv` **from another machine.** A venv is tied to the OS and Python path where it was created.

### macOS / Linux (recommended)

**Prerequisite:** Python 3.12 available on this machine (`python3.12 --version`).

```bash
cd <workspace>   # e.g. ops/

# Remove a stale or copied venv (safe if you have not installed deps yet)
rm -rf bootstrap/venv

# 1. Create the venv on this machine
python3.12 -m venv bootstrap/venv

# 2. Install bootstrap dependencies into that venv
bash bootstrap/setup-venvs.sh
```

If `python3.12` is not on your PATH, use Homebrew's binary explicitly:

```bash
/opt/homebrew/bin/python3.12 -m venv bootstrap/venv
bash bootstrap/setup-venvs.sh --python /opt/homebrew/bin/python3.12
```

Verify:

```bash
bootstrap/venv/bin/python --version
bootstrap/venv/bin/python -c "import aws_cdk; print('aws_cdk OK')"
```

You should see Python 3.12.x and `aws-cdk-lib` from `bootstrap/requirements.txt`.

### Alternative (script creates the venv)

If `bootstrap/venv` does **not** exist yet, `setup-venvs.sh` can create it:

```bash
cd <workspace>
rm -rf bootstrap/venv   # only if replacing a broken/copied venv
bash bootstrap/setup-venvs.sh --python python3.12
```

Idempotent — safe to re-run after `requirements.txt` changes.

### Troubleshooting: `venv python not found` / `No module named 'aws_cdk'`

Usually means `bootstrap/venv` exists but is from the wrong OS, is broken, or deps were not installed:

```bash
grep ^home= bootstrap/venv/pyvenv.cfg   # C:\... → Windows venv; remove it
ls bootstrap/venv/bin/python            # should exist on macOS
rm -rf bootstrap/venv
python3.12 -m venv bootstrap/venv
bash bootstrap/setup-venvs.sh
bootstrap/venv/bin/python -c "import aws_cdk"
```

---



## 3. Configure the environment

Copy the example and edit `launcher/cdk/customer-config.json`:

```bash
cd launcher/cdk
cp customer-config.example.json customer-config.json
```



### Default config (most new environments)

Omit `extension_path`. Set `compute_type` to `lambda_only`:

```json
{
  "env_name": "myenv",
  "aws_account": "123456789012",
  "aws_region": "us-east-1",
  "github_repo": "MyOrg/my-releases-repo",
  "enable_staging": false,
  "compute_type": "lambda_only"
}
```


| Field                        | Purpose                                                                    |
| ---------------------------- | -------------------------------------------------------------------------- |
| `env_name`                   | Prefix for AWS resources and synth output (`bootstrap/output/<env_name>/`) |
| `aws_account` / `aws_region` | Target AWS account and region                                              |
| `github_repo`                | **Releases** repo (backend CI via OIDC)                                    |
| `enable_staging`             | `false` = production only; `true` = production + staging Lambdas/APIs      |
| `compute_type`               | `lambda_only` (default) — handlers run as a Lambda with zip deploy from CI |


Optional: `github_handlers_repo` defaults to `github_repo` when omitted. Use one repo for both backend and handlers CI, or set a separate handlers repo.

### What the default install creates


| Included                                                                            | Not included (see [Advanced](#advanced-extensions-and-ecsec2-handlers)) |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Stack A: Cognito, S3, DynamoDB, tenant IAM, releases OIDC, backend ECR + CodeDeploy | Extension S3 buckets and IAM                                            |
| Stack A: seed CodeBuild (builds initial backend container image during deploy)      | Extension blueprints in DynamoDB                                        |
| Stack B: backend Lambda + REST/WebSocket API Gateway                                | ECS cluster, handlers ECR, EC2 ASG                                      |
| Stack B: handlers Lambda (`{env}-handlers`) + handlers OIDC                         | `/{env}/bootstrap/ecs-*` SSM parameters                                 |
| SSM bootstrap config after write-state                                              | `EXTERNAL_HANDLERS_ECS_HANDLERS` routing to ECS tasks                   |


**About ECR:** the backend always uses a **container Lambda** (ECR + CodeDeploy) — that replaces the old Zappa zip deploy. You do not configure ECR manually for a new project; stack-a runs a seed build during deploy and CI pushes real images afterward. With `lambda_only`, **handlers** use a zip Lambda (no handlers ECR). Handlers ECR/ECS only apply when you switch to `fargate` or `ec2` for heavy extension workloads.

Platform-wide defaults (`architecture`, backend seed image URI/tag): `launcher/cdk/platform_defaults.json` (copied to `bootstrap/output/<env>/cdk/` on synth).

---



## 4. Generate CloudFormation templates

From the workspace root. You do **not** need to activate the venv — `install.py` re-execs into `bootstrap/venv` automatically:

```bash
cd <workspace>
python3.12 bootstrap/install.py synth
```

Equivalent:

```bash
bootstrap/venv/bin/python bootstrap/install.py synth
```

Requires the **CDK CLI** on your PATH (`npm install -g aws-cdk`) in addition to `aws-cdk-lib` in the venv.

Output: `bootstrap/output/<env>/`

**Env root** (CloudFormation deploy + post-deploy):

- `<env>-stack-a.template.json`, `<env>-stack-b.template.json`

`**cdk/**` subfolder (only for `cdk deploy`):

- `app.py`, `stacks/`, `extensions/`, `customer-config.json`, `platform_defaults.json`
- CDK assembly (`manifest.json`, `*.assets.json`, `asset.*/`, …)

When `extension_path` is set, synth also emits `extension-state.json`, `extension-blueprints/`, and bundled extension infra under `cdk/extension/`.

Stack-a builds and pushes a seed backend image automatically via a CodeBuild custom resource during deploy. No local Docker and no manual step between stack-a and stack-b.

---



## 5. Deploy stacks to AWS

Run each step **in order**. Wait for each `cdk deploy` to finish before the next. Stack-a includes a seed image build (CodeBuild) and can take several minutes.

### Step 5.1 — Set environment variables

```bash
export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>
```



### Step 5.2 — Bootstrap CDK (once per AWS account and region)

Skip if you already ran `cdk bootstrap` in this account/region.

```bash
cdk bootstrap aws://$(aws sts get-caller-identity --query Account --output text)/${AWS_REGION} \
  --profile "$AWS_PROFILE"
```



### Step 5.3 — Check GitHub OIDC

Run this and read the output before continuing:

```bash
aws iam list-open-id-connect-providers --profile "$AWS_PROFILE"
```

**How to decide the next step:**


| Step 5.3 output                                                                                             | Next step                                |
| ----------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| `"OpenIDConnectProviderList"` contains an ARN ending in `oidc-provider/token.actions.githubusercontent.com` | **Step 5.4A** — do **not** run Step 5.4B |
| `"OpenIDConnectProviderList": []` (empty list)                                                              | **Step 5.4B** — do **not** run Step 5.4A |
| List has other providers but **not** `token.actions.githubusercontent.com`                                  | **Step 5.4B** — do **not** run Step 5.4A |


Example — run **Step 5.4A** (GitHub OIDC already exists):

```json
{
    "OpenIDConnectProviderList": [
        {
            "Arn": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
        }
    ]
}
```

Example — run **Step 5.4B** (first platform deploy in this AWS account):

```json
{
    "OpenIDConnectProviderList": []
}
```

Run **exactly one** of Step 5.4A or Step 5.4B, then continue to Step 5.5.

### Step 5.4A — Deploy stack-a

**Run this step if:** Step 5.3 showed `token.actions.githubusercontent.com` in `OpenIDConnectProviderList`.

**Do not run Step 5.4B.**

```bash
cd <workspace>/bootstrap/output/${ENV}/cdk

cdk deploy "${ENV}-stack-a" \
  --app "../../../venv/bin/python app.py" \
  --output . \
  --require-approval never \
  --profile "$AWS_PROFILE"
```

Wait until this command exits successfully, then go to **Step 5.5**.

### Step 5.4B — Deploy stack-a (creates GitHub OIDC in this account)

**Run this step if:** Step 5.3 returned `"OpenIDConnectProviderList": []`, or the list has no entry for `token.actions.githubusercontent.com`.

**Do not run Step 5.4A.**

This is typical on the **first** platform deploy in an AWS account. The extra `--parameters CreateGitHubOIDC=true` line registers the GitHub Actions OIDC provider.

```bash
cd <workspace>/bootstrap/output/${ENV}/cdk

cdk deploy "${ENV}-stack-a" \
  --app "../../../venv/bin/python app.py" \
  --output . \
  --parameters CreateGitHubOIDC=true \
  --require-approval never \
  --profile "$AWS_PROFILE"
```

Wait until this command exits successfully, then go to **Step 5.5**.

### Step 5.5 — Deploy stack-b

**Run this step if:** Step 5.4A or Step 5.4B finished successfully (stack-a is `CREATE_COMPLETE` or `UPDATE_COMPLETE`).

**Do not run this step if:** stack-a deploy failed or is still in progress.

```bash
cd <workspace>/bootstrap/output/${ENV}/cdk

cdk deploy "${ENV}-stack-b" \
  --app "../../../venv/bin/python app.py" \
  --output . \
  --exclusively \
  --require-approval never \
  --profile "$AWS_PROFILE"
```

With `compute_type=ec2`, add deploy-time parameters to this command — see [Advanced](#advanced-extensions-and-ecsec2-handlers).

### Fallback — if `cdk deploy` fails

Use `aws cloudformation deploy` with an S3 bucket (templates exceed the CLI inline size limit).

**Fallback step 1 — Resolve template bucket and deploy stack-a:**

```bash
export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

cd <workspace>/bootstrap/output/${ENV}

BUCKET="${CFN_TEMPLATE_BUCKET:-$(aws cloudformation describe-stacks \
  --stack-name "${ENV}-stack-a" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" \
  --output text 2>/dev/null)}"
if [ -z "$BUCKET" ] || [ "$BUCKET" = "None" ]; then
  ACCOUNT=$(aws sts get-caller-identity --query Account --output text --profile "$AWS_PROFILE")
  BUCKET="cdk-hnb659fds-assets-${ACCOUNT}-${AWS_REGION}"
fi

aws cloudformation deploy \
  --template-file "${ENV}-stack-a.template.json" \
  --stack-name "${ENV}-stack-a" \
  --capabilities CAPABILITY_NAMED_IAM \
  --s3-bucket "${BUCKET}" \
  --s3-prefix "cloudformation/templates/${ENV}" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

First deploy in a new AWS account — add before `--profile`:

```bash
  --parameter-overrides CreateGitHubOIDC=true \
```

**Fallback step 2 — Deploy stack-b:**

```bash
cd <workspace>/bootstrap/output/${ENV}

aws cloudformation deploy \
  --template-file "${ENV}-stack-b.template.json" \
  --stack-name "${ENV}-stack-b" \
  --capabilities CAPABILITY_NAMED_IAM \
  --s3-bucket "${BUCKET}" \
  --s3-prefix "cloudformation/templates/${ENV}" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

---



## 6. Bootstrap config in SSM (write-state after stack-b)

CloudFormation stacks do **not** write bootstrap JSON to Parameter Store. After stack-b succeeds, run **write-state** once (idempotent):

```bash
cd <workspace>

python3.12 bootstrap/install.py write-state \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"
```

Use `--dry-run` to preview without writing.


| SSM parameter                               | Purpose                                        | OIDC reader                                  |
| ------------------------------------------- | ---------------------------------------------- | -------------------------------------------- |
| `/{env}/bootstrap/platform-vars/production` | Releases repo CI — production                  | `GitHubActionsDeployRole-{env}-production`   |
| `/{env}/bootstrap/platform-vars/staging`    | Releases repo CI — staging (if enabled)        | `GitHubActionsDeployRole-{env}-staging`      |
| `/{env}/bootstrap/deploy-input`             | Handlers repo CI                               | `GitHubActionsHandlersRole-{env}-production` |
| `/{env}/bootstrap/ecs-*`                    | Handlers EC2 network (`compute_type=ec2` only) | releases + handlers OIDC roles               |


Each JSON envelope has `GITHUB_REPOSITORY`, `ENVIRONMENT`, `VARS`, and `SECRETS` (always `{}`). Application secrets (e.g. `OPENAI_API_KEY`) are **repo secrets** in GitHub, not in SSM.

With `lambda_only`, there are no `ecs-*` parameters — skip ECS network merge in CI.

**Verify after write-state:**

```bash
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/platform-vars/production" \
  --query Parameter.Value \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

---



## 7. CI/CD contract

Configure GitHub Actions in your **releases** repo to:

1. Assume `GitHubActionsDeployRole-{env}-production` (and staging if enabled) via OIDC.
2. Read `/{env}/bootstrap/platform-vars/production` from SSM.
3. Build the backend container image, push to ECR, and deploy via CodeDeploy.

If handlers code lives in a separate repo (or a separate workflow in the same repo):

1. Assume `GitHubActionsHandlersRole-{env}-production`.
2. Read `/{env}/bootstrap/deploy-input` from SSM.
3. Build a Lambda **zip** and update `{env}-handlers` (no handlers ECR with `lambda_only`).

Example reads:

```bash
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/platform-vars/production" \
  --query Parameter.Value --output text | jq -r '.VARS.BASE_URL'

aws ssm get-parameter \
  --name "/${ENV}/bootstrap/deploy-input" \
  --query Parameter.Value --output text | jq -r '.VARS.LAMBDA_HANDLERS_FUNCTION_NAME'
```

Verify handlers vars after write-state (expect empty `ECS_CLUSTER` and `ECR_IMAGE_URI` with `lambda_only`):

```bash
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/deploy-input" \
  --query Parameter.Value --output text \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  | jq '.VARS | {LAMBDA_HANDLERS_FUNCTION_NAME, ECR_IMAGE_URI, ECS_CLUSTER}'
```

For ECS/EC2 handler setups, merge `ecs-*` parameters into runtime `VARS` — see [Advanced](#advanced-extensions-and-ecsec2-handlers).

`bootstrap/helpers/inject_github_env_vars.py` is a legacy utility, not part of the bootstrap flow.

---



## Flow summary

```text
python3.12 -m venv bootstrap/venv  (on this machine — do not copy venv/)
  → bash bootstrap/setup-venvs.sh
  → customer-config.json  (lambda_only, no extension_path)
  → synth  →  bootstrap/output/<env>/
  → cdk bootstrap  (once per account/region)
  → cdk deploy <env>-stack-a  →  cdk deploy <env>-stack-b
  → write-state
  → CI/CD: releases repo pushes backend image (ECR + CodeDeploy);
           handlers repo pushes Lambda zip (lambda_only)
```

---



## Stack deletion (teardown)

Delete stacks in **reverse deploy order**.

**CloudFormation:**

```bash
export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

aws cloudformation delete-stack --stack-name "${ENV}-stack-b" --profile "$AWS_PROFILE" --region "$AWS_REGION"
aws cloudformation wait stack-delete-complete --stack-name "${ENV}-stack-b" --profile "$AWS_PROFILE" --region "$AWS_REGION"
aws cloudformation delete-stack --stack-name "${ENV}-stack-a" --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

**CDK** (from `bootstrap/output/<env>/cdk/`):

```bash
cd <workspace>/bootstrap/output/${ENV}/cdk
export ENV=<env>
export AWS_PROFILE=<aws-profile>

cdk destroy "${ENV}-stack-b" --app "../../../venv/bin/python app.py" --output . --force --profile "$AWS_PROFILE"
cdk destroy "${ENV}-stack-a" --app "../../../venv/bin/python app.py" --output . --force --profile "$AWS_PROFILE"
```

When `extension_path` was set, stack-b also owns extension-scoped resources (e.g. `{env}-threat-events-{account}`, `{env}_actions_tt_policy`). Platform roles in stack-a are not deleted by stack-b. The account-level GitHub OIDC provider is retained (shared across environments).

**S3 caveat:** if the auto-empty step fails (e.g. object lock, permissions), bucket deletion blocks stack delete until the bucket is emptied manually.

---



## Operator IAM (optional)

If the operator needs permissions to deploy or tear down the environment:

```bash
python bootstrap/helpers/generate_env_deployment_tt_policy.py <env> \
  --aws-profile <aws-profile> \
  --aws-region <aws-region>

python bootstrap/helpers/provision_env_deployment_tt_identity.py <env> \
  --aws-profile <aws-profile> \
  --aws-region <aws-region> \
  --create-access-key
```

---



## Shell scripts on Windows / WSL

If `bash bootstrap/setup-venvs.sh` fails with `$'\r': command not found`, normalize line endings:

```bash
find . -name "*.sh" -exec sed -i 's/\r$//' {} \;
```

---



## Advanced: extensions and ECS/EC2 handlers

Use this when you need an **external extension** (extension-specific IAM, blueprints, threat-events bucket) and/or **handlers on ECS/EC2** because the workload does not fit a Lambda zip (large dependencies, long-running tasks).

Everything else stays the same: follow §1–§7, then adjust config and re-deploy stack-b.

### Config changes

Clone the extension repo as a sibling folder (e.g. `arbitiumlab/`) with `installer/infra/cdk_extension.json`.

Update `launcher/cdk/customer-config.json`:

```json
{
  "env_name": "myenv",
  "aws_account": "123456789012",
  "aws_region": "us-east-1",
  "github_repo": "MyOrg/my-releases-repo",
  "github_handlers_repo": "MyOrg/my-handlers-repo",
  "extension_path": "arbitiumlab",
  "enable_staging": true,
  "compute_type": "fargate"
}
```


| Field                  | When to set                                                                |
| ---------------------- | -------------------------------------------------------------------------- |
| `extension_path`       | Sibling folder name for the extension repo                                 |
| `github_handlers_repo` | Handlers/extension CI repo (defaults to `github_repo`)                     |
| `compute_type`         | `fargate` or `ec2` for ECS/EC2 handlers; `lambda_only` for zip Lambda only |
| `ec2_*`                | Only when `compute_type` is `ec2`                                          |


Re-run synth and update stack-b (stack-a only needs redeploy if platform settings changed):

```bash
python3.12 bootstrap/install.py synth

cd bootstrap/output/${ENV}
aws cloudformation deploy \
  --template-file "${ENV}-stack-b.template.json" \
  --stack-name "${ENV}-stack-b" \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

Large stack-b templates may need `--s3-bucket` using the data bucket from stack-a (see appendix).

### Handlers EC2 network (deploy-time)

When `compute_type=ec2`, pass parameters on stack-b deploy:

```bash
  --parameter-overrides \
    HandlersNetworkMode=existing \
    ExistingVpcId=vpc-0123456789abcdef0 \
    ExistingSubnetIds=subnet-aaa,subnet-bbb
```

Default is `HandlersNetworkMode=create` (dedicated VPC). `ExistingSubnetIds` must belong to `ExistingVpcId` and span at least two Availability Zones.

### CI/CD differences

- Handlers CI pushes container images to handlers ECR and updates ECS task definitions (not a Lambda zip).
- Merge `/{env}/bootstrap/ecs-vpc`, `ecs-subnets`, and `ecs-security-groups` into runtime `VARS`:

```bash
python bootstrap/helpers/merge_bootstrap_ssm.py "${ENV}" production \
  --aws-profile "$AWS_PROFILE" --aws-region "$AWS_REGION" > platform_vars.production.json

python bootstrap/helpers/merge_bootstrap_ssm.py "${ENV}" deploy-input \
  --aws-profile "$AWS_PROFILE" --aws-region "$AWS_REGION" > deploy_input.json
```

Synth emits `extension-state.json` and `extension-blueprints/` at the env root when `extension_path` is set.

---



## Reference

- CDK stacks and details: [launcher/README.md](../launcher/README.md)
- Legacy flow (boto3 `deploy_environment.py`): [launcher/ENVIRONMENT_README.md](../launcher/ENVIRONMENT_README.md)
- JSON payloads and design notes: [ARCHITECTURE.md](ARCHITECTURE.md)

