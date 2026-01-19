resource "aws_sqs_queue" "health_checks" {
  name                       = "${var.project_name}-queue"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20

  tags = {
    Name = "${var.project_name}-queue"
  }
}


