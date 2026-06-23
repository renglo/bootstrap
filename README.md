# Bootstrap — environment installation (CDK + CloudFormation)



Step-by-step guide from scratch. All examples use **bash** (or Linux/macOS)





---



## Local requirements



- AWS CLI configured (`aws configure list-profiles`)

- Python 3.12

- Node.js + CDK CLI (`npm install -g aws-cdk`)

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



- `env_name` — prefix for AWS resources and name of the synth output folder (`bootstrap/output/<env_name>/`)

- `github_repo` — **releases** repo (backend OIDC)

- `github_handlers_repo` — **handlers** repo (extensions OIDC)

- `extension_path` — sibling folder with `installer/infra/cdk_extension.json` (e.g. `arbitiumlab`); extension resources are synthesized into **`<env>-stack-b`**

- `compute_type` — `lambda_only` | `fargate` | `ec2`

- `ec2_min_instances` / `ec2_desired_instances` / `ec2_max_instances` — only when `compute_type` is `ec2`

Platform-wide defaults (`architecture`, backend seed image URI/tag): `launcher/cdk/platform_defaults.json` (copied to `bootstrap/output/<env>/` on synth).



---



## 4. (Optional) GitHub OIDC provider — once per AWS account



**You can skip this step** if the customer confirms the GitHub Actions OIDC provider already exists in the account (`token.actions.githubusercontent.com`).



Check:



```bash

aws iam list-open-id-connect-providers --profile <aws-profile>

# Expected: arn:aws:iam::<aws-account>:oidc-provider/token.actions.githubusercontent.com

```



If it does not exist, create it (idempotent):



```bash

cd <infra-installer>

python bootstrap/install.py ensure-oidc \

  --aws-profile <aws-profile> \

  --aws-region <aws-region>

```



---



## 5. Generate CloudFormation templates



```bash

cd <infra-installer>

python bootstrap/install.py synth

```



Output: `bootstrap/output/<env>/` — self-contained deploy package:

- `<env>-stack-a.template.json`, `<env>-stack-b.template.json`, `manifest.json`
- `app.py`, `stacks/`, `extensions/`, `customer-config.json`, `platform_defaults.json`
- `extension/` — bundled extension infra (when `extension_path` is set)
- `extension-state.json`, `extension-blueprints/` (when extension configured)
- `upload_seed_image.py`, `seed-image/` — seed image build/push (no dependency on `launcher/`)



---



## 6. Deploy stacks to AWS



From `bootstrap/output/<env>`. Required order:



```bash

cd <infra-installer>/bootstrap/output/<env>



export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>


# 6a. Stack A — auth, storage, runtime (ECR, IAM, CodeDeploy, OIDC)

cdk deploy "$ENV-stack-a" \
  --app "python app.py" \
  --output . \
  --require-approval never \
  --profile "$AWS_PROFILE"

# 6b. Seed image — required before stack-b

python upload_seed_image.py \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"

# 6c. Stack B — app + compute + extension
#     With compute_type=ec2, pass VPC and subnets:
cdk deploy "$ENV-stack-b" \
  --app "python app.py" \
  --output . \
  --exclusively \
  --parameters VpcId=<vpc-id> \
  --parameters 'SubnetIds=<subnet-ids>' \
  --require-approval never \
  --profile "$AWS_PROFILE"

```



**Notes:**



- With `compute_type` = `fargate` or `lambda_only`, omit `--parameters VpcId` and `SubnetIds`.

- Alternative for the customer: deliver the `.template.json` files and use `aws cloudformation deploy` with stack names `<env>-stack-a` and `<env>-stack-b`.

- All IAM deploys require `--capabilities CAPABILITY_NAMED_IAM` when using `aws cloudformation deploy`.



CloudFormation CLI example (`<env>-stack-b` with EC2 compute):



```bash

aws cloudformation deploy \

  --template-file <env>-stack-b.template.json \

  --stack-name <env>-stack-b \

  --capabilities CAPABILITY_NAMED_IAM \

  --parameter-overrides \

    VpcId=<vpc-id> \

    SubnetIds=<subnet-ids> \

  --profile "$AWS_PROFILE" \

  --region "$AWS_REGION"

```



---



## 7. Post-deploy: write state



Reads CloudFormation outputs (including extension resources via `extension-state.json` from synth output), uploads platform and extension blueprints to DynamoDB, and writes state to S3 (`s3://<bucket>/params/`):



```bash

cd <infra-installer>

python bootstrap/install.py write-state \

  --env-name <env> \

  --aws-profile <aws-profile> \

  --aws-region <aws-region>

```



The extension state manifest (`extension-state.json`) is generated during `install.py synth` when `extension_path` is set. It lists which CloudFormation outputs from **`<env>-stack-b`** become runtime env vars vs inventory-only metadata.



Files written to S3 (bucket = `<env>-<aws-account>-<aws-region>` lowercase):



| S3 object | Purpose |

|-----------|---------|

| `params/platform_vars.production.json` | GitHub Environment **production** for the **releases** repo |

| `params/platform_vars.staging.json` | GitHub Environment **staging** for the **releases** repo |

| `params/deploy_input.json` | GitHub Environment for the **handlers** repo |

| `params/platform_resources.json` | Full environment inventory |

| `params/env_config.py` | Python app config |



---



## 8. Sync GitHub Environment variables



Requires: `gh auth login`



Download JSON from S3 and push to GitHub:



```bash

export ENV=<env>

export AWS_PROFILE=<aws-profile>

export AWS_REGION=<aws-region>

export BUCKET="${ENV}-<aws-account>-${AWS_REGION}"   # all lowercase



mkdir -p /tmp/state

aws s3 cp "s3://${BUCKET}/params/platform_vars.production.json" /tmp/state/production.json --profile "$AWS_PROFILE"

aws s3 cp "s3://${BUCKET}/params/platform_vars.staging.json"    /tmp/state/staging.json    --profile "$AWS_PROFILE"

aws s3 cp "s3://${BUCKET}/params/deploy_input.json"            /tmp/state/deploy_input.json --profile "$AWS_PROFILE"



cd <infra-installer>



# Releases repo (backend) — production and staging

python bootstrap/helpers/inject_github_env_vars.py --json /tmp/state/production.json

python bootstrap/helpers/inject_github_env_vars.py --json /tmp/state/staging.json



# Handlers repo (extensions)

python bootstrap/helpers/inject_github_env_vars.py --json /tmp/state/deploy_input.json

```



`inject_github_env_vars.py` creates the GitHub Environment if missing, updates VARS/SECRETS, and removes keys that are no longer in the JSON.



---



## Flow summary



```text

setup-venvs

  → customer-config.json

  → [optional] ensure-oidc

  → synth  →  bootstrap/output/<env>/

  → cdk deploy <env>-stack-a → upload_seed_image → cdk deploy <env>-stack-b

  → write-state

  → inject_github_env_vars (production, staging, deploy_input)

  → CI/CD (GitHub Actions) deploys real images to ECR/Lambda

```



---



## Stack deletion (teardown)



Delete stacks in **reverse deploy order**:



```bash

cd <infra-installer>/bootstrap/output/<env>

export ENV=<env>
export AWS_PROFILE=<aws-profile>



cdk destroy "$ENV-stack-b" --app "python app.py" --output . --force --profile "$AWS_PROFILE"

cdk destroy "$ENV-stack-a" --app "python app.py" --output . --force --profile "$AWS_PROFILE"

```



**Extension resources (in `<env>-stack-b`):**



| Resource | On `<env>-stack-b` delete |

|----------|---------------------|

| S3 `{env}-threat-events-{account}` | `DeletionPolicy: Delete` + CDK auto-empty custom resource |

| IAM `{env}_actions_tt_policy` | Deleted; detached from `{env}_tt_role`, `{env}-handlers-role`, `{env}-handlers-ecs-task` |

| Platform roles in `<env>-stack-a` | **Not** deleted by `<env>-stack-b` |



**S3 caveat:** if the auto-empty step fails (e.g. object lock, permissions), bucket deletion blocks stack delete until the bucket is emptied manually.



Extension resources are **scoped per platform env** (`{env}-threat-events-{account}`, `{env}_actions_tt_policy`). Each environment's `<env>-stack-b` manages its own bucket and policy in the account.



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



## Reference



- CDK stacks and details: [launcher/README.md](../launcher/README.md)

- Legacy flow (boto3 `deploy_environment.py`): [launcher/ENVIRONMENT_README.md](../launcher/ENVIRONMENT_README.md)

- JSON payloads and design notes: [ARCHITECTURE.md](ARCHITECTURE.md)

