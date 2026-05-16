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

Also, a DevOps profile must be configured in the AWS CLI, the required policy can be obtained with bootstrap/helpers/generate_env_deployment_tt_policy.py

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

**Non-default VPC:** run extensions standalone with `--vpc vpc-...` — see [extensions-service/README.md](../extensions-service/README.md).

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

## More detail

- Full layout, JSON shapes, and design: [ARCHITECTURE.md](ARCHITECTURE.md)
- Launcher-only deploy: [launcher/ENVIRONMENT_README.md](../launcher/ENVIRONMENT_README.md)
- Extensions-only: [extensions-service/README.md](../extensions-service/README.md)

---

## Shell scripts on Windows / WSL

If `bash bootstrap/setup-venvs.sh` fails with errors like `$'\r': command not found`, or `set: pipefail` / `invalid option name`, the script probably has **Windows (CRLF) line endings**—common when the workspace is on Windows or synced (for example OneDrive). From **WSL** or Linux, strip carriage returns and run the script again:

```bash
sed -i 's/\r$//' bootstrap/setup-venvs.sh
bash bootstrap/setup-venvs.sh
```

If you have `dos2unix` installed: `dos2unix bootstrap/setup-venvs.sh`. In **Cursor/VS Code**, you can also switch the file’s line ending from **CRLF** to **LF** in the status bar, then save.
