output "api_url" {
  description = "API endpoint URL"
  value       = "http://${aws_lb.main.dns_name}"
}

output "rds_endpoint" {
  description = "RDS endpoint (host:port)"
  value       = aws_db_instance.postgres.endpoint
  sensitive   = true
}

output "rds_address" {
  description = "RDS hostname only"
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "database_url" {
  description = "Complete DATABASE_URL for manual tasks"
  value       = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.endpoint}/healthmonitor"
  sensitive   = true
}

output "sqs_queue_url" {
  description = "SQS queue URL"
  value       = aws_sqs_queue.health_checks.url
}

output "ecr_repositories" {
  description = "ECR repository URLs"
  value = {
    api       = aws_ecr_repository.api.repository_url
    scheduler = aws_ecr_repository.scheduler.repository_url
    worker    = aws_ecr_repository.worker.repository_url
    db_init   = aws_ecr_repository.db_init.repository_url
  }
}

output "ecs_cluster_name" {
  description = "ECS cluster name for running db-init"
  value       = aws_ecs_cluster.main.name
}

output "public_subnet_ids" {
  description = "Public subnet IDs for db-init run-task"
  value       = [aws_subnet.public_1.id, aws_subnet.public_2.id]
}

output "ecs_security_group_id" {
  description = "ECS security group ID for db-init run-task"
  value       = aws_security_group.ecs.id
}
