# Bootstrap — environment installation (CDK + CloudFormation)

Step-by-step guide from scratch. Examples use **bash** (Linux/macOS/WSL). For a copy-paste CloudFormation flow, see [Appendix: CloudFormation deploy](#appendix-cloudformation-deploy-full-script).

---

## Local requirements

- AWS CLI configured (`aws configure list-profiles`)
- Python 3.12
- Node.js + CDK CLI (`npm install -g aws-cdk`) — only if using `cdk deploy`
- Docker (for the backend seed image)
- Git
- GitHub CLI `gh` (only for uploading vars/secrets to GitHub Environments)

---

## 1. Clone repos

Expected workspace layout:

```text
<infra-installer>/
  bootstrap/
  launcher/
  extensions-service/
  extension-repo/          # or another handlers repo (optional, per extension)
```

```bash
cd <infra-installer>

# If you do not have the infra-installer monorepo yet:
# git clone <infra-installer-url> .
# git clone <launcher-url> launcher
# git clone <extensions-service-url> extensions-service
```

---

## 2. Virtualenvs

Idempotent — run once (or when `requirements.txt` changes):

```bash
cd <infra-installer>
bash bootstrap/setup-venvs.sh
```

Installs `bootstrap/venv` (boto3 + CDK). `install.py` automatically re-execs with that Python.

---

## 3. Configure the environment

```bash
cd <infra-installer>/launcher/cdk
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

```bash
cd <infra-installer>
python bootstrap/install.py synth
```

Output: `bootstrap/output/<env>/`

**Env root** (CloudFormation deploy + post-deploy):

- `<env>-stack-a.template.json`, `<env>-stack-b.template.json`
- `extension-state.json`, `extension-blueprints/` (when `extension_path` is set)

**`cdk/`** subfolder (only for `cdk deploy`):

- `app.py`, `stacks/`, `extensions/`, `customer-config.json`, `platform_defaults.json`
- `extension/` — bundled extension infra (when `extension_path` is set)
- CDK assembly (`manifest.json`, `*.assets.json`, `asset.*/`, …)

Seed image (between stack-a and stack-b): `python bootstrap/upload_seed_image.py` from `<infra-installer>/` (not copied into output).

---

## 5. Deploy stacks to AWS

Required order: **stack-a → seed image → stack-b**

### 5a. CloudFormation CLI (recommended)

From `bootstrap/output/<env>/` (templates at env root). See the [appendix](#appendix-cloudformation-deploy-full-script) for a full script including `--s3-bucket` on stack-b.

```bash
cd <infra-installer>/bootstrap/output/<env>

export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

# Stack A — pass CreateGitHubOIDC=true only if the account lacks
# token.actions.githubusercontent.com as an IAM OIDC provider.
aws cloudformation deploy \
  --template-file "${ENV}-stack-a.template.json" \
  --stack-name "${ENV}-stack-a" \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"
```

```bash
cd <infra-installer>
python bootstrap/upload_seed_image.py \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"
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

### 5b. CDK CLI (alternative)

From `bootstrap/output/<env>/cdk/`:

```bash
cd <infra-installer>/bootstrap/output/<env>/cdk

export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

cdk deploy "$ENV-stack-a" \
  --app "python app.py" \
  --output . \
  --require-approval never \
  --profile "$AWS_PROFILE"
```

```bash
cd <infra-installer>
python bootstrap/upload_seed_image.py \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"
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

## 6. Bootstrap config in SSM (automatic on stack-b)

Stack-b writes bootstrap config to **SSM Parameter Store** and uploads blueprints to DynamoDB (custom resource). No post-deploy script is required.

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

**Verify after stack-b:**

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
setup-venvs
  → customer-config.json
  → synth  →  bootstrap/output/<env>/  (templates + state at root; cdk/ for CDK deploy)
  → deploy stack-a → bootstrap/upload_seed_image.py → deploy stack-b
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
aws cloudformation deploy \
  --template-file "${ENV}-stack-a.template.json" \
  --stack-name "${ENV}-stack-a" \
  --capabilities CAPABILITY_NAMED_IAM \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION"

# Seed image (required before stack-b)
cd <infra-installer>
python bootstrap/upload_seed_image.py \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"

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

## Reference

- CDK stacks and details: [launcher/README.md](../launcher/README.md)
- Legacy flow (boto3 `deploy_environment.py`): [launcher/ENVIRONMENT_README.md](../launcher/ENVIRONMENT_README.md)
- JSON payloads and design notes: [ARCHITECTURE.md](ARCHITECTURE.md)
