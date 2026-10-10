resource "aws_s3_bucket" "web" {
  #checkov:skip=CKV_AWS_18:Static site bucket; CloudFront is the only reader.
  #checkov:skip=CKV_AWS_144:Static assets are rebuilt from Git; replication not needed.
  #checkov:skip=CKV_AWS_145:SSE-S3 keeps CloudFront OAC simple for public UI assets.
  #checkov:skip=CKV2_AWS_62:No event notifications needed.
  #checkov:skip=CKV_AWS_21:Assets are versioned in Git.
  #checkov:skip=CKV2_AWS_61:Static assets are replaced on every deploy; nothing to expire.
  bucket_prefix = "${local.name}-web-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "web" {
  bucket                  = aws_s3_bucket.web.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "web" {
  bucket = aws_s3_bucket.web.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "web" {
  name                              = "${local.name}-web"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_response_headers_policy" "web" {
  name = "${local.name}-security-headers"

  security_headers_config {
    content_security_policy {
      override = true
      content_security_policy = join("; ", [
        "default-src 'self'",
        "connect-src 'self' https://*.amazonaws.com https://*.amazoncognito.com",
        "img-src 'self' data:",
        "style-src 'self'",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "form-action 'self'",
      ])
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
  }
}

resource "aws_cloudfront_distribution" "web" {
  #checkov:skip=CKV_AWS_68:WAF omitted for demo cost (~$5+/month); API Gateway throttling and Cognito auth protect the backend.
  #checkov:skip=CKV_AWS_86:Access logging omitted for demo cost.
  #checkov:skip=CKV_AWS_310:Single origin; failover not needed for a static UI.
  #checkov:skip=CKV_AWS_374:Geo restriction not required.
  #checkov:skip=CKV_AWS_174:Default CloudFront certificate enforces TLSv1 minimum; use a custom domain and ACM cert to raise it.
  #checkov:skip=CKV2_AWS_42:Custom domain/ACM certificate is optional for the demo.
  #checkov:skip=CKV2_AWS_47:WAF omitted, see CKV_AWS_68.
  enabled             = true
  comment             = "${local.name} web UI"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"

  origin {
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_id                = "web"
    origin_access_control_id = aws_cloudfront_origin_access_control.web.id
  }

  default_cache_behavior {
    target_origin_id           = "web"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.web.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "web_bucket" {
  statement {
    sid       = "CloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.web.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.web.arn, "${aws_s3_bucket.web.arn}/*"]
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

resource "aws_s3_bucket_policy" "web" {
  bucket     = aws_s3_bucket.web.id
  policy     = data.aws_iam_policy_document.web_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.web]
}

# ------------------------------------------------------------- site content
locals {
  web_dir = "${path.module}/../../web"
  mime = {
    html = "text/html; charset=utf-8"
    js   = "text/javascript; charset=utf-8"
    css  = "text/css; charset=utf-8"
    svg  = "image/svg+xml"
    ico  = "image/x-icon"
  }
  web_files = [for f in fileset(local.web_dir, "**") : f if f != "config.js"]
}

resource "aws_s3_object" "web" {
  for_each      = toset(local.web_files)
  bucket        = aws_s3_bucket.web.id
  key           = each.value
  source        = "${local.web_dir}/${each.value}"
  etag          = filemd5("${local.web_dir}/${each.value}")
  content_type  = lookup(local.mime, reverse(split(".", each.value))[0], "application/octet-stream")
  cache_control = each.value == "index.html" ? "no-cache" : "public, max-age=300"
}

resource "aws_s3_object" "web_config" {
  bucket        = aws_s3_bucket.web.id
  key           = "config.js"
  content_type  = local.mime.js
  cache_control = "no-cache"
  content = templatefile("${path.module}/templates/config.js.tftpl", {
    api_base    = aws_apigatewayv2_api.http.api_endpoint
    auth_domain = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
    client_id   = aws_cognito_user_pool_client.web.id
    environment = var.environment
  })
}
