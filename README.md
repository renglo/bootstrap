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

**If you only have a single inbox** (no DNS control): set `"email_identity_type": "email"`. SES sends a verification link to that inbox during [Step 7.2](#step-72--verify-the-system-sender-from_email) (`SesDnsMode=email_inbox`).


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
| Stack A: AI amenities — S3 Vectors bucket, `rag-kb` index, RAG docs bucket, default Bedrock KB (`KB_ID`) | Extension-declared vector **indexes** (only when `extension_path` is set) |
| Stack B: backend Lambda + REST/WebSocket API Gateway                                | ECS cluster, handlers ECR, EC2 ASG                                      |
| Stack B: handlers Lambda (`{env}-handlers`) + handlers OIDC                         | `/{env}/bootstrap/ecs-*` SSM parameters                                 |
| SSM bootstrap config after write-state                                              | `EXTERNAL_HANDLERS_ECS_HANDLERS` routing to ECS tasks                   |


**About ECR:** the backend always uses a **container Lambda** (ECR + CodeDeploy) — that replaces the old Zappa zip deploy. You do not configure ECR manually for a new project; stack-a runs a seed build during deploy and CI pushes real images afterward. With `lambda_only`, **handlers** use a zip Lambda (no handlers ECR). Handlers ECR/ECS only apply when you switch to `fargate` or `ec2` for heavy extension workloads.

Templates are **environment-agnostic**: account and region resolve via `AWS::AccountId` / `AWS::Region` at deploy time. Synth does not need (or accept) `aws_account` / `aws_region` in the config.

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

Continue with **[§7 — After bootstrap](#7-after-bootstrap--make-the-app-usable)**. Start at [Step 7.1](#step-71--confirm-bootstrap-config-in-ssm) to confirm SSM — you do not need to run `write-state` again if you just ran it above.

---



## 7. After bootstrap — make the app usable

You now have AWS infrastructure. You do **not** have a working app yet: nobody can log in, and the hosted API is still a placeholder (`seed image ok`). That is expected. The next steps finish email, create the first user, and run the real API on your laptop.

For a new or test project, follow **7.1 → 7.8** (**Path B**). You do not need GitHub Actions or a cloud deploy.

When you later want the API hosted in AWS, come back to [Path A](#path-a--cloud-go-live-optional-later). That is the only time you need [§8 CI/CD](#8-cicd-contract-optional--cloud-production-only).

### Three kinds of email (read this first)

People join this app by **invite email**, not by signing up themselves. That means three different addresses are involved, and they are easy to mix up:

1. **Who the system writes from.** This is `email_from` in `customer-config.json` — typically something like `noreply@your-app-domain.com`. Invite emails show this address in the From field. You prove to AWS that you own it in [Step 7.2](#step-72--verify-the-system-sender-from_email).
2. **The first person who can log in.** There is no public registration. You create one admin by hand ([Step 7.3](#step-73--create-the-first-admin-user)). AWS Cognito (the login service) emails that person a temporary password. That message does **not** go through the app’s mail setup, so you do not need the recipient list in Step 7.4 first.
3. **Who the app is allowed to send mail to.** New AWS accounts start in an SES **sandbox**: the app can send from your verified From address, but only **to** inboxes you have confirmed. That list is for **invite emails the app sends**, not for the admin’s temporary password. You build it in [Step 7.4](#step-74--whitelist-outbound-recipients-ses-sandbox) after you know the admin’s address.

Work through the steps in order: check that config was saved (7.1), prove you own the From address (7.2), create the admin (7.3), then allow the inboxes that admin will invite (7.4). After that, generate developer files and run the app locally (7.5–7.8).

If you used a single external inbox in §3.3 (`email_identity_type: "email"`) instead of a domain, Step 7.2 is “click the confirmation link AWS sent you,” not a DNS change. You still do 7.3 and 7.4.

### Step 7.1 — Confirm bootstrap config in SSM

In [§6](#6-bootstrap-config-in-ssm-write-state-after-stack-b) you saved settings into AWS Parameter Store (SSM) so the app and, later, CI can find them. You already ran that command. Do **not** run it again unless you changed `customer-config.json` or redeployed the stacks.

If these are not already set in your terminal:

```bash
export ENV=<env>
export AWS_PROFILE=<aws-profile>
export AWS_REGION=<aws-region>
```

Check that the From address and console URL were saved:

```bash
aws ssm get-parameter \
  --name "/${ENV}/bootstrap/platform-vars/production" \
  --query Parameter.Value \
  --output text \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  | jq '.VARS | {FROM_EMAIL, FE_BASE_URL, BASE_URL, AMPLIFY_CONSOLE_URL}'
```

If those values are missing, run `write-state` once as in §6.

### Step 7.2 — Verify the system sender (`FROM_EMAIL`)

AWS will not send mail **from** an address it does not trust. This step is only that From address (`email_from`). It does **not** decide who can receive invites — that is Step 7.4.

Look up stack-a output `SesDnsMode` and follow **one** of the three cases below. That value comes from how you configured email in §3.3.

#### `route53_auto` — the domain’s DNS lives in Route53 in this account

Deploy already wrote the DNS records AWS needs. Confirm the **domain** (the part after `@` in `email_from`) is verified:

```bash
EMAIL_DOMAIN=your-app-domain.com   # noreply@example.com → example.com

aws ses get-identity-verification-attributes \
  --identities "$EMAIL_DOMAIN" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Wait until `"VerificationStatus": "Success"`.

#### `manual_dns` — the domain’s DNS is at another provider (GoDaddy, Cloudflare, …)

Copy stack-a outputs `DkimRecord1Name` / `DkimRecord1Value` (and 2, 3) into your DNS host as CNAME records. Then run the same domain check as `route53_auto` above.

#### `email_inbox` — you only have a single inbox (`email_identity_type: "email"`)

Skip DNS. When stack-a deployed, AWS emailed a confirmation link to `email_from`. Open that inbox, click the link, then check:

```bash
FROM_EMAIL=noreply@your-inbox-provider.com   # same as email_from

aws ses get-identity-verification-attributes \
  --identities "$FROM_EMAIL" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Wait until `"VerificationStatus": "Success"`.

If the inbox was not ready when stack-a deployed, or the link expired, **do not redeploy**. Ask SES to send the confirmation email again:

```bash
aws ses verify-email-identity \
  --email-address "$FROM_EMAIL" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

That command is safe to repeat. You can also resend from **AWS Console → SES → Identities**.

### Step 7.3 — Create the first admin user

Nobody can create an account from the login screen. You create the first person (the admin) with this command. After they finish setup, they invite everyone else.

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

Cognito emails a **temporary password** to `ADMIN_EMAIL`. Copy only the password — a period at the end of the sentence is punctuation, not part of it. This email is sent by Cognito, not by the app, so Step 7.4 is not required yet.

The message subject is **“Complete your admin account setup”**. It includes a link. If the hosted console is already live, that link looks like:

```text
https://production.<amplify-default-domain>/invite?setup=admin&email=<ADMIN_EMAIL>
```

If you are running locally (Path B) and the hosted console is not ready, use this instead:

```text
http://127.0.0.1:5174/invite?setup=admin&email=<ADMIN_EMAIL>
```

On that screen, enter the temporary password, name, email, and a new password. After that, `/home` opens. If they go to `/login` with the temporary password, they are sent to the same setup screen.

### Step 7.4 — Whitelist outbound recipients (SES sandbox)

New AWS accounts cannot email the whole internet. Until you leave the [SES sandbox](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html), the app can send **from** the address you verified in 7.2, but only **to** addresses you confirm here.

That is true for any inbox — work, personal, Gmail — not only addresses on your app domain. Confirm every person you will invite while testing, including `ADMIN_EMAIL` if that person should also receive an invite from the app:

```bash
RECIPIENT=teammate@example.com   # repeat for each address, including ADMIN_EMAIL if needed

aws ses verify-email-identity \
  --email-address "$RECIPIENT" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Each person clicks the confirmation link AWS sends them. Then check:

```bash
aws ses get-identity-verification-attributes \
  --identities "$RECIPIENT" \
  --profile "$AWS_PROFILE" --region "$AWS_REGION"
```

Repeat for each test address, or add them in **AWS Console → SES → Identities → Create identity → Email address**.

You do **not** need this for real customers yet. When you are ready to invite anyone without confirming each inbox first, request [SES production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html). Skip that for local Path B.

### Step 7.5 — Generate local developer config bundle

The app on a laptop needs connection details (database tables, login pool, From address, and so on). This command writes those files from Parameter Store so nobody has to copy values by hand.

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


If you change infrastructure later (new tables, a different From address, and so on), run this again and send developers the new folder. Existing `SECRET_KEY` / `CSRF_SESSION_KEY` in the output are kept unless you pass `--no-preserve-secrets`.

---



### Path B — Local development (default — no CI/CD)

This is the usual way to try the product. The API and the web console run on your machine. They still talk to the Cognito, database, files, and email you created in AWS. You do not set up GitHub Actions or wait for a cloud deploy. Use the files from Step 7.5.

#### Step 7.6 — Hand off config to developers

Send `bootstrap/output/${ENV}/local-dev/` (zip or shared drive). Developers copy the three files as described in `local-dev/README.md`.

#### Step 7.7 — Run local API and console

```bash
# Developer machine — after copying files from local-dev/
cd dev/renglo-api && ./run.sh          # terminal 1 — http://127.0.0.1:5001

cd console && npm run dev              # terminal 2 — http://127.0.0.1:5174
```

`run.sh` uses an AWS profile; it must be able to reach the same account and region you used for bootstrap. Open `http://127.0.0.1:5174/login`. If Cognito still has the temporary password, it will ask you to set a new one (or send you to the admin setup link from Step 7.3).

#### Step 7.8 — Test team invites (local)

1. Log in at `http://127.0.0.1:5174/login`.
2. Invite a teammate whose inbox you confirmed in Step 7.4. If you skip that, AWS will refuse to deliver the invite.
3. The email contains a link to the local console (`http://127.0.0.1:5174` by default). The teammate opens it on a machine that is also running the local console, or pastes the invite code from the email at `/invite`.

**You are done for local testing.** Stop here unless you need a hosted production API.

---



### Path A — Cloud go-live (optional, later)

Skip this while you are developing locally. Use it when you want the API and the web console hosted in AWS instead of on a laptop. That needs GitHub Actions in your releases repo — see **[§8](#8-cicd-contract-optional--cloud-production-only)**.

#### Step 7.9 — Deploy application code (GitHub Actions)

Stack-b starts the backend on a **placeholder** image so the stack can finish. GitHub Actions must build and deploy the real backend (and handlers). Details are in §8.

1. Configure and run the releases workflow for **production** (and **staging** if you enabled it).
2. If handlers have a separate workflow, run that too.

If you redeploy `<env>-stack-b` later, the backend goes back to the placeholder. Run the releases pipeline again afterward.

#### Step 7.10 — Confirm the cloud API is live

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

#### Step 7.11 — Test team invites (cloud)

Log in at the hosted console URL (`FE_BASE_URL` from Parameter Store) and invite by email. Invite links in those emails point at the hosted console.

Until you leave the SES sandbox (Step 7.4), you can still only invite addresses you confirmed. When real users should be able to join without that extra step, request [SES production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html).

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
  → §6 write-state  →  §7.1 confirm SSM
  → §7.2 verify sender (FROM_EMAIL)
  → §7.3 admin-create-user
  → §7.4 whitelist outbound recipients (SES sandbox)
  → §7.5 write-local-config  →  bootstrap/output/<env>/local-dev/
  → Path B (default): §7.6 handoff → §7.7 run local → §7.8 invites
       (stop here — no GitHub / no CI/CD)
  → Path A (optional later): §7.9–7.11 + §8 CI/CD when you want cloud production
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
    "HandlersNetworkMode=existing" \
    "ExistingVpcId=vpc-0123456789abcdef0" \
    "ExistingSubnetIds=subnet-aaa,subnet-bbb"
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

