# ---------------------------------------------------------------- data bucket
resource "aws_s3_bucket" "data" {
  #checkov:skip=CKV_AWS_18:Access logging omitted for demo cost; enable CloudTrail S3 data events per account if needed.
  #checkov:skip=CKV_AWS_144:Cross-region replication not needed for short-lived job files.
  #checkov:skip=CKV2_AWS_62:No event notifications; jobs are queued by the API.
  bucket_prefix = "${local.name}-data-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "expire-uploads"
    status = "Enabled"
    filter {
      prefix = "uploads/"
    }
    expiration {
      days = var.upload_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "expire-results"
    status = "Enabled"
    filter {
      prefix = "results/"
    }
    expiration {
      days = var.result_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_cors_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  cors_rule {
    allowed_methods = ["POST", "GET"]
    allowed_origins = local.origins
    allowed_headers = ["*"]
    max_age_seconds = 3600
  }
}

data "aws_iam_policy_document" "data_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.data.arn,
      "${aws_s3_bucket.data.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket     = aws_s3_bucket.data.id
  policy     = data.aws_iam_policy_document.data_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.data]
}

# ------------------------------------------------------------------ jobs table
resource "aws_dynamodb_table" "jobs" {
  name         = "${local.name}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }
  attribute {
    name = "owner"
    type = "S"
  }
  attribute {
    name = "created_at"
    type = "S"
  }

  global_secondary_index {
    name            = "owner-index"
    hash_key        = "owner"
    range_key       = "created_at"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.main.arn
  }

  deletion_protection_enabled = var.environment == "prod"
}

# ---------------------------------------------------------------------- queue
resource "aws_sqs_queue" "dlq" {
  name                              = "${local.name}-jobs-dlq"
  message_retention_seconds         = 1209600
  kms_master_key_id                 = aws_kms_key.main.id
  kms_data_key_reuse_period_seconds = 3600
}

resource "aws_sqs_queue" "jobs" {
  name                              = "${local.name}-jobs"
  visibility_timeout_seconds        = local.worker_timeout * 6
  message_retention_seconds         = 86400
  kms_master_key_id                 = aws_kms_key.main.id
  kms_data_key_reuse_period_seconds = 3600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    # Keep in sync with MAX_RECEIVES in src/aggtool/handlers.py.
    maxReceiveCount = 3
  })
}
