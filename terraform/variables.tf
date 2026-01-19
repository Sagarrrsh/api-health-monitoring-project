variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name"
  type        = string
  default     = "health-monitor"
}

variable "db_username" {
  description = "Database master username"
  type        = string
  default     = "postgres"
}

variable "db_password" {
  description = "Database password"
  type        = string
  sensitive   = true
}

variable "worker_min_count" {
  description = "Minimum number of worker tasks"
  type        = number
  default     = 1
}

variable "worker_max_count" {
  description = "Maximum number of worker tasks"
  type        = number
  default     = 5
}