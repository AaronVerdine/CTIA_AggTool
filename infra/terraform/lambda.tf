locals {
  worker_timeout = 120
  common_env = {
    AGGTOOL_MODE                 = "aws"
    AGGTOOL_DATA_BUCKET          = aws_s3_bucket.data.bucket
    AGGTOOL_JOBS_TABLE           = aws_dynamodb_table.jobs.name
    AGGTOOL_QUEUE_URL            = aws_sqs_queue.jobs.url
    AGGTOOL_MAX_UPLOAD_BYTES     = tostring(var.max_upload_mb * 1024 * 1024)
    AGGTOOL_JOB_RETENTION_DAYS   = tostring(var.result_retention_days)
    POWERTOOLS_SERVICE_NAME      = var.project
    POWERTOOLS_METRICS_NAMESPACE = "AggTool"
    POWERTOOLS_LOG_LEVEL         = "INFO"
  }
  lambda_source_hash = filebase64sha256(var.lambda_zip_path)
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ------------------------------------------------------------------ API role
resource "aws_iam_role" "api" {
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "api" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
  statement {
    sid       = "Jobs"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.jobs.arn, "${aws_dynamodb_table.jobs.arn}/index/owner-index"]
  }
  statement {
    # Presigned POST uploads and HEAD checks on uploads/; presigned GET on results/.
    sid       = "Uploads"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/uploads/*"]
  }
  statement {
    sid       = "Results"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/results/*"]
  }
  statement {
    # Lets HEAD return 404 rather than 403 for not-yet-uploaded files.
    sid       = "ListForHead"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["uploads/*"]
    }
  }
  statement {
    sid       = "Queue"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.jobs.arn]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "api" {
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

# --------------------------------------------------------------- worker role
resource "aws_iam_role" "worker" {
  name               = "${local.name}-worker"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }
  statement {
    sid       = "Tracing"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
  statement {
    sid       = "Jobs"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.jobs.arn]
  }
  statement {
    sid       = "ReadUploads"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/uploads/*"]
  }
  statement {
    sid       = "WriteResults"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/results/*"]
  }
  statement {
    sid       = "Queue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.jobs.arn]
  }
  statement {
    sid       = "Kms"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "worker" {
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

# ------------------------------------------------------------------- logs
resource "aws_cloudwatch_log_group" "api" {
  #checkov:skip=CKV_AWS_338:Retention is configurable (log_retention_days); 30 days keeps demo costs low.
  name              = "/aws/lambda/${local.name}-api"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

resource "aws_cloudwatch_log_group" "worker" {
  #checkov:skip=CKV_AWS_338:Retention is configurable (log_retention_days); 30 days keeps demo costs low.
  name              = "/aws/lambda/${local.name}-worker"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

# --------------------------------------------------------------- functions
resource "aws_lambda_function" "api" {
  #checkov:skip=CKV_AWS_117:No VPC resources are used; all dependencies are regional AWS APIs.
  #checkov:skip=CKV_AWS_116:Synchronous API function; failures return HTTP errors, no DLQ applies.
  #checkov:skip=CKV_AWS_115:Reserved concurrency is not set so new accounts with low limits can deploy; API Gateway throttling bounds load.
  #checkov:skip=CKV_AWS_272:Code signing omitted for the demo; artifacts are built and deployed only by CI.
  function_name    = "${local.name}-api"
  role             = aws_iam_role.api.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "aggtool.handlers.api_handler"
  filename         = var.lambda_zip_path
  source_code_hash = local.lambda_source_hash
  memory_size      = 512
  timeout          = 15
  kms_key_arn      = aws_kms_key.main.arn

  environment {
    variables = merge(local.common_env, {
      AGGTOOL_CORS_ORIGINS = join(",", local.origins)
    })
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

resource "aws_lambda_function" "worker" {
  #checkov:skip=CKV_AWS_117:No VPC resources are used; all dependencies are regional AWS APIs.
  #checkov:skip=CKV_AWS_116:Failures are redriven by the SQS source queue to its DLQ.
  #checkov:skip=CKV_AWS_115:Concurrency is bounded by the event source mapping's maximum_concurrency.
  #checkov:skip=CKV_AWS_272:Code signing omitted for the demo; artifacts are built and deployed only by CI.
  function_name    = "${local.name}-worker"
  role             = aws_iam_role.worker.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "aggtool.handlers.worker_handler"
  filename         = var.lambda_zip_path
  source_code_hash = local.lambda_source_hash
  memory_size      = 2048
  timeout          = local.worker_timeout
  kms_key_arn      = aws_kms_key.main.arn

  environment {
    variables = local.common_env
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_cloudwatch_log_group.worker, aws_iam_role_policy.worker]
}

resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn        = aws_sqs_queue.jobs.arn
  function_name           = aws_lambda_function.worker.arn
  batch_size              = 1
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 5
  }
}
