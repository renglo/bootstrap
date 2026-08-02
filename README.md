# Bootstrap — environment installation (CDK + CloudFormation)

Step-by-step guide from scratch. Examples use **bash** (Linux/macOS/WSL).

**Default path (kick the tires):** install the **Renglo platform only** — Cognito, DynamoDB, S3, SES, and handlers Lambda (`compute_type: lambda_only`) — then run the app **locally**. Follow **§1–§6**, then **[§7 Path B](#path-b--local-development-default--no-cicd)**. No GitHub Actions and no cloud backend deploy.

**Team invite email is required.** Configure SES in [§3.3](#step-33--set-up-application-email-required) before synth; finish invites in §7.

**Cloud production (later):** when you want the hosted API live, use [§7 Path A](#path-a--cloud-go-live-optional-later) and the optional [§8 CI/CD contract](#8-cicd-contract-optional--cloud-production-only).

**Advanced:** extensions and/or ECS/EC2 handlers — see [Advanced](#advanced-extensions-and-ecsec2-handlers).

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



### Step 3.1 — Create `customer-config.json`

```bash
cd launcher/cdk
cp customer-config.example.json customer-config.json
```



### Step 3.2 — Set platform fields

Edit `launcher/cdk/customer-config.json`. Omit `extension_path`. Use `compute_type: lambda_only` for a standard install:


| Field                        | Purpose                                                                    |
| ---------------------------- | -------------------------------------------------------------------------- |
| `env_name`                   | Prefix for AWS resources and synth output (`bootstrap/output/<env_name>/`) |
| `aws_account` / `aws_region` | Target AWS account and region                                              |
| `github_repo`                | **Releases** repo (backend CI via OIDC)                                    |
| `enable_staging`             | `false` = production only; `true` = production + staging                   |
| `compute_type`               | `lambda_only` — handlers deploy as a Lambda zip from CI                    |


Optional: `github_handlers_repo` defaults to `github_repo` when omitted.

### Step 3.3 — Set up application email (required)

Team invite email is core platform infrastructure — same tier as Cognito and DynamoDB. Cognito **self-signup is disabled**; after the first admin, **every new user arrives via invite email**. You must configure SES **before synth** (stack-a creates the identity during deploy).

**1. Choose a from-address** on a domain or inbox your organization controls (e.g. `noreply@your-app-domain.com`). You do not need a mailbox for no-reply when using domain verification.

**2. Find your Route53 hosted zone** (skip to step 3 if DNS for that domain is not in this AWS account):

```bash
export AWS_PROFILE=<aws-profile>

aws route53 list-hosted-zones \
  --profile "$AWS_PROFILE" \
  --query "HostedZones[].[Name,Id]" --output table
```

Pick the zone whose name matches the domain of `email_from` (e.g. `your-app-domain.com.` for `noreply@your-app-domain.com`).

**3. Add email fields to** `customer-config.json`**:**

When the domain’s public DNS is in Route53 **in this account** (usual case):

```json
{
  "env_name": "myenv",
  "aws_account": "123456789012",
  "aws_region": "us-east-1",
  "github_repo": "MyOrg/my-releases-repo",
  "enable_staging": false,
  "compute_type": "lambda_only",
  "email_from": "noreply@your-app-domain.com",
  "email_identity_type": "domain",
  "email_hosted_zone_id": "Z0123456789EXAMPLE"
}
```

Replace `Z0123456789EXAMPLE` with the zone id from step 2. Stack-a creates the SES domain identity and writes DKIM records automatically (`SesDnsMode=route53_auto`).

**If DNS is outside this AWS account:** use the same JSON but **omit** `email_hosted_zone_id`. After deploy, copy stack-a outputs `DkimRecord1Name` / `DkimRecord1Value` (and 2, 3) into your DNS provider as CNAME records (`SesDnsMode=manual_dns`).

**If you only have a single inbox** (no DNS control): set `"email_identity_type": "email"`. SES sends a verification link to that inbox during §7.2 (`SesDnsMode=email_inbox`).


| Field                  | Purpose                                                            |
| ---------------------- | ------------------------------------------------------------------ |
| `email_from`           | **Required** — SES from-address for team invite email              |
| `email_identity_type`  | **Required** — `domain` (preferred) or `email`                     |
| `email_hosted_zone_id` | Route53 zone id when DNS is in this account; omit for external DNS |


After stack-b succeeds, follow **[§7](#7-after-bootstrap--make-the-app-usable)** (default Path B: local API — no CI/CD). `write-state` alone does **not** create SES — deploy stack-a first.

### What the default install creates


| Included                                                                            | Not included (see [Advanced](#advanced-extensions-and-ecsec2-handlers)) |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Stack A: Cognito, S3, DynamoDB, tenant IAM, releases OIDC, backend ECR + CodeDeploy | Extension S3 buckets and IAM                                            |
| Stack A: SES domain/email identity + invite from-address (`email_from`)             | Verified personal/work from-addresses unrelated to your app             |
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

When stack-b finishes, continue with **[§7 — After bootstrap](#7-after-bootstrap--make-the-app-usable)** (Path B by default).

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

When stack-b finishes, continue with **[§7 — After bootstrap](#7-after-bootstrap--make-the-app-usable)** (Path B by default).

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

Relevant `VARS` for invites (after SES is configured and stacks are deployed):


| Key                  | Purpose                                                             |
| -------------------- | ------------------------------------------------------------------- |
| `FROM_EMAIL`         | SES from-address (`email_from` from customer-config)                |
| `FE_BASE_URL`        | Cloud console URL (Amplify) — production invite links and CORS      |
| `INVITE_FE_BASE_URL` | Optional override for invite links only (local dev; not set in SSM) |
| `BASE_URL`           | API Gateway URL (backend), not the invite link host                 |


With `lambda_only`, there are no `ecs-*` parameters — skip ECS network merge in CI.

**Verify after write-state:**

```bash
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/platform-vars/production" \
  --query Parameter.Value \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" | jq '.VARS | {FROM_EMAIL, FE_BASE_URL, BASE_URL}'
```

Continue with **[§7 — After bootstrap](#7-after-bootstrap--make-the-app-usable)** (Step 7.1 repeats `write-state` if you already ran it here — safe to run twice).

---



## 7. After bootstrap — make the app usable

§1–§6 provision AWS infrastructure only. They do **not** require GitHub Actions. After stack-b, the cloud API is still a seed stub (`seed image ok`); that is fine for local development — you run the real API on your machine.

**Default (every new / test project):** Steps **7.1 → 7.7** below (**Path B**). No CI/CD.

**Later (optional):** [Path A](#path-a--cloud-go-live-optional-later) when you want a hosted production API — that is when [§8 CI/CD](#8-cicd-contract-optional--cloud-production-only) matters.

### Step 7.1 — Write bootstrap config to SSM

```bash
export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>

cd <workspace>

python3.12 bootstrap/install.py write-state \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"
```

Confirm `FROM_EMAIL` and `FE_BASE_URL` are set:

```bash
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/platform-vars/production" \
  --query Parameter.Value \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  | jq '.VARS | {FROM_EMAIL, FE_BASE_URL, BASE_URL, AMPLIFY_CONSOLE_URL}'
```



### Step 7.2 — Verify SES (sender + test recipients)

**1. Confirm the sender domain** (from `email_from` — everything after `@`):

```bash
EMAIL_DOMAIN=your-app-domain.com   # noreply@example.com → example.com

aws ses get-identity-verification-attributes \
  --identities "$EMAIL_DOMAIN" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Wait until `"VerificationStatus": "Success"`. If status stays `Pending`, check stack-a output `SesDnsMode` and (for external DNS) add the `DkimRecord*` CNAMEs from the stack outputs.

**2. Add verified recipient addresses for development (SES sandbox)**

New AWS accounts start in the **SES sandbox**: you can send from your verified domain, but only **to** addresses you verify first. For Path B (kicking the tires) you do **not** need SES production access — verify each invitee email you will test with:

```bash
# Replace with the inbox that will receive invite emails during testing
RECIPIENT=teammate@example.com

aws ses verify-email-identity \
  --email-address "$RECIPIENT" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

The recipient clicks the confirmation link in their inbox. Check status:

```bash
aws ses get-identity-verification-attributes \
  --identities "$RECIPIENT" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Repeat for each test address. You can also verify identities in **AWS Console → SES → Identities → Create identity → Email address**.

**Later (cloud production only):** when you need to invite arbitrary users without verifying each inbox, request [SES production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html). Not required for local Path B.

### Step 7.3 — Generate local developer config bundle

Produces ready-to-copy `env_config.py`, `.env.development`, and `run.sh` from SSM — no manual copy/paste from `jq`. The infrastructure operator shares this folder with application developers (who may be on a different machine).

```bash
python3.12 bootstrap/install.py write-local-config \
  --env-name "$ENV" \
  --aws-profile "$AWS_PROFILE" \
  --aws-region "$AWS_REGION"
```

**Output:** `bootstrap/output/${ENV}/local-dev/`


| File               | Developer copies to                               |
| ------------------ | ------------------------------------------------- |
| `env_config.py`    | `dev/renglo-api/env_config.py`                    |
| `run.sh`           | `dev/renglo-api/run.sh`                           |
| `.env.development` | `console/.env.development`                        |
| `README.md`        | handoff instructions (do not copy into app repos) |


Re-run `write-local-config` after any infrastructure or SSM change (new tables, Cognito IDs, `FROM_EMAIL`, etc.) and send developers an updated bundle. Existing `SECRET_KEY` / `CSRF_SESSION_KEY` in the output file are preserved by default; use `--no-preserve-secrets` to rotate.

### Step 7.4 — Create the first admin user

Cognito **self-signup is disabled**. Create the first operator once (needed for local and cloud). Everyone else joins via team invite.

```bash
POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name "${ENV}-stack-a" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" \
  --output text)

ADMIN_EMAIL="you@your-domain.com"
aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username "$ADMIN_EMAIL" \
  --user-attributes Name=email,Value="${ADMIN_EMAIL}" Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Cognito emails a **temporary password** (copy it exactly — the period at the end of the sentence is punctuation, not part of the password).

**First login:** after stack-a redeploy, the Cognito invitation email includes a setup link and subject **“Complete your admin account setup”**. It points at the cloud Amplify console:

```text
https://production.<amplify-default-domain>/invite?setup=admin&email=<ADMIN_EMAIL>
```

For **Path B (local dev)** before the console is deployed to Amplify, use the local URL instead:

```text
http://127.0.0.1:5174/invite?setup=admin&email=<ADMIN_EMAIL>
```

On that screen the admin enters the temporary password, first and last name, email, and a new password. Cognito attributes and the user entity (`name`, `slot_a`, `email`) are saved before redirect to `/home`.

If they try `/login` with the temporary password instead, they are redirected to the same setup screen.

---



### Path B — Local development (default — no CI/CD)

**This is the golden path for kicking the tires.** You do **not** configure GitHub Actions, deploy a backend image, or wait for Amplify. The local API talks to cloud Cognito, DynamoDB, and SES using the files from Step 7.3.

#### Step 7.5 — Hand off config to developers

Send `bootstrap/output/${ENV}/local-dev/` (zip or shared drive). Developers copy the three files per `local-dev/README.md`.

#### Step 7.6 — Run local API and console

```bash
# Developer machine — after copying files from local-dev/
cd dev/renglo-api && ./run.sh          # terminal 1 — http://127.0.0.1:5001

cd console && npm run dev              # terminal 2 — http://127.0.0.1:5174
```

Developers need AWS credentials for the profile in `run.sh` (same account/region as bootstrap). Open `http://127.0.0.1:5174/login` and set a new password when Cognito prompts.

#### Step 7.7 — Test team invites (local)

1. Log in at `http://127.0.0.1:5174/login`.
2. Invite a teammate whose email you verified in Step 7.2 (sandbox recipients).
3. Invite emails use `INVITE_FE_BASE_URL` from the generated `env_config.py` (default `http://127.0.0.1:5174`). The invitee opens the link on a machine running the local console, or pastes the invite code from the email at `/invite`.

**You are done for local testing.** Stop here unless you need a hosted production API.

---



### Path A — Cloud go-live (optional, later)

Only when you want the **hosted** API and Amplify console live. Requires GitHub Actions in your releases repo — see **[§8](#8-cicd-contract-optional--cloud-production-only)**. Skip this entire path while developing locally.

#### Step 7.8 — Deploy application code (GitHub Actions)

Stack-b starts the backend Lambda on a **seed image**. CI must build and deploy the real backend and handlers (contract in §8).

1. Configure and run the releases workflow for **production** (and **staging** if enabled).
2. If handlers use a separate workflow, run it too.

If you re-deploy `<env>-stack-b` later, Lambda code resets to the seed image — re-run the releases pipeline afterward.

#### Step 7.9 — Confirm the cloud API is live

```bash
BASE_URL=$(aws ssm get-parameter \
  --name "/${ENV}/bootstrap/platform-vars/production" \
  --query Parameter.Value \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  | jq -r '.VARS.BASE_URL')

curl -s "${BASE_URL}/"
```

**Pass:** the response is not exactly `seed image ok`.

#### Step 7.10 — Test team invites (cloud)

Log in at `FE_BASE_URL` (Amplify), invite by email. Production invite links use `FE_BASE_URL` (leave `INVITE_FE_BASE_URL` unset on Lambda). Until you leave the SES sandbox (Step 7.2 “Later”), invite only verified recipient addresses — or request [SES production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html) when you are ready for real users.

---



## 8. CI/CD contract (optional — cloud production only)

**Skip this section** for local development (Path B). You only need it when following [Path A](#path-a--cloud-go-live-optional-later).

Stacks already create OIDC deploy roles and write SSM for CI. Configure GitHub Actions in your **releases** repo to:

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
  → customer-config.json  (§3.2 platform fields + §3.3 required email_from / SES)
  → synth  →  bootstrap/output/<env>/
  → cdk bootstrap  (once per account/region)
  → cdk deploy <env>-stack-a  →  cdk deploy <env>-stack-b
  → §7.1 write-state
  → §7.2 verify SES
  → §7.3 write-local-config  →  bootstrap/output/<env>/local-dev/
  → §7.4 admin-create-user
  → Path B (default): §7.5 handoff → §7.6 run local → §7.7 invites
       (stop here — no GitHub / no CI/CD)
  → Path A (optional later): §7.8–7.10 + §8 CI/CD when you want cloud production
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

Everything else stays the same: follow §1–§7 (Path B for local), then adjust config and re-deploy stack-b.

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

