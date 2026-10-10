output "web_url" {
  description = "The web UI."
  value       = "${local.web_origin}/"
}

output "api_url" {
  description = "Base URL of the REST API."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "user_pool_id" {
  description = "Cognito user pool (create users here)."
  value       = aws_cognito_user_pool.main.id
}

output "web_bucket" {
  value = aws_s3_bucket.web.bucket
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.web.id
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${local.name}"
}
