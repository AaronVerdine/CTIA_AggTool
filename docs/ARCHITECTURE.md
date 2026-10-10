# AggTool architecture

## 1. Product: from script to service

The prototype answered one question: *"merge these two reports"*. The service
answers the question the business actually has:

> **Which short codes are live on the carrier, who aggregates them, who provides
> the content, and who holds the lease, and where do the carrier's records and
> the CSCA registry disagree?**

| Capability | Why it matters to an organisation |
|---|---|
| Correct, rule-driven combined list (the README spec) | Replaces a manual spreadsheet task; repeatable and auditable. |
| **Reconciliation report**: carrier codes with no CSCA lease, registry codes not on the carrier, conflicting registry rows | Surfaces expired or unleased codes still provisioned. This is a compliance and revenue-leak finding, not just a merge. |
| **Audit trail**: every dropped row with the rule that dropped it | "Why is code 12345 missing?" has an answer without re-running anything. |
| **Configurable profiles** (YAML): column aliases, join keys, filters, output layout | New report layouts or new carriers become config changes, not code changes. |
| CSV **and** Excel in and out, formula-injection safe | Works with the files people actually have; outputs are safe to open. |
| Web UI + REST API + CLI | Analysts use the UI; other systems call the API; engineers script the CLI. |
| Job history per user | Past runs and their outputs can be re-downloaded. |

## 2. AWS architecture (serverless)

```
                     ┌──────────────┐
 Browser ──HTTPS──▶  │  CloudFront  │──OAC──▶ S3 (static web UI)
    │                └──────────────┘
    │  sign-in (OAuth2 code + PKCE)
    ├────────────▶ Cognito User Pool (hosted UI)
    │
    │  JWT                      ┌────────────────────┐
    ├──HTTPS──▶ API Gateway ───▶│ API Lambda          │──▶ DynamoDB (jobs)
    │          (HTTP API,       │ FastAPI + Mangum    │
    │           JWT authorizer, └─────────┬──────────┘
    │           throttling)               │ presigned POST / GET
    │                                     ▼
    └──presigned upload/download──▶ S3 data bucket (SSE-KMS)
                                          ▲      uploads/ (expire 7 d)
                       submit ─▶ SQS ─▶ Worker Lambda  results/ (expire 30 d)
                                 │      (pandas engine)
                                 └─▶ DLQ ─▶ CloudWatch alarm ─▶ SNS email
```

**Request flow**

1. `POST /jobs` creates a job (status `PENDING_UPLOAD`) and returns two presigned
   S3 POST forms (carrier report, registry report) limited by size and key.
2. The browser uploads both files **directly to S3**. File bytes never pass
   through API Gateway or the API Lambda.
3. `POST /jobs/{id}/submit` verifies both objects exist, sets `QUEUED`, sends an
   SQS message.
4. The worker Lambda runs the engine, writes `combined.xlsx`, `combined.csv` and
   `report.json` to `results/{job}/`, and sets `SUCCEEDED` with summary counts
   (or `FAILED` with a user-readable error). Three failed attempts go to the DLQ.
5. `GET /jobs/{id}` polls status; `GET /jobs/{id}/download?format=xlsx` returns
   a short-lived presigned GET URL.

**Why serverless rather than containers on ECS/EKS**

- Workload is bursty and small (a few files a day): Lambda + DynamoDB on-demand
  costs a few dollars a month at demo scale, versus ~$50+/month idle for Fargate
  plus an ALB and NAT gateway.
- No servers, patching or VPC to manage; scaling and HA are built in.
- The engine is a plain Python package, so moving the worker to Fargate later
  (for very large files beyond Lambda's 15-minute / 10 GB limits) is a packaging
  change, not a rewrite. That trade-off is documented rather than pre-built.

## 3. Security design

**Data classification decision.** The README's data (short codes, aggregator,
content-provider and lease-holder company names) is *business-confidential*,
not regulated: no PII, PHI, cardholder or CUI data. I recommend **not** taking
on a regulated-data scope (HIPAA, PCI DSS, FedRAMP). It would add cost
(dedicated logging accounts, WAF, Macie, GuardDuty, compliance reporting) and
paperwork with no matching data to protect. Instead the design applies the
controls a regulated workload would need, so the gap to a compliance programme
is process, not architecture:

| Control | Implementation |
|---|---|
| Encryption at rest | Customer-managed KMS key for S3, DynamoDB, SQS, CloudWatch Logs; key rotation on. |
| Encryption in transit | TLS everywhere; S3 bucket policy denies non-TLS requests. |
| Authentication | Cognito user pool, admin-created users only (no self sign-up), strong password policy, optional MFA. |
| Authorisation | API Gateway JWT authorizer; every job is scoped to the caller's `sub`, so users can only see their own jobs. |
| Least privilege | Separate IAM role per Lambda, scoped to exact bucket prefixes, table and queue. |
| Data minimisation | Uploads expire after 7 days, results after 30; DynamoDB TTL on job records. |
| Input safety | Size limits enforced by S3 presigned POST policy; extension and content checks; spreadsheet formula-injection neutralised in outputs. |
| Public exposure | S3 Block Public Access on all buckets; the web bucket is reachable only via CloudFront OAC. |
| CI/CD credentials | GitHub Actions assumes an AWS role via OIDC; no long-lived access keys. |
| Auditability | Structured JSON logs (no file contents logged), CloudTrail (account default), per-job drop report. |

## 4. Engineering practices

- **Infrastructure as code**: Terraform, remote state in S3 with locking.
- **CI** (every PR): ruff, mypy, pytest with coverage gate, `terraform fmt` /
  `validate`, Checkov IaC scan.
- **CD** (on `main`, manual approval environment): build the Lambda bundle for
  arm64, `terraform apply`, publish the web UI, smoke test `/health`.
- **Observability**: AWS Lambda Powertools logging, metrics (jobs succeeded /
  failed, rows processed) and X-Ray tracing; CloudWatch alarms on DLQ depth,
  Lambda errors and API 5xx, delivered by SNS email.
- **Local development**: the same API and engine run locally with a filesystem
  storage backend (`aggtool serve`), plus a CLI (`aggtool run`), so no AWS
  account is needed to develop or demo.

## 5. Estimated cost (demo scale, us-east-1)

| Item | Monthly |
|---|---|
| KMS customer-managed key | ~$1.00 |
| Lambda, API Gateway, SQS, DynamoDB, S3, CloudFront | < $1 (mostly free tier) |
| CloudWatch logs and alarms | < $1 |
| **Total** | **≈ $1–3** |
