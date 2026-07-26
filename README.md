# Bootstrap — environment installation (CDK + CloudFormation)

Step-by-step guide from scratch. Examples use **bash** (Linux/macOS/WSL). For a copy-paste CloudFormation flow, see [Appendix: CloudFormation deploy](#appendix-cloudformation-deploy-full-script). For platform-only install without an extension or ECS/EC2 handlers, see [Appendix: Minimum Renglo setup](#appendix-minimum-renglo-setup-no-external-handlers).

---

## Local requirements

- AWS CLI configured (`aws configure list-profiles`)
- **Python 3.12 installed on this machine** (see [§2](#2-virtualenvs) — do not copy `bootstrap/venv` from another computer or OS)
- Node.js + CDK CLI (`npm install -g aws-cdk`) — only if using `cdk deploy`
- Git
- GitHub CLI `gh` (only for uploading vars/secrets to GitHub Environments)

**macOS:** install Python 3.12 if needed, then create the bootstrap venv locally:

```bash
brew install python@3.12
python3.12 --version
```

---

## 1. Clone repos

Create a workspace folder in your computer and clone the repositories

```bash
mkdir infra-installer && cd infra-installer
git clone https://github.com/renglo/bootstrap.git bootstrap
git clone https://github.com/renglo/launcher.git launcher
git clone https://github.com/renglo/extensions-service.git extensions-service
```

You can use any other name instead of infra-installer for your workspace folder.

---

## 2. Virtualenvs

Run from the **workspace root** (the folder that contains `bootstrap/`, `launcher/`, and `extensions-service/` — e.g. `ops/`).

**Do not copy `bootstrap/venv` from another machine.** A venv is tied to the OS and Python path where it was created. A Windows venv (`Scripts/python.exe`, `home = C:\...` in `pyvenv.cfg`) will not work on macOS, which expects `bootstrap/venv/bin/python`.

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

If `python3.12` is not on your PATH, use Homebrew’s binary explicitly:

```bash
/opt/homebrew/bin/python3.12 -m venv bootstrap/venv
bash bootstrap/setup-venvs.sh --python /opt/homebrew/bin/python3.12
```

Verify:

```bash
bootstrap/venv/bin/python --version
bootstrap/venv/bin/python -c "import aws_cdk; print('aws_cdk OK')"
bootstrap/venv/bin/pip list
```

You should see Python 3.12.x, `aws-cdk-lib`, and other packages from `bootstrap/requirements.txt` (which includes `launcher/cdk/requirements.txt`).

### Alternative (script creates the venv)

If `bootstrap/venv` does **not** exist yet, `setup-venvs.sh` can create it for you:

```bash
cd <workspace>
rm -rf bootstrap/venv   # only if replacing a broken/copied venv
bash bootstrap/setup-venvs.sh --python python3.12
```

Idempotent — safe to re-run after `requirements.txt` changes (upgrades pip and reinstalls deps).

Optional: install launcher and extensions-service venvs too:

```bash
bash bootstrap/setup-venvs.sh --all --python python3.12
```

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

`setup-venvs.sh` recreates Windows venvs on macOS automatically and verifies `aws_cdk` after install.

---

## 3. Configure the environment

```bash
cd launcher/cdk
cp customer-config.example.json customer-config.json
# Edit customer-config.json: env_name, aws_account, aws_region, github_repo, compute_type, etc.
```

Important fields in `customer-config.json`:

- `env_name` — prefix for AWS resources and synth output folder (`bootstrap/output/<env_name>/`)
- `github_repo` — **releases** repo (backend OIDC)
- `github_handlers_repo` — **handlers** repo (extensions OIDC)
- `extension_path` — sibling folder with `installer/infra/cdk_extension.json` (e.g. `arbitiumlab`); extension resources are synthesized into **`<env>-stack-b`**
- `compute_type` — `lambda_only` | `fargate` | `ec2`
- `ec2_min_instances` / `ec2_desired_instances` / `ec2_max_instances` — only when `compute_type` is `ec2`

Platform-wide defaults (`architecture`, backend seed image URI/tag): `launcher/cdk/platform_defaults.json` (copied to `bootstrap/output/<env>/cdk/` on synth).

---

## 4. Generate CloudFormation templates

From the workspace root (`ops/`). You do **not** need to `source activate` the venv — `install.py` re-execs into `bootstrap/venv` automatically (macOS Homebrew `python3.12` is fine):

```bash
cd <infra-installer>   # e.g. ops/
python3.12 bootstrap/install.py synth
```

Equivalent (calls the venv directly):

```bash
bootstrap/venv/bin/python bootstrap/install.py synth
```

Requires the **CDK CLI** on your PATH (`npm install -g aws-cdk`) in addition to the Python `aws-cdk-lib` package in the venv.

Output: `bootstrap/output/<env>/`

**Env root** (CloudFormation deploy + post-deploy):

- `<env>-stack-a.template.json`, `<env>-stack-b.template.json`
- `extension-state.json`, `extension-blueprints/` (when `extension_path` is set)

**`cdk/`** subfolder (only for `cdk deploy`):

- `app.py`, `stacks/`, `extensions/`, `customer-config.json`, `platform_defaults.json`
- `extension/` — bundled extension infra (when `extension_path` is set)
- CDK assembly (`manifest.json`, `*.assets.json`, `asset.*/`, …)

Seed image: stack-a builds and pushes it automatically via a CodeBuild custom resource (`SeedCodeBuildProjectName`, typically `<env>-seed-image`). No local Docker and no manual step between stack-a and stack-b.

---

## 5. Deploy stacks to AWS

Required order: **stack-a → stack-b** (stack-a builds the seed image automatically during its own deploy).

### 5a. CloudFormation CLI (recommended)

From `bootstrap/output/<env>/` (templates at env root). See the [appendix](#appendix-cloudformation-deploy-full-script) for a full script including `--s3-bucket` on stack-b.

```bash
cd <infra-installer>/bootstrap/output/<env>

export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

# Stack A — pass CreateGitHubOIDC=true only if the account lacks
# token.actions.githubusercontent.com as an IAM OIDC provider.
# You can run 'aws iam list-open-id-connect-providers' to check if there is one already
# Stack A also builds and pushes the seed image (CodeBuild custom resource);
# the deploy does not complete until the build succeeds.
aws cloudformation deploy \
  --template-file "${ENV}-stack-a.template.json" \
  --stack-name "${ENV}-stack-a" \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

```bash
cd <infra-installer>/bootstrap/output/<env>

aws cloudformation deploy \
  --template-file "${ENV}-stack-b.template.json" \
  --stack-name "${ENV}-stack-b" \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

To rebuild the seed image manually later (rarely needed):

```bash
aws codebuild start-build --project-name "${ENV}-seed-image" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

### 5b. CDK CLI (alternative)

From `bootstrap/output/<env>/cdk/`:

```bash
cd <infra-installer>/bootstrap/output/<env>/cdk

export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

# Stack A builds the seed image automatically (CodeBuild custom resource).
cdk deploy "$ENV-stack-a" \
  --app "python app.py" \
  --output . \
  --require-approval never \
  --profile "$AWS_PROFILE"
```

```bash
cd <infra-installer>/bootstrap/output/<env>/cdk

cdk deploy "$ENV-stack-b" \
  --app "python app.py" \
  --output . \
  --exclusively \
  --require-approval never \
  --profile "$AWS_PROFILE"
```

**Notes:**

- `CreateGitHubOIDC` on **`<env>-stack-a`** — set to `true` only on first deploy in an account without the GitHub Actions OIDC provider (`token.actions.githubusercontent.com`). Default is `false`. Check with `aws iam list-open-id-connect-providers`.
- With `compute_type=ec2`, stack-b exposes **`HandlersNetworkMode`** on **`<env>-stack-b`** (`create` default, or `existing` with `ExistingVpcId` / `ExistingSubnetIds`). In `create` mode the stack provisions a dedicated handlers VPC and subnets; in `existing` mode it uses customer VPC/subnets and still creates a dedicated security group. Synth does not need AWS credentials.
- Stack-b templates may reference a CDK bootstrap asset for S3 auto-delete; the account needs `cdk bootstrap` (or a prior CDK deploy) in that region.
- Large stack-b templates: upload via `--s3-bucket` using the data bucket from stack-a (see appendix).
- All IAM deploys require `--capabilities CAPABILITY_NAMED_IAM` when using `aws cloudformation deploy`.

---

## 6. Bootstrap config in SSM (write-state after stack-b)

CloudFormation stacks do **not** write bootstrap JSON to Parameter Store. After stack-b succeeds, run **write-state** once (idempotent — safe to re-run after stack updates):

```bash
cd <infra-installer>   # e.g. ops/

python bootstrap/install.py write-state \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"
```

Or with the venv python:

```bash
bootstrap/venv/bin/python bootstrap/install.py write-state \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"
```

Use `--dry-run` to preview without writing.

| SSM parameter | Purpose | OIDC reader |
|---------------|---------|-------------|
| `/{env}/bootstrap/platform-vars/production` | Releases repo CI — production | `GitHubActionsDeployRole-{env}-production` |
| `/{env}/bootstrap/platform-vars/staging` | Releases repo CI — staging | `GitHubActionsDeployRole-{env}-staging` |
| `/{env}/bootstrap/deploy-input` | Handlers repo CI | `GitHubActionsHandlersRole-{env}-production` |
| `/{env}/bootstrap/ecs-vpc` | Handlers EC2 VPC ID (`compute_type=ec2` only) | releases + handlers OIDC roles |
| `/{env}/bootstrap/ecs-subnets` | Comma-separated subnet IDs | releases + handlers OIDC roles |
| `/{env}/bootstrap/ecs-security-groups` | Handlers EC2 security group ID | releases + handlers OIDC roles |

Each JSON envelope has `GITHUB_REPOSITORY`, `ENVIRONMENT`, `VARS`, and `SECRETS` (always `{}`). Application secrets (e.g. `OPENAI_API_KEY`) are **repo secrets** in GitHub, not in SSM.

`ECS_VPC`, `ECS_SUBNETS`, and `ECS_SECURITY_GROUPS` are **not** inside the JSON envelopes (CloudFormation `Fn::If` tokens cannot be serialized with `Fn::to_json_string`). CI/CD must read the three `ecs-*` parameters and merge them into runtime `VARS` as `ECS_VPC`, `ECS_SUBNETS`, and `ECS_SECURITY_GROUPS`.

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

Configure GitHub Actions workflows to:

1. Assume the OIDC role created by the stack (`AWS_GITHUB_OIDC_ROLE_ARN` — set by the customer in the workflow).
2. Read the SSM parameter for that repo/stage:

```bash
# Releases production example
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/platform-vars/production" \
  --query Parameter.Value --output text | jq -r '.VARS.BASE_URL'

# Handlers example
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/deploy-input" \
  --query Parameter.Value --output text | jq -r '.VARS.LAMBDA_HANDLERS_FUNCTION_NAME'

# Merge ECS network into platform-vars or deploy-input (recommended):
python bootstrap/helpers/merge_bootstrap_ssm.py "${ENV}" production \
  --aws-profile "$AWS_PROFILE" --aws-region "$AWS_REGION" > platform_vars.production.json

python bootstrap/helpers/merge_bootstrap_ssm.py "${ENV}" deploy-input \
  --aws-profile "$AWS_PROFILE" --aws-region "$AWS_REGION" > deploy_input.json
```

Or merge manually in shell:

```bash
ECS_VPC=$(aws ssm get-parameter --name "/${ENV}/bootstrap/ecs-vpc" --query Parameter.Value --output text)
ECS_SUBNETS=$(aws ssm get-parameter --name "/${ENV}/bootstrap/ecs-subnets" --query Parameter.Value --output text)
ECS_SG=$(aws ssm get-parameter --name "/${ENV}/bootstrap/ecs-security-groups" --query Parameter.Value --output text)
# jq ... | jq --arg vpc "$ECS_VPC" --arg subnets "$ECS_SUBNETS" --arg sg "$ECS_SG" \
#   '.VARS.ECS_VPC=$vpc | .VARS.ECS_SUBNETS=$subnets | .VARS.ECS_SECURITY_GROUPS=$sg'
```

`bootstrap/helpers/inject_github_env_vars.py` remains in the repo as a legacy utility but is **not** part of the bootstrap flow.

---

## Flow summary

```text
python3.12 -m venv bootstrap/venv  (on this machine — do not copy venv/)
  → bash bootstrap/setup-venvs.sh
  → customer-config.json
  → synth  →  bootstrap/output/<env>/  (templates + state at root; cdk/ for CDK deploy)
  → deploy stack-a (builds seed image automatically) → deploy stack-b → write-state
  → CI/CD (GitHub Actions via OIDC reads SSM, deploys images to ECR/Lambda)
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
cd <infra-installer>/bootstrap/output/<env>/cdk
export ENV=<env>
export AWS_PROFILE=<aws-profile>

cdk destroy "$ENV-stack-b" --app "python app.py" --output . --force --profile "$AWS_PROFILE"
cdk destroy "$ENV-stack-a" --app "python app.py" --output . --force --profile "$AWS_PROFILE"
```

**Extension resources (in `<env>-stack-b`):**

| Resource | On `<env>-stack-b` delete |
|----------|---------------------------|
| S3 `{env}-threat-events-{account}` | `DeletionPolicy: Delete` + CDK auto-empty custom resource |
| IAM `{env}_actions_tt_policy` | Deleted; detached from `{env}_tt_role`, `{env}-handlers-role`, `{env}-handlers-ecs-task` |
| Platform roles in `<env>-stack-a` | **Not** deleted by `<env>-stack-b` |

**S3 caveat:** if the auto-empty step fails (e.g. object lock, permissions), bucket deletion blocks stack delete until the bucket is emptied manually.

Extension resources are **scoped per platform env** (`{env}-threat-events-{account}`, `{env}_actions_tt_policy`). Each environment's `<env>-stack-b` manages its own bucket and policy in the account.

The account-level OIDC provider is **not** deleted (shared across environments).

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

## Appendix: CloudFormation deploy (full script)

Copy-paste flow from `bootstrap/output/<env>/`. Assumes `AWS_PROFILE` and `AWS_REGION` are set.

```bash
export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

cd <infra-installer>/bootstrap/output/${ENV}

# Stack A — add CreateGitHubOIDC=true only if the account lacks the GitHub OIDC provider
# Stack A also builds and pushes the seed image (CodeBuild custom resource);
# the deploy does not complete until the build succeeds. No manual step follows.
aws cloudformation deploy \
  --template-file "${ENV}-stack-a.template.json" \
  --stack-name "${ENV}-stack-a" \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

# Stack B — use the data bucket from stack-a to upload the template (large templates)
cd <infra-installer>/bootstrap/output/${ENV}

BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "${ENV}-stack-a" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" \
  --output text)

echo "Data bucket: ${BUCKET}"

aws cloudformation deploy \
  --template-file "${ENV}-stack-b.template.json" \
  --stack-name "${ENV}-stack-b" \
  --capabilities CAPABILITY_NAMED_IAM \
  --s3-bucket "${BUCKET}" \
  --s3-prefix "cloudformation/templates" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

To create the GitHub OIDC provider on first deploy in a new account, add to stack-a:

```bash
  --parameter-overrides CreateGitHubOIDC=true \
```

To use an existing VPC and subnets for handlers EC2 capacity (when `compute_type=ec2`), add to stack-b:

```bash
  --parameter-overrides \
    HandlersNetworkMode=existing \
    ExistingVpcId=vpc-0123456789abcdef0 \
    ExistingSubnetIds=subnet-aaa,subnet-bbb
```

`ExistingSubnetIds` must belong to `ExistingVpcId` and should span at least two Availability Zones.

---

## Appendix: Minimum Renglo setup (no external handlers)

Use this path when you only need the **Renglo platform** — backend API, Cognito, DynamoDB, and a handlers Lambda — without an extension repo (e.g. Arbitium) and without ECS/EC2 **external handler** capacity.

### What you get

| Included | Not included |
|----------|--------------|
| Stack A: Cognito, S3, DynamoDB, backend ECR, seed CodeBuild, tenant IAM, CodeDeploy, releases OIDC | Extension S3 buckets and IAM (`actions_tt_policy`, threat-events bucket, …) |
| Stack B: backend Lambda + REST/WebSocket API Gateway | Extension blueprints in DynamoDB |
| Handlers Lambda (`{env}-handlers`) with seed stub + handlers IAM + handlers OIDC | ECS cluster, handlers ECR, results bucket, EC2 ASG |
| SSM bootstrap config (`platform-vars/*`, `deploy-input`) | `/{env}/bootstrap/ecs-*` parameters |
| | `EXTERNAL_HANDLERS_ECS_HANDLERS` routing to ECS tasks |

Handlers run in **Lambda only** (`compute_type: lambda_only`). There is no separate extension handlers repo to clone and no ECS task dispatch for heavy extension workloads.

### Repos to clone

Only the three platform repos — **do not** clone an extension folder:

```bash
mkdir infra-installer && cd infra-installer
git clone https://github.com/renglo/bootstrap.git bootstrap
git clone https://github.com/renglo/launcher.git launcher
git clone https://github.com/renglo/extensions-service.git extensions-service
```

`extensions-service` is still required: CDK synth imports `compute_stack.py` from it to define the handlers Lambda and OIDC roles.

### `customer-config.json`

Copy the example and use a **minimal** config — omit `extension_path` and set `compute_type` to `lambda_only`:

```bash
cd launcher/cdk
cp customer-config.example.json customer-config.json
```

Example (`launcher/cdk/customer-config.json`):

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

Notes:

- **`extension_path`** — leave out (or set to `""`). Stack B will not create extension resources and synth will not emit `extension-state.json` or `extension-blueprints/`.
- **`github_handlers_repo`** — optional. Defaults to `github_repo` when omitted. Use one repo for both backend and handlers CI, or set a separate handlers repo if you prefer.
- **`enable_staging`** — set `false` for a single production stage (fewer Lambdas, APIs, and OIDC roles). Keep `true` if you want staging parity with production.
- **`ec2_*` fields** — not used with `lambda_only`; omit them.

### Install flow

Follow the main guide ([§1–§6](#1-clone-repos)) with the config above. Summary:

```bash
# 1. Python 3.12 + bootstrap venv on this machine (once)
cd <infra-installer>   # workspace root, e.g. ops/
rm -rf bootstrap/venv  # if copied from Windows or another machine
python3.12 -m venv bootstrap/venv
bash bootstrap/setup-venvs.sh

# 2. Edit launcher/cdk/customer-config.json (see example above)

# 3. Synth (uses bootstrap/venv automatically — no need to activate)
python3.12 bootstrap/install.py synth
# Output: bootstrap/output/<env>/  (no extension-blueprints/)

#4 PRE-CONDITION. If this is the first time running CDK in this AWS account and Region you need to bootstrap it


export AWS_PROFILE=<your-profile>
export AWS_REGION=us-east-1
export AWS_ACCOUNT=<your-aws-account>

cdk bootstrap aws://${AWS_ACCOUNT}/${AWS_REGION} --profile "$AWS_PROFILE"

# 4. Deploy — stack-a → stack-b (stack-a builds the seed image automatically)
export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

cd bootstrap/output/${ENV}
# Stack A builds and pushes the seed image (CodeBuild custom resource);
# the deploy blocks until the build succeeds.
aws cloudformation deploy \
  --template-file "${ENV}-stack-a.template.json" \
  --stack-name "${ENV}-stack-a" \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

cd bootstrap/output/${ENV}
aws cloudformation deploy \
  --template-file "${ENV}-stack-b.template.json" \
  --stack-name "${ENV}-stack-b" \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

cd <infra-installer>
python bootstrap/install.py write-state \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"


First deploy in a new AWS account: add `--parameter-overrides CreateGitHubOIDC=true` to the stack-a command (see [§5a](#5a-cloudformation-cli-recommended)).

Stack-b is smaller than an extension + EC2 setup; `--s3-bucket` is usually unnecessary unless the template exceeds the CLI size limit.

### Post-deploy and CI/CD

This section is about GitHub Actions in the releases repo. AWS bootstrap already created the IAM roles and SSM parameters; GitHub runs the workflows that assume those roles and deploy.

In this repo the workflows are already written under ops/productora-releases/.git/workflows. You mainly publish that folder to GitHub and wire it to your AWS env — you don't need to author YAML from scratch unless you're forking the pipeline.

Run **write-state** after stack-b ([§6](#6-bootstrap-config-in-ssm-write-state-after-stack-b)) to populate SSM.

Configure GitHub Actions in your **releases** repo to:

1. Assume `GitHubActionsDeployRole-{env}-production` (and staging if enabled).
2. Read `/{env}/bootstrap/platform-vars/production` from SSM.
3. Build and deploy the backend image to ECR and Lambda via CodeDeploy.

If you deploy handlers code separately, configure the **handlers** repo (or the same repo) to:

1. Assume `GitHubActionsHandlersRole-{env}-production`.
2. Read `/{env}/bootstrap/deploy-input` from SSM.
3. Build a Lambda zip and update `{env}-handlers` (no ECR push or ECS task definition).

With `lambda_only`, skip ECS network merge — there are no `/{env}/bootstrap/ecs-*` parameters ([§6](#6-bootstrap-config-in-ssm-write-state-after-stack-b)).

Verify SSM after write-state:

```bash
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/platform-vars/production" \
  --query Parameter.Value \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" | jq '.VARS | {BASE_URL, LAMBDA_EXTERNAL_HANDLERS_ARN, ECS_CLUSTER}'

aws ssm get-parameter \
  --name "/${ENV}/bootstrap/deploy-input" \
  --query Parameter.Value \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" | jq '.VARS | {LAMBDA_HANDLERS_FUNCTION_NAME, ECR_IMAGE_URI, ECS_CLUSTER}'
```

Expect `ECS_CLUSTER` and `ECR_IMAGE_URI` to be empty; `LAMBDA_HANDLERS_FUNCTION_NAME` should be `{env}-handlers`.

### Adding an extension later

When you need external handlers (extension-specific IAM, blueprints, ECS/EC2 capacity):

1. Clone the extension repo as a sibling folder (e.g. `arbitiumlab`).
2. Add `"extension_path": "arbitiumlab"` and set `"compute_type"` to `fargate` or `ec2` in `customer-config.json`.
3. Set `"github_handlers_repo"` to the extension/handlers repo if it differs from `github_repo`.
4. Re-run `python bootstrap/install.py synth` and update stack-b (see [§5](#5-deploy-stacks-to-aws)).

Extension resources are scoped per environment; stack-a does not need to be redeployed unless platform settings change.

---

## Reference

- CDK stacks and details: [launcher/README.md](../launcher/README.md)
- Legacy flow (boto3 `deploy_environment.py`): [launcher/ENVIRONMENT_README.md](../launcher/ENVIRONMENT_README.md)
- JSON payloads and design notes: [ARCHITECTURE.md](ARCHITECTURE.md)
