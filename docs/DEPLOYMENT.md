# Deploying to AWS

Deployment has two parts: a one-time **bootstrap** you run from your machine,
then **continuous deployment** from GitHub Actions on every push to `main`.

## Prerequisites

- An AWS account and an admin profile in the AWS CLI (`aws sts get-caller-identity` works).
- Terraform ≥ 1.10, Python 3.12+, `make`, `zip`.
- The repository pushed to GitHub.

## 1. Bootstrap (once per account)

Creates the Terraform state bucket, the GitHub OIDC identity provider, and the
`aggtool-github-deploy` role that only this repository's `dev` environment can
assume.

```bash
cd infra/bootstrap
terraform init
terraform apply -var="github_repository=MeridianIDR-Devs/aggtool"
# If the account already has the GitHub OIDC provider, add -var="create_oidc_provider=false".
terraform output   # note state_bucket and deploy_role_arn
```

The bootstrap state is local (`infra/bootstrap/terraform.tfstate`). Keep it, or
re-import the resources if you lose it.

## 2. Configure GitHub

In the repository: **Settings → Environments → New environment → `dev`**
(optionally add yourself as a required reviewer for deploy approvals).

Then **Settings → Secrets and variables → Actions → Variables**:

| Variable | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | `deploy_role_arn` output |
| `TF_STATE_BUCKET` | `state_bucket` output |
| `AWS_REGION` | `us-east-1` (or your region) |
| `ALARM_EMAIL` | where alarm emails go (optional) |

No secrets are needed: GitHub Actions gets short-lived AWS credentials via OIDC.

## 3. Deploy

Push to `main` or run the **Deploy** workflow manually. It builds the arm64
Lambda bundle, runs `terraform apply`, invalidates CloudFront and smoke-tests
the API. The run summary shows the web URL.

To deploy from your machine instead:

```bash
make bundle
cd infra/terraform
cp backend.hcl.example backend.hcl        # fill in the state bucket
cp terraform.tfvars.example terraform.tfvars
terraform init -backend-config=backend.hcl
terraform apply
```

If you set `alarm_email`, confirm the SNS subscription email AWS sends.

## 4. Create users

Sign-up is invite-only:

```bash
aws cognito-idp admin-create-user \
  --user-pool-id "$(terraform -chdir=infra/terraform output -raw user_pool_id)" \
  --username you@example.com \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
```

Cognito emails a temporary password; the first sign-in asks for a new one.

## Operating

- **Dashboard**: `terraform output dashboard_url`.
- **Alarms** (SNS email): DLQ not empty, Lambda errors, API 5xx.
- **Logs**: structured JSON in CloudWatch, `/aws/lambda/aggtool-dev-{api,worker}`;
  filter by `job_id`.
- **Failed jobs**: input problems show to the user as a `FAILED` job with a
  reason. Crashes retry three times, then the job is marked failed and the
  message lands in the DLQ for inspection.

## Tear down

```bash
cd infra/terraform && terraform destroy
cd ../bootstrap && terraform destroy   # empty the state bucket first
```

The KMS key enters a 7-day deletion window, during which it still costs about
$1 pro rata.

## Hardening for production

- Replace `AdministratorAccess` on the deploy role with an explicit policy.
- Add a custom domain with an ACM certificate (TLS 1.2+ minimum) and AWS WAF
  on CloudFront.
- Turn on S3 server access logs, CloudTrail S3 data events and GuardDuty.
- Require MFA in Cognito, or federate to the company identity provider.
- Add a `prod` environment (separate account) with required reviewers.
