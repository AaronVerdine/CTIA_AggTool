variable "project" {
  description = "Name prefix for all resources."
  type        = string
  default     = "aggtool"
}

variable "environment" {
  description = "Deployment environment name (dev, prod, ...)."
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "lambda_zip_path" {
  description = "Path to the Lambda bundle built by `make bundle`."
  type        = string
  default     = "../../build/lambda.zip"
}

variable "alarm_email" {
  description = "Email address for operational alarms. Empty disables the subscription."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "owner/name of the GitHub repository (used for tagging)."
  type        = string
  default     = ""
}

variable "upload_retention_days" {
  description = "Days before uploaded input files are deleted."
  type        = number
  default     = 7
}

variable "result_retention_days" {
  description = "Days before results and job records are deleted."
  type        = number
  default     = 30
}

variable "max_upload_mb" {
  description = "Maximum size of each uploaded file."
  type        = number
  default     = 25
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 30
}

variable "extra_web_origins" {
  description = "Additional origins allowed to use the API and sign in, e.g. http://localhost:8000 for local UI development."
  type        = list(string)
  default     = []
}
