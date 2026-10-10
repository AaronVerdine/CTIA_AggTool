data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  web_origin = "https://${aws_cloudfront_distribution.web.domain_name}"
  origins    = concat([local.web_origin], var.extra_web_origins)
}
