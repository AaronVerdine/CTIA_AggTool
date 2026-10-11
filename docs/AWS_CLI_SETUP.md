# Setting up an admin AWS CLI profile

This uses **IAM Identity Center** (single sign-on), which AWS recommends. You
sign in through the browser and the CLI gets short-lived credentials, so no
access keys are ever stored on your machine. AWS console labels change from
time to time; if a button is named slightly differently, look for the closest
match.

## 1. Secure the root user

1. Sign in to <https://console.aws.amazon.com> with the email you used to create the account (the *root user*).
2. Top-right account menu → **Security credentials** → **Assign MFA device**. Use an authenticator app.
3. From now on, use the root user only for billing and account settings.

## 2. Create your admin user in IAM Identity Center

1. Set the region selector (top right) to **US East (N. Virginia) us-east-1**.
2. Search for **IAM Identity Center** → **Enable**. Accept the prompt to create an organization instance (free).
3. **Users** → **Add user**. Use your email and choose "Send an email to this user with password setup instructions".
4. Open that email, accept the invitation, set a password and register MFA.
5. **Permission sets** → **Create permission set** → **Predefined permission set** → **AdministratorAccess**. Set the session duration to 4 hours.
6. **AWS accounts** → tick your account → **Assign users or groups** → choose your user → choose **AdministratorAccess** → **Submit**.
7. **Dashboard** (or **Settings**): copy the **AWS access portal URL**. It looks like `https://d-xxxxxxxxxx.awsapps.com/start`.

## 3. Install the AWS CLI v2

- **Windows**: download and run <https://awscli.amazonaws.com/AWSCLIV2.msi>
- **macOS**: download and run <https://awscli.amazonaws.com/AWSCLIV2.pkg>
- **Linux**: see <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>

Check it with `aws --version` (it should print `aws-cli/2.x`).

## 4. Create the CLI profile

```bash
aws configure sso
```

Answer the prompts:

| Prompt | Answer |
|---|---|
| SSO session name | `aggtool` |
| SSO start URL | your access portal URL from step 2.7 |
| SSO region | `us-east-1` |
| SSO registration scopes | press Enter |

A browser window opens. Approve the request, then back in the terminal:

| Prompt | Answer |
|---|---|
| Account / role | your account, `AdministratorAccess` |
| Default client Region | `us-east-1` |
| CLI default output format | `json` |
| Profile name | `aggtool-admin` |

## 5. Verify and use it

```bash
aws sts get-caller-identity --profile aggtool-admin
```

You should see your 12-digit account ID and an ARN containing
`AWSReservedSSO_AdministratorAccess`.

Make it the default for your terminal so Terraform picks it up:

```bash
export AWS_PROFILE=aggtool-admin          # macOS / Linux
$env:AWS_PROFILE = "aggtool-admin"        # Windows PowerShell
```

When the session expires (after the duration you chose), sign in again:

```bash
aws sso login --profile aggtool-admin
```

You're now ready for [DEPLOYMENT.md](DEPLOYMENT.md) step 1.

## Alternative: IAM user with access keys

Simpler, but it leaves a long-lived secret on your machine, which is a weaker
security story for a portfolio project. If you use it anyway: IAM → Users →
Create user → attach `AdministratorAccess` → Security credentials → Create
access key (CLI) → `aws configure --profile aggtool-admin`. Enable MFA on the
user and delete the key when you're done.
