# Bootstrap — new environment (quick start)

From the **`infra-installer/`** repo root. Replace placeholders: `<extension>`, `<aws-profile>`, `Org/repo`, and region if not `us-east-1`.

---

## 1. Prepare infra-launcher folder

Local machine must have:
- AWS CLI
- Docker
- Python 3.12
- Git
- GitHub API (Optional - only renglo DevOps)
- WSL (only for windows)

Also, configure a DevOps-capable AWS CLI profile.

- **Minimal IAM policy for scripts that provision an environment:** run  
  `python bootstrap/helpers/generate_env_deployment_tt_policy.py <env> --aws-profile <profile>`  
  (writes `bootstrap/state/<env>/<env>_deployment_tt_policy.json` by default.)

Clone **bootstrap** into a <infra-launcher> folder, alongside extensions-service and **launcher**
```bash
cd <infra-launcher>
git clone https://github.com/renglo/bootstrap.git
git clone https://github.com/renglo/launcher.git
git clone https://github.com/renglo/extensions-service.git
```

---

## 2. Create virtualenvs

**Idempotent:** safe to run anytime, but typically only needed once. Uses **Python 3.12** by default; if the venv already exists, requirements are re-installed / upgraded.

```bash
bash bootstrap/setup-venvs.sh
```

---

## 3. Provision everything (launcher + extensions + merge)

`--github-repo` is the GitHub org/repo that **controls releases** (OIDC trust for deploying this environment). Placeholder: `Org/repo`.

### Lambda-only (default)

Provisions launcher core infra + Lambda IAM role/policy. No ECS cluster, ECR, or S3 results bucket.

```bash
python bootstrap/install.py <extension> \
  --profile <aws-profile> \
  --aws-region us-east-1 \
  --github-repo Org/repo
```

### Lambda + ECS

Add `--launch-type` to also provision ECS cluster, ECR repo, and S3 results bucket. Uses **default VPC** for subnet/SG discovery (no `--vpc` needed for most cases).

```bash
python bootstrap/install.py <extension> \
  --profile <aws-profile> \
  --aws-region us-east-1 \
  --github-repo Org/repo \
  --launch-type ec2
```

Both modes are **idempotent**: re-running updates IAM and (if `--launch-type` is given) ECS infra without destroying existing resources. Running without `--launch-type` on an environment that already has ECS preserves the ECS manifest sections.

**Optional:**

| Flag | When to use |
|------|-------------|
| `--launch-type fargate` | ECS on Fargate instead of EC2 |
| `--handlers-github-repo Org/other-repo` | Handlers CI repo ≠ release control repo (default: same as `--github-repo`) |
| `--handlers-enable-staging-role` | Second IAM OIDC role for GitHub Environment `staging` |
| `--skip-launcher` | Only (re)run extensions + merge |
| `--skip-extensions` | Only launcher + merge |
| `--merge-only` | No AWS changes; re-merge existing `launcher/state` + `extensions-service/state` into `bootstrap/state` |
| `--tenant <tenant_name>` | Prefix `ENVIRONMENT` in `platform_vars.*` only (`tenant_name_production`, `tenant_name_staging`) |

**Non-default VPC:** run extensions standalone with `--vpc vpc-...` 

---

## 4. Uninstall (tear down AWS + local merged state)

Runs extensions teardown, then launcher teardown, then deletes **`bootstrap/state/<extension>/`**.

```bash
python bootstrap/uninstall.py <extension> \
  --profile <aws-profile> \
  --yes
```

**Optional:**

| Flag | When to use |
|------|-------------|
| `--skip-extensions` | Do not tear down extensions-service / handlers ECS |
| `--skip-launcher` | Do not tear down launcher core env |
| `--skip-tables` | Keep DynamoDB tables (launcher) |
| `--skip-cognito` | Keep Cognito user pool (launcher) |
| `--keep-logs` | Keep CloudWatch log groups (launcher + extensions) |

`--aws-region` defaults to `us-east-1`; uninstall also reads `bootstrap/state/<extension>/platform_resources.json` for region when present.

---

## Sync GitHub environment variables

After you have GitHub payloads (`launcher/state/<env>/production.json`, `bootstrap/state/<extension>/platform_vars.production.json` for the **releases** repo, or `bootstrap/state/<extension>/deploy_input.json` for the **handlers** repo), push variables and secrets to GitHub Environments:

```bash
python bootstrap/helpers/inject_github_env_vars.py --json path/to/environment.json
```

Use `--repo owner/repo` or `--environment <name>` if they are not set in or implied by the JSON. Requires authenticated **Github API** (`gh auth login`).

---

## Shell scripts on Windows / WSL

If `bash bootstrap/setup-venvs.sh` fails with errors like `$'\r': command not found`, or `set: pipefail` / `invalid option name`, the script probably has **Windows (CRLF) line endings**—common when the workspace is on Windows or synced (for example OneDrive). From **WSL** or Linux, strip carriage returns and run the script again:

```bash
sed -i 's/\r$//' <repo>/<file_name.sh>
```

or user for all sh files:
```bash
find . -name "*.sh" -exec sed -i 's/\r$//' {} \;
```

---

